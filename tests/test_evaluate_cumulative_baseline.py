"""Cumulative size drift, measured against a target's ORIGINAL recorded baseline.

The per-proposal growth cap and shrink floor each compare a candidate against the body it
immediately replaces, so the reference resets every pass and the limits compound. With a
20% floor, four accepted passes halve a skill (0.8**4 = 0.41) while every individual pass
looks compliant.

RegressionEvaluator does not catch it either: each deletion *raises* the judge score
(`conciseness` rewards brevity), so every pass legitimately outscores the last and the
gate keeps passing. The erosion is self-reinforcing, not self-limiting.

These tests pin the second reference point: the earliest size recorded for that target in
eval_history.jsonl, so total drift is bounded no matter how many passes it is spread over.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import DeterministicEvaluator, EvalResult

BODY = "---\nname: demo\ndescription: does a thing\n---\n\n# Demo\n\n"


def _sized(target_bytes):
    filler = "Guidance line that carries real instruction content.\n"
    body = BODY
    while len(body.encode("utf-8")) < target_bytes:
        body += filler
    return body


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in ("SKILL_EVOLUTION_MAX_GROWTH_PCT", "SKILL_EVOLUTION_MAX_SHRINK_PCT",
                "SKILL_EVOLUTION_MAX_CUMULATIVE_GROWTH_PCT",
                "SKILL_EVOLUTION_MAX_CUMULATIVE_SHRINK_PCT",
                "SKILL_EVOLUTION_MAX_SKILL_SIZE_KB",
                "SKILL_EVOLUTION_MAX_SHRINK_BYTES"):
        monkeypatch.delenv(var, raising=False)
    path = str(tmp_path / "eval_history.jsonl")
    monkeypatch.setattr(evaluate, "get_history_path", lambda: path)
    return path


def _eroding_case(original_bytes=12000):
    """Build the compounding scenario from the configured limits, not hardcoded numbers.

    Each pass shrinks by comfortably less than the per-pass floor (so that check never
    fires) and repeats until the total drift passes the cumulative floor. Derived from the
    constants so retuning a limit re-derives the case instead of silently making this a
    test of a stale number.
    """
    step = 1 - (evaluate.DEFAULT_MAX_SHRINK_PCT * 0.9) / 100   # safely inside per-pass
    limit = 1 - evaluate.DEFAULT_MAX_CUMULATIVE_SHRINK_PCT / 100
    ratio, passes = 1.0, 0
    while ratio > limit:
        ratio *= step
        passes += 1
    assert passes >= 2, "cumulative floor must need more than one pass to breach"
    previous_ratio = ratio / step
    return (_sized(int(original_bytes * ratio)),           # candidate
            len(_sized(int(original_bytes * previous_ratio)).encode("utf-8")),  # immediate
            len(_sized(original_bytes).encode("utf-8")))    # original


def test_the_compounding_case_is_rejected():
    """Several individually-legal passes breach the cumulative floor together."""
    candidate, immediate, original = _eroding_case()

    result = DeterministicEvaluator().evaluate(
        candidate, context={"content_kind": "body",
                            "baseline_size": immediate, "original_size": original})

    assert result.passed is False
    assert "cumulative" in result.feedback.lower(), result.feedback


def test_a_single_compliant_pass_still_passes():
    """original == previous: the first -10% edit is fine on both references."""
    base = len(_sized(10000).encode("utf-8"))
    result = DeterministicEvaluator().evaluate(
        _sized(9000), context={"content_kind": "body",
                               "baseline_size": base, "original_size": base})

    assert result.passed is True, result.feedback


def test_drift_within_the_cumulative_allowance_passes():
    """-24% total is past one pass's 20% but inside the 30% cumulative allowance."""
    result = DeterministicEvaluator().evaluate(
        _sized(7600),
        context={"content_kind": "body",
                 "baseline_size": len(_sized(8000).encode("utf-8")),
                 "original_size": len(_sized(10000).encode("utf-8"))})

    assert result.passed is True, result.feedback


