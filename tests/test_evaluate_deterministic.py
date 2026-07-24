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
