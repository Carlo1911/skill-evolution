"""Tests for scripts/evaluate.py's deterministic evaluator (U4)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

from evaluate import DeterministicEvaluator


def _skill_body(extra_chars=0, name="my-skill", description="Does a thing."):
    body = f"---\nname: {name}\ndescription: {description}\n---\n\n# Body\n\nSome content.\n"
    return body + ("x" * extra_chars)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("SKILL_EVOLUTION_MAX_SKILL_SIZE_KB", raising=False)
    monkeypatch.delenv("SKILL_EVOLUTION_MAX_GROWTH_PCT", raising=False)
    monkeypatch.delenv("SKILL_EVOLUTION_MAX_SHRINK_PCT", raising=False)
    monkeypatch.delenv("SKILL_EVOLUTION_MAX_SHRINK_BYTES", raising=False)


def test_well_formed_appropriately_sized_skill_passes():
    evaluator = DeterministicEvaluator()
    result = evaluator.evaluate(_skill_body())
    assert result.passed is True
    assert result.score == 1.0
    assert result.evaluator_name == "deterministic"


def test_skill_body_just_under_15kb_passes():
    evaluator = DeterministicEvaluator()
    content = _skill_body()
    padding = (15 * 1024) - len(content.encode("utf-8")) - 10
    content = _skill_body(extra_chars=padding)
    assert len(content.encode("utf-8")) < 15 * 1024
    result = evaluator.evaluate(content)
    assert result.passed is True


def test_skill_body_just_over_15kb_fails_with_size_feedback():
    evaluator = DeterministicEvaluator()
    content = _skill_body()
    padding = (15 * 1024) - len(content.encode("utf-8")) + 100
    content = _skill_body(extra_chars=padding)
    assert len(content.encode("utf-8")) > 15 * 1024
    result = evaluator.evaluate(content)
    assert result.passed is False
    assert "size" in result.feedback.lower()


def test_size_exactly_at_boundary_passes(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_MAX_SKILL_SIZE_KB", "1")
    evaluator = DeterministicEvaluator()
    base = "---\nname: x\ndescription: y\n---\n"
    exact_padding = 1024 - len(base.encode("utf-8"))
    content = base + ("z" * exact_padding)
    assert len(content.encode("utf-8")) == 1024
    result = evaluator.evaluate(content)
    assert result.passed is True


def test_growth_exactly_at_20_percent_boundary_passes():
    evaluator = DeterministicEvaluator()
    baseline_size = 1000
    content = _skill_body()
    # Pad content so its size is exactly baseline * 1.20
    target_size = int(baseline_size * 1.20)
    current_size = len(content.encode("utf-8"))
    padding = max(0, target_size - current_size)
    content = _skill_body(extra_chars=padding)
    result = evaluator.evaluate(content, context={"baseline_size": baseline_size})
    assert result.passed is True


def test_growth_just_over_20_percent_fails_with_growth_feedback():
    evaluator = DeterministicEvaluator()
    baseline_size = 1000
    content = _skill_body()
    target_size = int(baseline_size * 1.25)
    current_size = len(content.encode("utf-8"))
    padding = max(0, target_size - current_size)
    content = _skill_body(extra_chars=padding)
    result = evaluator.evaluate(content, context={"baseline_size": baseline_size})
    assert result.passed is False
    assert "growth" in result.feedback.lower()


def test_missing_frontmatter_fails_with_specific_feedback():
    evaluator = DeterministicEvaluator()
    result = evaluator.evaluate("# Just a heading\n\nNo frontmatter here.", context={"content_kind": "body"})
    assert result.passed is False
    assert "frontmatter" in result.feedback.lower()


def test_non_body_content_skips_frontmatter_check():
    """A description-only change or summary+rationale fallback is plain prose,
    never a full skill-file body -- the frontmatter check must not apply to it."""
    evaluator = DeterministicEvaluator()
    result = evaluator.evaluate("Just a plain description string with no frontmatter at all.", context={"content_kind": "description"})
    assert result.passed is True

    result = evaluator.evaluate("Some summary\n\nSome rationale", context={"content_kind": "summary_rationale"})
    assert result.passed is True


def test_missing_description_field_fails_naming_the_field():
    evaluator = DeterministicEvaluator()
    content = "---\nname: my-skill\n---\n\nBody\n"
    result = evaluator.evaluate(content, context={"content_kind": "body"})
    assert result.passed is False
    assert "description" in result.feedback.lower()


def test_missing_name_field_fails_naming_the_field():
    evaluator = DeterministicEvaluator()
    content = "---\ndescription: does a thing\n---\n\nBody\n"
    result = evaluator.evaluate(content, context={"content_kind": "body"})
    assert result.passed is False
    assert "name" in result.feedback.lower()


# ── The cap as a ratchet ─────────────────────────────────────────────────
# 22 of the 143 installed skills already exceed the 15KB cap. A flat ceiling rejected a
# proposal *shrinking* one of them toward compliance with the identical message as one
# growing it, so the gate could not tell improvement from worsening. The cap now fails only
# when the candidate is over it AND larger than the body it replaces.

def _oversized(size_bytes):
    """A structurally valid body of exactly `size_bytes`, over the 15360B cap."""
    body = _skill_body()
    return body + "x" * (size_bytes - len(body.encode("utf-8")))


def test_oversized_candidate_smaller_than_its_baseline_is_admitted():
    """The defect this fixes: an over-cap skill may be improved downward."""
    result = DeterministicEvaluator().evaluate(
        _oversized(20000), context={"content_kind": "body", "baseline_size": 21000})
    assert result.passed is True, result.feedback


def test_oversized_candidate_equal_to_its_baseline_is_admitted():
    """A pure rewrite at unchanged length is the most valuable thing this unblocks, and is
    exactly what _build_objective() asks for when no size headroom is left."""
    result = DeterministicEvaluator().evaluate(
        _oversized(20000), context={"content_kind": "body", "baseline_size": 20000})
    assert result.passed is True, result.feedback


def test_oversized_candidate_larger_than_its_baseline_is_rejected():
    result = DeterministicEvaluator().evaluate(
        _oversized(21000), context={"content_kind": "body", "baseline_size": 20000})
    assert result.passed is False
    # Naming both numbers is the point: the old message was identical for an improvement
    # and a worsening, which is what made the two indistinguishable to a reviewer.
    assert "21000" in result.feedback and "20000" in result.feedback


def test_oversized_candidate_with_no_baseline_is_rejected():
    """create_new carries no baseline, so the strict cap holds and a skill is never born
    oversized. The exemption is a consequence of the rule, not a proposal-type check."""
    result = DeterministicEvaluator().evaluate(
        _oversized(20000), context={"content_kind": "body"})
    assert result.passed is False
    assert "new skill" in result.feedback.lower()


def test_compliant_skill_still_cannot_exceed_the_cap():
    """The ratchet must not leak into the 121 skills that are within the cap."""
    result = DeterministicEvaluator().evaluate(
        _oversized(16000), context={"content_kind": "body", "baseline_size": 14000})
    assert result.passed is False
    assert "exceeds" in result.feedback


def test_zero_baseline_size_keeps_the_strict_cap():
    """A 0 baseline keeps strict behaviour, matching the `if baseline_size:` gating the
    per-pass checks already use."""
    result = DeterministicEvaluator().evaluate(
        _oversized(20000), context={"content_kind": "body", "baseline_size": 0})
    assert result.passed is False


def test_missing_baseline_does_not_raise_on_the_cap_check():
    """Regression: comparing size to a None baseline directly is a TypeError, which would
    turn every create_new into an exception instead of a gate decision."""
    result = DeterministicEvaluator().evaluate(
        _oversized(20000), context={"content_kind": "body", "baseline_size": None})
    assert result.passed is False
