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
                "SKILL_EVOLUTION_MAX_SKILL_SIZE_KB", "SKILL_EVOLUTION_MAX_SHRINK_BYTES"):
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
    # The absolute floor has to be widened too, or it -- not the percentage -- becomes the
    # binding limit and this stops testing what it names. Widening one knob and not the
    # other is exactly the misconfiguration the two-check split makes legible.
    monkeypatch.setenv("SKILL_EVOLUTION_MAX_SHRINK_BYTES", "0")
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


# ── The absolute companion to the percentage floor ───────────────────────
# A percentage floor scales with the skill, so it is weakest exactly where a deletion does
# the most damage: 15% of the median 9,954B skill is ~1.5KB, but 15% of the largest
# installed one (103,656B) is 15,548B -- a whole median skill's worth of guidance gone in
# one pass. Until the cap became a ratchet that deletion was masked (the shrunk candidate
# was still over the cap and failed there first), so unblocking oversized skills is what
# made it reachable. The stricter of percentage and bytes applies.

def _ctx(baseline_bytes, kind="body"):
    return {"content_kind": kind, "baseline_size": baseline_bytes}


def test_absolute_byte_floor_binds_on_a_large_skill():
    """The measured hole: -15.0% is inside the percentage floor, but sheds 15KB."""
    baseline, candidate = 103656, 88108
    result = DeterministicEvaluator().evaluate(_sized(candidate), context=_ctx(baseline))

    assert result.passed is False
    assert "shrink" in result.feedback.lower()
    assert str(evaluate.DEFAULT_MAX_SHRINK_BYTES) in result.feedback


def test_percentage_floor_still_binds_on_a_median_skill():
    """Below the crossover the percentage is the operative limit, and must still be the one
    reported -- the byte floor complements it rather than replacing it."""
    result = DeterministicEvaluator().evaluate(_sized(8000), context=_ctx(9954))

    assert result.passed is False
    assert "%" in result.feedback


def test_byte_floor_is_inert_below_the_crossover():
    """Derived from the constants rather than hardcoded, so retuning either limit moves
    this test's own expectation with it."""
    crossover = evaluate.DEFAULT_MAX_SHRINK_BYTES / (evaluate.DEFAULT_MAX_SHRINK_PCT / 100)
    baseline = int(crossover) - 2000
    candidate = int(baseline * (1 - evaluate.DEFAULT_MAX_SHRINK_PCT / 100)) + 50

    result = DeterministicEvaluator().evaluate(_sized(candidate), context=_ctx(baseline))

    assert baseline - candidate < evaluate.DEFAULT_MAX_SHRINK_BYTES
    assert result.passed is True, result.feedback


def test_byte_floor_is_configurable(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_MAX_SHRINK_BYTES", "500")
    result = DeterministicEvaluator().evaluate(_sized(9200), context=_ctx(10000))

    assert result.passed is False
    assert "500B absolute" in result.feedback


def test_byte_floor_disabled_by_zero(monkeypatch):
    """0 is the escape hatch for a deliberate consolidation pass: percentage only."""
    monkeypatch.setenv("SKILL_EVOLUTION_MAX_SHRINK_BYTES", "0")
    baseline, candidate = 103656, 88108  # -15.0%, inside the percentage floor

    result = DeterministicEvaluator().evaluate(_sized(candidate), context=_ctx(baseline))

    assert result.passed is True, result.feedback


def test_byte_floor_inert_for_a_description_change():
    """Descriptions are a few hundred bytes, so the percentage is always the binding
    limit for them and the byte floor can never fire."""
    result = DeterministicEvaluator().evaluate("A short new description.", context=_ctx(300, kind="description"))

    assert result.passed is False
    assert "%" in result.feedback


def test_byte_floor_applies_through_evaluate_skill_text(monkeypatch, tmp_path):
    """Assert the wiring end to end, not just the evaluator in isolation -- the growth
    guard was dead on the live path for exactly this reason."""
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    monkeypatch.setenv("SKILL_EVOLUTION_HISTORY_PATH", str(tmp_path / "history.jsonl"))

    class _Change:
        field = "body"
        new_value = _sized(88108)
        old_value = _sized(103656)

    class _Proposal:
        proposal_id = "p1"
        target_skill = ""          # unresolvable -> falls back to old_value
        proposed_changes = [_Change()]

    results = evaluate.evaluate_skill_text(_Proposal())

    assert len(results) == 1
    assert results[0].passed is False
    assert "absolute per-pass" in results[0].feedback
