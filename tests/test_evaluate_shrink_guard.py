"""Tests for DeterministicEvaluator's shrink floor.

The growth cap protects against a skill ballooning. Nothing protected against the
opposite, and the LLM judge's `conciseness` criterion actively rewards deletion: a real
GEPA run on `test-driven-development` produced a candidate that cut the body from 10258B
to 3041B (-70.4%) and scored *higher* (0.85 -> 0.90). 17 of 29 headings were lost,
including "Red Flags — STOP and Start Over", "Common Rationalizations" and
"Testing Anti-Patterns" — precisely the guardrail sections.

A gate that permits that is not a safety gate.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import DeterministicEvaluator

BODY = "---\nname: demo\ndescription: does a thing\n---\n\n# Demo\n\n"


def _sized(target_bytes):
    """Build a valid skill body of roughly `target_bytes`."""
    filler = "Guidance line that carries real instruction content.\n"
    body = BODY
    while len(body.encode("utf-8")) < target_bytes:
        body += filler
    return body


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("SKILL_EVOLUTION_MAX_SHRINK_PCT", "SKILL_EVOLUTION_MAX_GROWTH_PCT",
                "SKILL_EVOLUTION_MAX_SKILL_SIZE_KB"):
        monkeypatch.delenv(var, raising=False)


def test_catastrophic_shrink_is_rejected():
    """The exact shape of the real GEPA regression: -70% of the body."""
    baseline = _sized(10258)
    candidate = _sized(3041)

    result = DeterministicEvaluator().evaluate(
        candidate, context={"content_kind": "body",
                            "baseline_size": len(baseline.encode("utf-8"))})

    assert result.passed is False
    assert "shrink" in result.feedback.lower()


def test_modest_tightening_still_passes():
    baseline = _sized(10000)
    candidate = _sized(9200)  # -8%, ordinary editing

    result = DeterministicEvaluator().evaluate(
        candidate, context={"content_kind": "body",
                            "baseline_size": len(baseline.encode("utf-8"))})

    assert result.passed is True, result.feedback


def test_shrink_floor_is_configurable(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_MAX_SHRINK_PCT", "80")
    baseline = _sized(10000)
    candidate = _sized(3000)  # -70%, now inside an explicitly widened floor

    result = DeterministicEvaluator().evaluate(
        candidate, context={"content_kind": "body",
                            "baseline_size": len(baseline.encode("utf-8"))})

    assert result.passed is True, result.feedback


def test_shrink_check_inert_without_baseline():
    """create_new has nothing to shrink from."""
    result = DeterministicEvaluator().evaluate(
        _sized(3000), context={"content_kind": "body"})

    assert result.passed is True, result.feedback


def test_growth_and_shrink_are_both_enforced():
    baseline_size = len(_sized(5000).encode("utf-8"))
    ev = DeterministicEvaluator()

    grew = ev.evaluate(_sized(9000), context={"content_kind": "body",
                                              "baseline_size": baseline_size})
    shrank = ev.evaluate(_sized(1500), context={"content_kind": "body",
                                                "baseline_size": baseline_size})

    assert grew.passed is False and "growth" in grew.feedback.lower()
    assert shrank.passed is False and "shrink" in shrank.feedback.lower()


def test_shrink_is_rejected_through_evaluate_skill_text(monkeypatch):
    """End-to-end through the live gate path, not just the isolated evaluator."""
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")

    class _Change:
        field = "body"
        old_value = _sized(10000)
        new_value = _sized(3000)

    class _Proposal:
        proposal_id = "p1"
        target_skill = "demo"
        summary = ""
        rationale = ""
        proposed_changes = [_Change()]

    results = evaluate.evaluate_skill_text(_Proposal())

    assert results[0].passed is False
    assert "shrink" in results[0].feedback.lower()
