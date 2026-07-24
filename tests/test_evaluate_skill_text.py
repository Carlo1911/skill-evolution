"""Tests for scripts/evaluate.py's evaluate_skill_text() target wiring (U8)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import EvalResult, evaluate_skill_text, target_key_for_proposal


class _FakeChange:
    def __init__(self, field, new_value=None, old_value=None):
        self.field = field
        self.new_value = new_value
        self.old_value = old_value


class _FakeProposal:
    def __init__(self, proposal_id="p1", target_skill=None, summary="", rationale="", proposed_changes=None):
        self.proposal_id = proposal_id
        self.target_skill = target_skill
        self.summary = summary
        self.rationale = rationale
        self.proposed_changes = proposed_changes or []


@pytest.fixture(autouse=True)
def stub_run_evaluators(monkeypatch):
    captured = {}

    def fake_run_evaluators(content, target, context=None):
        captured["content"] = content
        captured["target"] = target
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


def test_target_key_uses_skill_prefix_when_target_skill_present():
    proposal = _FakeProposal(target_skill="foo")
    assert target_key_for_proposal(proposal) == "skill:foo"


def test_target_key_falls_back_to_proposal_id_when_no_target_skill():
    proposal = _FakeProposal(proposal_id="abc-123", target_skill=None)
    assert target_key_for_proposal(proposal) == "proposal:abc-123"
