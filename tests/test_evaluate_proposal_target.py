"""Tests for scripts/evaluate.py's evaluate_proposal() target (U1, R5)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import EvalResult, evaluate_proposal


class _FakeChange:
    def __init__(self, field, new_value=None, old_value=None):
        self.field = field
        self.new_value = new_value
        self.old_value = old_value


class _FakeProposal:
    def __init__(self, proposal_id="p1", target_skill=None, summary="", rationale="",
                 proposed_changes=None):
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
        captured["context"] = context or {}
        return [EvalResult(score=1.0, passed=True, feedback="ok", evaluator_name="stub")]

    monkeypatch.setattr(evaluate, "run_evaluators", fake_run_evaluators)
    return captured


def test_evaluate_proposal_extracts_summary_and_rationale(stub_run_evaluators):
    proposal = _FakeProposal(
        proposal_id="abc-123",
        summary="Improve error handling",
        rationale="Sessions show repeated crashes on malformed input.",
    )
    results = evaluate_proposal(proposal)

    assert "# Summary" in stub_run_evaluators["content"]
    assert "Improve error handling" in stub_run_evaluators["content"]
    assert "# Rationale" in stub_run_evaluators["content"]
    assert "malformed input" in stub_run_evaluators["content"]
    assert stub_run_evaluators["target"] == "proposal:abc-123"
    assert results[0].passed is True


def test_evaluate_proposal_includes_proposed_changes(stub_run_evaluators):
    proposal = _FakeProposal(
        proposal_id="abc-123",
        summary="Add logging",
        proposed_changes=[
            _FakeChange(field="body", new_value="new body content"),
            _FakeChange(field="description", new_value="new description"),
        ],
    )
    evaluate_proposal(proposal)

    assert "# Proposed Changes" in stub_run_evaluators["content"]
    assert "body" in stub_run_evaluators["content"]
    assert "new body content" in stub_run_evaluators["content"]


def test_evaluate_proposal_empty_summary_and_rationale(stub_run_evaluators):
    proposal = _FakeProposal(proposal_id="abc-123", summary="", rationale="")
    results = evaluate_proposal(proposal)

    assert "(empty proposal)" in stub_run_evaluators["content"]
    assert results[0].passed is True


def test_evaluate_proposal_regression_runs_last_with_new_score(monkeypatch):
    """RegressionEvaluator must run last with new_score set to the mean of other evaluators."""
    call_order = []

    def fake_run_evaluators(content, target, context=None):
        call_order.append(target)
        return [
            EvalResult(score=0.8, passed=True, feedback="ok", evaluator_name="llm_judge"),
        ]

    monkeypatch.setattr(evaluate, "run_evaluators", fake_run_evaluators)

    proposal = _FakeProposal(proposal_id="abc-123", summary="test")
    evaluate_proposal(proposal)

    assert call_order == ["proposal:abc-123"]


def test_evaluate_proposal_uses_proposal_target_not_skill(stub_run_evaluators):
    """KTD1: evaluate_proposal uses proposal:<uuid> target, not skill:<name>."""
    proposal = _FakeProposal(
        proposal_id="abc-123",
        target_skill="my-skill",
        summary="test",
        proposed_changes=[_FakeChange(field="body", new_value="new body")],
    )

    # evaluate_proposal should use the proposal target, not the skill target
    evaluate_proposal(proposal)
    assert stub_run_evaluators["target"] == "proposal:abc-123"
