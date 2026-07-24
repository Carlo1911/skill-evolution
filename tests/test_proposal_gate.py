"""Tests for the evaluation gate wired into proposal.py's apply_proposal() (U7)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import proposal as proposal_module
from proposal import ProposalStatus, ProposalType, ProposedChange, SkillEvolutionProposal, apply_proposal


def _make_proposal(target_skill="test-skill", confidence=0.9, body="Well-formed body."):
    return SkillEvolutionProposal(
        proposal_id="fixture-001",
        type=ProposalType.IMPROVE_EXISTING,
        target_skill=target_skill,
        confidence=confidence,
        summary="Improve test-skill",
        rationale="Fixture rationale.",
        proposed_changes=[ProposedChange(field="body", new_value=body)],
    )


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    history_path = str(tmp_path / "eval_history.jsonl")
    monkeypatch.setattr(evaluate, "get_history_path", lambda: history_path)
    monkeypatch.setattr(proposal_module, "save_proposal", lambda p, directory=None: "noop")
    return history_path


@pytest.fixture(autouse=True)
def clean_gate_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("SKILL_EVOLUTION_GATE_STRICTNESS") or key.startswith("SKILL_EVOLUTION_") and key.endswith("_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("SKILL_EVOLUTION_EVALUATORS", raising=False)


def _stub_evaluators(monkeypatch, deterministic_passes=True, llm_judge_passes=True):
    def det_evaluate(self, content, context=None):
        return evaluate.EvalResult(
            score=1.0 if deterministic_passes else 0.0,
            passed=deterministic_passes, feedback="det", evaluator_name="deterministic",
        )

    def judge_evaluate(self, content, context=None):
        return evaluate.EvalResult(
            score=0.9 if llm_judge_passes else 0.2,
            passed=llm_judge_passes, feedback="judge", evaluator_name="llm_judge",
        )

    monkeypatch.setattr(evaluate.DeterministicEvaluator, "evaluate", det_evaluate)
    monkeypatch.setattr(evaluate.LLMJudgeEvaluator, "evaluate", judge_evaluate)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic,llm_judge")


def test_below_threshold_llm_judge_blocks_auto_apply_strict_and(monkeypatch, isolated_history):
    """Covers AE1: llm_judge fails, deterministic passes -> strict AND blocks."""
    _stub_evaluators(monkeypatch, deterministic_passes=True, llm_judge_passes=False)
    proposal = _make_proposal()

    result = apply_proposal(proposal, min_confidence=0.5)

    assert result["can_apply"] is False
    assert proposal.status == ProposalStatus.PROPOSED  # unchanged


def test_all_evaluators_and_confidence_pass_allows_apply(monkeypatch, isolated_history):
    _stub_evaluators(monkeypatch, deterministic_passes=True, llm_judge_passes=True)
    proposal = _make_proposal()

    result = apply_proposal(proposal, min_confidence=0.5)

    assert result["can_apply"] is True
    assert proposal.status == ProposalStatus.APPLIED


def test_no_evaluators_configured_matches_todays_behavior(monkeypatch, isolated_history):
    """When zero evaluators run, the gate never blocks -- matching pre-U7 behavior."""
    monkeypatch.setattr(evaluate, "run_evaluators", lambda content, target, context=None: [])
    proposal = _make_proposal()

    result = apply_proposal(proposal, min_confidence=0.5)

    assert result["can_apply"] is True
    assert proposal.status == ProposalStatus.APPLIED


def test_per_type_gate_strictness_override(monkeypatch, isolated_history):
    """Edge: SKILL_EVOLUTION_GATE_STRICTNESS_DEPRECATE_SKILL applies only to deprecate_skill proposals."""
    _stub_evaluators(monkeypatch, deterministic_passes=True, llm_judge_passes=False)
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_STRICTNESS_DEPRECATE_SKILL", "majority")

    deprecate_proposal = SkillEvolutionProposal(
        proposal_id="fixture-002",
        type=ProposalType.DEPRECATE_SKILL,
        target_skill="stale-skill",
        confidence=0.9,
        summary="Deprecate stale-skill",
        rationale="Fixture rationale.",
    )
    result = apply_proposal(deprecate_proposal, min_confidence=0.5)
    # majority: 1 of 2 pass -> not a majority -> still blocked, but exercised via the override path
    assert result["can_apply"] is False

    improve_proposal = _make_proposal(target_skill="other-skill")
    result2 = apply_proposal(improve_proposal, min_confidence=0.5)
    # improve_existing keeps the global strict default -> blocked too (llm_judge fails)
    assert result2["can_apply"] is False


def test_evaluator_provider_failure_blocks_and_keeps_proposed(monkeypatch, isolated_history):
    def raising_evaluate(self, content, context=None):
        raise evaluate.ProviderError("simulated provider outage")

    monkeypatch.setattr(evaluate.LLMJudgeEvaluator, "evaluate", raising_evaluate)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "llm_judge")

    proposal = _make_proposal()
    result = apply_proposal(proposal, min_confidence=0.5)

    assert result["can_apply"] is False
    assert proposal.status == ProposalStatus.PROPOSED


def test_combined_result_appended_to_history_exactly_once_on_pass(monkeypatch, isolated_history):
    _stub_evaluators(monkeypatch, deterministic_passes=True, llm_judge_passes=True)
    proposal = _make_proposal()
    apply_proposal(proposal, min_confidence=0.5)

    entries = evaluate.read_history(evaluate.target_key_for_proposal(proposal))
    assert len(entries) == 1
    assert entries[0]["evaluator_name"] == "gate"


def test_combined_result_appended_to_history_exactly_once_on_fail(monkeypatch, isolated_history):
    _stub_evaluators(monkeypatch, deterministic_passes=True, llm_judge_passes=False)
    proposal = _make_proposal()
    apply_proposal(proposal, min_confidence=0.5)

    entries = evaluate.read_history(evaluate.target_key_for_proposal(proposal))
    assert len(entries) == 1
    assert entries[0]["evaluator_name"] == "gate"
    assert entries[0]["passed"] is False
