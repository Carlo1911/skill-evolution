"""Tests for scripts/evaluate.py's regression evaluator (U6)."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import EvalResult, RegressionEvaluator, append_history


@pytest.fixture
def history_file(tmp_path, monkeypatch):
    path = str(tmp_path / "eval_history.jsonl")
    monkeypatch.setattr(evaluate, "get_history_path", lambda: path)
    return path


def _seed(target, score):
    append_history(target, EvalResult(score=score, feedback="seed", passed=True, evaluator_name="dummy"))


def test_new_score_higher_than_previous_passes(history_file):
    _seed("skill:foo", 0.6)
    evaluator = RegressionEvaluator()
    result = evaluator.evaluate("content", context={"target": "skill:foo", "new_score": 0.8})
    assert result.passed is True


def test_no_prior_history_passes_with_no_baseline_message(history_file):
    evaluator = RegressionEvaluator()
    result = evaluator.evaluate("content", context={"target": "skill:never-seen", "new_score": 0.5})
    assert result.passed is True
    assert "no baseline" in result.feedback.lower()


def test_new_score_exactly_equal_to_previous_passes(history_file):
    _seed("skill:foo", 0.7)
    evaluator = RegressionEvaluator()
    result = evaluator.evaluate("content", context={"target": "skill:foo", "new_score": 0.7})
    assert result.passed is True


def test_new_score_lower_than_previous_fails(history_file):
    _seed("skill:foo", 0.9)
    evaluator = RegressionEvaluator()
    result = evaluator.evaluate("content", context={"target": "skill:foo", "new_score": 0.5})
    assert result.passed is False


def test_corrupted_history_entry_fails_closed(history_file):
    # A valid JSON line but missing the 'score' field entirely.
    with open(history_file, "w") as f:
        f.write(json.dumps({"target": "skill:foo", "feedback": "no score field here"}) + "\n")

    evaluator = RegressionEvaluator()
    result = evaluator.evaluate("content", context={"target": "skill:foo", "new_score": 0.8})
    assert result.passed is False
    assert "failed closed" in result.feedback.lower()


def test_missing_context_fails_closed(history_file):
    evaluator = RegressionEvaluator()
    result = evaluator.evaluate("content", context={})
    assert result.passed is False