def test_cumulative_growth_is_bounded_too():
    result = DeterministicEvaluator().evaluate(
        _sized(9000),
        context={"content_kind": "body",
                 "baseline_size": len(_sized(8000).encode("utf-8")),
                 "original_size": len(_sized(5000).encode("utf-8"))})

    assert result.passed is False
    assert "cumulative" in result.feedback.lower()


def test_cumulative_limits_are_configurable(monkeypatch):
    """The same case that breaches the default allowance passes under a widened one."""
    candidate, immediate, original = _eroding_case()
    monkeypatch.setenv("SKILL_EVOLUTION_MAX_CUMULATIVE_SHRINK_PCT", "90")

    result = DeterministicEvaluator().evaluate(
        candidate, context={"content_kind": "body",
                            "baseline_size": immediate, "original_size": original})

    assert result.passed is True, result.feedback


def test_cumulative_check_inert_without_original_size():
    """No history for this target yet -- only the per-pass check applies."""
    result = DeterministicEvaluator().evaluate(
        _sized(9000), context={"content_kind": "body",
                               "baseline_size": len(_sized(10000).encode("utf-8"))})

    assert result.passed is True, result.feedback


# ── history plumbing ────────────────────────────────────────────────────────

def test_append_history_records_sizes(clean_env):
    evaluate.append_history("skill:demo",
                            EvalResult(score=0.9, passed=True, feedback="ok", evaluator_name="gate"),
                            content_size=8000, baseline_size=10000)

    entry = json.loads(open(clean_env).read().strip())
    assert entry["content_size"] == 8000
    assert entry["baseline_size"] == 10000


def test_original_size_uses_the_earliest_recorded_baseline(clean_env):
    for content, base in ((8000, 10000), (6400, 8000), (5120, 6400)):
        evaluate.append_history("skill:demo",
                                EvalResult(score=0.9, passed=True, feedback="ok", evaluator_name="gate"),
                                content_size=content, baseline_size=base)

    assert evaluate.original_size_for_target("skill:demo") == 10000


def test_original_size_is_per_target(clean_env):
    evaluate.append_history("skill:a", EvalResult(score=1.0, passed=True, feedback="", evaluator_name="gate"),
                            content_size=100, baseline_size=999)
    evaluate.append_history("skill:b", EvalResult(score=1.0, passed=True, feedback="", evaluator_name="gate"),
                            content_size=100, baseline_size=555)

    assert evaluate.original_size_for_target("skill:a") == 999
    assert evaluate.original_size_for_target("skill:b") == 555


def test_original_size_none_for_unknown_target(clean_env):
    assert evaluate.original_size_for_target("skill:never-seen") is None


def test_original_size_tolerates_entries_without_sizes(clean_env):
    """History written before size recording existed must not break the lookup."""
    evaluate.append_history("skill:demo", EvalResult(score=0.9, passed=True, feedback="ok", evaluator_name="gate"))
    evaluate.append_history("skill:demo", EvalResult(score=0.9, passed=True, feedback="ok", evaluator_name="gate"),
                            content_size=6400, baseline_size=8000)

    assert evaluate.original_size_for_target("skill:demo") == 8000


def test_evaluate_skill_text_supplies_original_size_from_history(clean_env, monkeypatch):
    """The seam: the live gate path must look the original up, not just pass the immediate one."""
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    candidate, immediate, original = _eroding_case()
    evaluate.append_history("skill:demo", EvalResult(score=0.9, passed=True, feedback="ok", evaluator_name="gate"),
                            content_size=immediate, baseline_size=original)

    class _Change:
        field = "body"
        old_value = _sized(immediate)
        new_value = candidate

    class _Proposal:
        proposal_id = "p1"
        target_skill = "demo"
        summary = ""
        rationale = ""
        proposed_changes = [_Change()]

    results = evaluate.evaluate_skill_text(_Proposal())

    assert results[0].passed is False
    assert "cumulative" in results[0].feedback.lower()
