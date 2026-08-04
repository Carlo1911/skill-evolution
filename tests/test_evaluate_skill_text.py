"""Tests for scripts/evaluate.py's evaluate_skill_text() target wiring (U8)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import EvalResult, evaluate_skill_text, target_key_for_proposal
from proposal import ProposalType

# Captured before the autouse stub below replaces it, so the integration test at the
# bottom of this file can run the real evaluator chain.
_REAL_RUN_EVALUATORS = evaluate.run_evaluators


class _FakeChange:
    def __init__(self, field, new_value=None, old_value=None):
        self.field = field
        self.new_value = new_value
        self.old_value = old_value


class _FakeProposal:
    def __init__(self, proposal_id="p1", target_skill=None, summary="", rationale="",
                 proposed_changes=None, type=ProposalType.IMPROVE_EXISTING):
        self.proposal_id = proposal_id
        self.target_skill = target_skill
        self.summary = summary
        self.rationale = rationale
        self.proposed_changes = proposed_changes or []
        self.type = type


@pytest.fixture(autouse=True)
def stub_run_evaluators(monkeypatch):
    captured = {}

    def fake_run_evaluators(content, target, context=None):
        captured["content"] = content
        captured["target"] = target
        captured["context"] = context or {}
        return [EvalResult(score=1.0, passed=True, feedback="ok", evaluator_name="stub")]

    monkeypatch.setattr(evaluate, "run_evaluators", fake_run_evaluators)
    return captured


def test_improve_existing_body_change_is_extracted(stub_run_evaluators):
    proposal = _FakeProposal(
        target_skill="my-skill",
        proposed_changes=[_FakeChange(field="body", new_value="# My Skill\n\nBody content.")],
    )
    results = evaluate_skill_text(proposal)

    assert stub_run_evaluators["content"] == "# My Skill\n\nBody content."
    assert stub_run_evaluators["target"] == "skill:my-skill"
    assert results[0].passed is True


def test_create_new_proposal_evaluates_description_without_baseline(stub_run_evaluators):
    proposal = _FakeProposal(
        target_skill="brand-new-skill",
        proposed_changes=[
            _FakeChange(field="description", new_value="A brand new skill description."),
            _FakeChange(field="category", new_value="general-skills"),
        ],
    )
    results = evaluate_skill_text(proposal)

    assert stub_run_evaluators["content"] == "A brand new skill description."
    assert stub_run_evaluators["target"] == "skill:brand-new-skill"
    assert len(results) == 1


def test_proposal_with_no_body_or_description_falls_back_to_summary_and_rationale(stub_run_evaluators):
    """merge_skills-shaped proposals have no single body/description field."""
    proposal = _FakeProposal(
        target_skill="merged-skill",
        summary="Merge skill-a and skill-b",
        rationale="They overlap significantly.",
        proposed_changes=[_FakeChange(field="source_skill_a", new_value="skill-a")],
    )
    results = evaluate_skill_text(proposal)

    assert "Merge skill-a and skill-b" in stub_run_evaluators["content"]
    assert "They overlap significantly." in stub_run_evaluators["content"]
    assert len(results) == 1


def test_baseline_size_is_wired_from_the_change_old_value(stub_run_evaluators):
    """DeterministicEvaluator's growth guard reads context["baseline_size"].

    Without it the growth-vs-baseline check silently no-ops, so a proposal may balloon a
    skill far past SKILL_EVOLUTION_MAX_GROWTH_PCT and still report
    "all deterministic checks passed".
    """
    baseline = "# My Skill\n\nShort body.\n"
    proposal = _FakeProposal(
        target_skill="my-skill",
        proposed_changes=[_FakeChange(field="body", new_value=baseline + "more\n", old_value=baseline)],
    )
    evaluate_skill_text(proposal)

    assert stub_run_evaluators["context"].get("baseline_size") == len(baseline.encode("utf-8"))


def test_baseline_size_absent_when_change_has_no_old_value(stub_run_evaluators):
    """create_new has nothing to grow from -- the growth check must stay inert, not fire on 0."""
    proposal = _FakeProposal(
        target_skill="brand-new-skill",
        proposed_changes=[_FakeChange(field="description", new_value="A brand new skill.")],
    )
    evaluate_skill_text(proposal)

    assert not stub_run_evaluators["context"].get("baseline_size")


def test_excessive_growth_is_rejected_through_evaluate_skill_text(monkeypatch):
    """End-to-end guard: the real deterministic evaluator must reject runaway growth.

    Covers the seam the isolated DeterministicEvaluator tests miss -- those pass
    baseline_size in by hand, so they cannot catch evaluate_skill_text() failing to
    supply it.
    """
    monkeypatch.setattr(evaluate, "run_evaluators", _REAL_RUN_EVALUATORS)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")

    baseline = "---\nname: my-skill\ndescription: does a thing\n---\n\n# My Skill\n\nShort body.\n"
    bloated = baseline + ("\nverbose padding line.\n" * 60)  # far beyond the 20% default

    proposal = _FakeProposal(
        target_skill="my-skill",
        proposed_changes=[_FakeChange(field="body", new_value=bloated, old_value=baseline)],
    )
    results = evaluate_skill_text(proposal)

    assert results[0].passed is False, "runaway growth must not pass the deterministic gate"
    assert "growth" in results[0].feedback.lower()


def test_target_key_uses_skill_prefix_when_target_skill_present():
    proposal = _FakeProposal(target_skill="foo")
    assert target_key_for_proposal(proposal) == "skill:foo"


def test_target_key_falls_back_to_proposal_id_when_no_target_skill():
    proposal = _FakeProposal(proposal_id="abc-123", target_skill=None)
    assert target_key_for_proposal(proposal) == "proposal:abc-123"


def test_target_key_create_new_extracts_name_from_changes():
    """create_new proposal with field='name' in proposed_changes returns skill:<name>."""
    proposal = _FakeProposal(
        proposal_id="abc-123",
        target_skill=None,
        type=ProposalType.CREATE_NEW,
        proposed_changes=[
            _FakeChange(field="name", new_value="my-new-skill"),
            _FakeChange(field="description", new_value="A new skill."),
        ],
    )
    assert target_key_for_proposal(proposal) == "skill:my-new-skill"


def test_target_key_create_new_no_name_field_falls_back():
    """create_new proposal without a name change falls back to proposal:<uuid>."""
    proposal = _FakeProposal(
        proposal_id="abc-123",
        target_skill=None,
        type=ProposalType.CREATE_NEW,
        proposed_changes=[
            _FakeChange(field="description", new_value="A new skill."),
        ],
    )
    assert target_key_for_proposal(proposal) == "proposal:abc-123"


def test_target_key_non_create_new_no_target_skill():
    """Non-create_new proposal with no target_skill stays proposal:<uuid>."""
    proposal = _FakeProposal(
        proposal_id="abc-123",
        target_skill=None,
        type=ProposalType.IMPROVE_EXISTING,
    )
    assert target_key_for_proposal(proposal) == "proposal:abc-123"
