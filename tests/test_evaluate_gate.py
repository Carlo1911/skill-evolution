"""Tests for scripts/evaluate.py's run_evaluators/combine_gate/resolve_gate_strictness (U7 support)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import EvalResult, combine_gate, resolve_gate_strictness, run_evaluators


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("SKILL_EVOLUTION_GATE_STRICTNESS"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("SKILL_EVOLUTION_EVALUATORS", raising=False)


def _dummy(name, passed, score):
    return type(name, (evaluate.Evaluator,), {
        "name": name,
        "evaluate": lambda self, content, context=None: EvalResult(
            score=score, feedback=f"{name} says {passed}", passed=passed, evaluator_name=name,
        ),
    })


def test_resolve_gate_strictness_defaults_to_strict():
    assert resolve_gate_strictness("improve_existing") == "strict"


def test_resolve_gate_strictness_per_type_override(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_STRICTNESS_DEPRECATE_SKILL", "majority")
    assert resolve_gate_strictness("deprecate_skill") == "majority"
    assert resolve_gate_strictness("improve_existing") == "strict"


def test_combine_gate_strict_requires_all_pass():
    results = [
        EvalResult(score=1.0, passed=True, feedback="", evaluator_name="a"),
        EvalResult(score=0.2, passed=False, feedback="", evaluator_name="b"),
    ]
    assert combine_gate(results, "strict") is False


def test_combine_gate_strict_all_passing():
    results = [
        EvalResult(score=1.0, passed=True, feedback="", evaluator_name="a"),
        EvalResult(score=1.0, passed=True, feedback="", evaluator_name="b"),
    ]
    assert combine_gate(results, "strict") is True


def test_combine_gate_majority():
    results = [
        EvalResult(score=1.0, passed=True, feedback="", evaluator_name="a"),
        EvalResult(score=1.0, passed=True, feedback="", evaluator_name="b"),
        EvalResult(score=0.0, passed=False, feedback="", evaluator_name="c"),
    ]
    assert combine_gate(results, "majority") is True


def test_combine_gate_empty_results_never_blocks():
    assert combine_gate([], "strict") is True


def test_combine_gate_unknown_strictness_raises():
    with pytest.raises(ValueError, match="Unknown gate strictness"):
        combine_gate([EvalResult(score=1, passed=True, feedback="", evaluator_name="a")], "bogus")


def test_run_evaluators_treats_raising_evaluator_as_failed(monkeypatch):
    def _raising_evaluate(self, content, context=None):
        raise RuntimeError("boom")

    Raising = type("Raising", (evaluate.Evaluator,), {"name": "raising", "evaluate": _raising_evaluate})
    monkeypatch.setattr(evaluate, "REGISTRY", {"raising": Raising})
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "raising")

    results = run_evaluators("content", "skill:foo")
    assert len(results) == 1
    assert results[0].passed is False
    assert "fail-closed" in results[0].feedback.lower()


def test_run_evaluators_supplies_aggregate_new_score_to_regression(monkeypatch):
    captured_context = {}

    def _regression_evaluate(self, content, context=None):
        captured_context.update(context or {})
        return EvalResult(score=context["new_score"], passed=True, feedback="ok", evaluator_name="regression")

    Passing = _dummy("passing_one", True, 0.6)
    Regression = type("Regression", (evaluate.Evaluator,), {"name": "regression", "evaluate": _regression_evaluate})

    monkeypatch.setattr(evaluate, "REGISTRY", {"passing_one": Passing, "regression": Regression})
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "passing_one,regression")

    run_evaluators("content", "skill:foo")
    assert captured_context["new_score"] == pytest.approx(0.6)
    assert captured_context["target"] == "skill:foo"
