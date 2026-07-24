"""Tests for scripts/evaluate.py's core Evaluator interface and registry (U1)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import EvalResult, Evaluator, get_enabled_evaluators, register_evaluator


def _make_dummy(evaluator_name):
    """Build a dummy Evaluator subclass whose `name` matches its registry key."""
    return type(
        f"Dummy_{evaluator_name}",
        (Evaluator,),
        {
            "name": evaluator_name,
            "evaluate": lambda self, content, context=None: EvalResult(
                score=1.0, feedback="ok", passed=True, evaluator_name=self.name
            ),
        },
    )


DummyEvaluator = _make_dummy("dummy")
OtherDummyEvaluator = _make_dummy("other_dummy")


@pytest.fixture
def isolated_registry(monkeypatch):
    """Give each test a clean registry stocked with three default-named dummies."""
    fake_registry = {
        "deterministic": _make_dummy("deterministic"),
        "llm_judge": _make_dummy("llm_judge"),
        "regression": _make_dummy("regression"),
        "dummy": DummyEvaluator,
        "other_dummy": OtherDummyEvaluator,
    }
    monkeypatch.setattr(evaluate, "REGISTRY", fake_registry)
    return fake_registry


def test_explicit_evaluators_returned_in_order(isolated_registry):
    evaluators = get_enabled_evaluators(env_value="dummy,other_dummy")
    assert [e.name for e in evaluators] == ["dummy", "other_dummy"]


def test_unset_env_var_falls_back_to_default(isolated_registry):
    evaluators = get_enabled_evaluators(env_value=None)
    assert [e.name for e in evaluators] == ["deterministic", "llm_judge", "regression"]


def test_unknown_evaluator_name_raises_clear_error(isolated_registry):
    with pytest.raises(ValueError, match="Unknown evaluator 'bogus'"):
        get_enabled_evaluators(env_value="dummy,bogus")


def test_empty_string_env_var_falls_back_to_default(isolated_registry):
    evaluators = get_enabled_evaluators(env_value="")
    assert [e.name for e in evaluators] == ["deterministic", "llm_judge", "regression"]


def test_whitespace_only_env_var_falls_back_to_default(isolated_registry):
    evaluators = get_enabled_evaluators(env_value="   ")
    assert [e.name for e in evaluators] == ["deterministic", "llm_judge", "regression"]


def test_eval_result_shape():
    result = EvalResult(score=0.5, feedback="partial", passed=False, evaluator_name="dummy")
    assert result.score == 0.5
    assert result.passed is False


def test_register_evaluator_adds_to_module_registry(monkeypatch):
    monkeypatch.setattr(evaluate, "REGISTRY", {})
    register_evaluator("dummy", DummyEvaluator)
    assert evaluate.REGISTRY["dummy"] is DummyEvaluator
