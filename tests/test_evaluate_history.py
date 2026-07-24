"""Tests for scripts/evaluate.py's versioned evaluation history store (U2)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

from evaluate import EvalResult, append_history, prune_history, read_history


def _result(score=0.8, evaluator_name="dummy", passed=True, feedback="ok"):
    return EvalResult(score=score, feedback=feedback, passed=passed, evaluator_name=evaluator_name)


@pytest.fixture
def history_path(tmp_path):
    return str(tmp_path / "eval_history.jsonl")


def test_append_then_read_preserves_both_entries_in_order(history_path):
    append_history("skill:foo", _result(score=0.5), session_ids=["s1"], path=history_path)
    append_history("skill:foo", _result(score=0.9), session_ids=["s2"], path=history_path)

    entries = read_history("skill:foo", path=history_path)
    assert len(entries) == 2
    assert entries[0]["score"] == 0.5
    assert entries[1]["score"] == 0.9


def test_read_history_for_unknown_target_returns_empty_list(history_path):
    assert read_history("skill:never-evaluated", path=history_path) == []


def test_read_history_when_file_does_not_exist_returns_empty_list(tmp_path):
    missing = str(tmp_path / "does_not_exist.jsonl")
    assert read_history("skill:foo", path=missing) == []


def test_append_from_two_separate_calls_both_land(history_path):
    # Simulates two separate process runs both appending (not truncating).
    append_history("skill:foo", _result(score=0.1), path=history_path)
    append_history("skill:foo", _result(score=0.2), path=history_path)
    append_history("skill:bar", _result(score=0.3), path=history_path)

    foo_entries = read_history("skill:foo", path=history_path)
    bar_entries = read_history("skill:bar", path=history_path)
    assert len(foo_entries) == 2
    assert len(bar_entries) == 1


def test_prune_by_count_keeps_most_recent_n(history_path):
    for score in [0.1, 0.2, 0.3, 0.4, 0.5]:
        append_history("skill:foo", _result(score=score), path=history_path)

    prune_history(path=history_path, retention="2")

    entries = read_history("skill:foo", path=history_path)
    assert [e["score"] for e in entries] == [0.4, 0.5]


def test_prune_never_drops_most_recent_entry_even_with_limit_zero(history_path):
    append_history("skill:foo", _result(score=0.1), path=history_path)
    append_history("skill:foo", _result(score=0.9), path=history_path)

    prune_history(path=history_path, retention="0")

    entries = read_history("skill:foo", path=history_path)
    assert len(entries) == 1
    assert entries[0]["score"] == 0.9


def test_prune_with_unset_retention_is_unbounded(history_path):
    for score in [0.1, 0.2, 0.3]:
        append_history("skill:foo", _result(score=score), path=history_path)

    prune_history(path=history_path, retention=None)

    entries = read_history("skill:foo", path=history_path)
    assert len(entries) == 3


def test_prune_preserves_other_targets_independently(history_path):
    for score in [0.1, 0.2, 0.3]:
        append_history("skill:foo", _result(score=score), path=history_path)
    append_history("skill:bar", _result(score=0.7), path=history_path)

    prune_history(path=history_path, retention="1")

    assert [e["score"] for e in read_history("skill:foo", path=history_path)] == [0.3]
    assert [e["score"] for e in read_history("skill:bar", path=history_path)] == [0.7]
