"""Tests for the evaluation gate wired into proposal.py's apply_proposal() (U7)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import host
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


def test_gate_level_error_blocks_apply_instead_of_crashing(monkeypatch, isolated_history):
    """A misconfigured env value (bad evaluator name, bad gate strictness) raises
    one level above any individual evaluator -- apply_proposal() must still fail
    closed (can_apply: False) rather than let the exception propagate uncaught."""
    def raising_evaluate_and_record(proposal, session_ids=None):
        raise ValueError("simulated misconfiguration: unknown evaluator")

    monkeypatch.setattr(evaluate, "evaluate_and_record", raising_evaluate_and_record)

    proposal = _make_proposal()
    result = apply_proposal(proposal, min_confidence=0.5)

    assert result["can_apply"] is False
    assert proposal.status == ProposalStatus.PROPOSED
    assert "evaluation_error" in result


_REAL_SAVE_PROPOSAL = proposal_module.save_proposal


class _ReadOnlyAdapter(host.HostAdapter):
    """A synthetic host that cannot mutate skills (supports_write stays False)."""

    name = "read_only_host"

    def iter_sessions(self, since=None):
        return []

    def iter_skills(self):
        return []


def test_read_only_host_blocks_apply_before_gate_without_mutating_file(monkeypatch, tmp_path):
    """U2 (write side): apply_proposal() delegates mutations to the active host's
    adapter, gated on adapter.supports_write. A host that can't write must refuse
    immediately -- before the evaluation gate ever runs (no provider call spent on a
    proposal that can never be applied) -- and without mutating the proposal file on
    disk or its in-memory status."""
    # Override the isolated_history fixture's save_proposal stub with the real function
    # for this test only, so "the file is untouched" is actually load-bearing here.
    monkeypatch.setattr(proposal_module, "save_proposal", _REAL_SAVE_PROPOSAL)

    def _boom(*args, **kwargs):
        raise AssertionError("evaluation gate must not run when the host cannot write")

    monkeypatch.setattr(evaluate, "evaluate_and_record", _boom)
    monkeypatch.setitem(host.HOST_ADAPTERS, "read_only_host", _ReadOnlyAdapter())
    monkeypatch.setenv(host.HOST_ENV_VAR, "read_only_host")

    proposal = _make_proposal()
    path = proposal_module.save_proposal(proposal, directory=str(tmp_path))
    with open(path) as f:
        original_content = f.read()
    assert "status: proposed" in original_content

    result = apply_proposal(proposal, min_confidence=0.5, directory=str(tmp_path))

    assert result["can_apply"] is False
    assert "read_only_host" in result["reason"]
    assert "does not support skill writes" in result["reason"]
    assert proposal.status == ProposalStatus.PROPOSED  # in-memory object also untouched

    with open(path) as f:
        assert f.read() == original_content


def test_unknown_host_blocks_apply_before_gate(monkeypatch, tmp_path):
    """An unknown host name resolves to no adapter at all -- apply_proposal() must
    refuse with the ValueError's message before the gate runs, not crash."""
    def _boom(*args, **kwargs):
        raise AssertionError("evaluation gate must not run for an unknown host")

    monkeypatch.setattr(evaluate, "evaluate_and_record", _boom)
    monkeypatch.setenv(host.HOST_ENV_VAR, "no_such_host")

    proposal = _make_proposal()
    result = apply_proposal(proposal, min_confidence=0.5)

    assert result["can_apply"] is False
    assert "no_such_host" in result["reason"]
    assert proposal.status == ProposalStatus.PROPOSED


def test_claude_code_host_applies_by_writing_skill_file(monkeypatch, tmp_path):
    """P2-2 end to end: with SKILL_EVOLUTION_HOST=claude_code, an approved
    improve_existing proposal is applied by the adapter writing the installed SKILL.md
    directly -- no skill_manage instructions, the file on disk changes."""
    monkeypatch.setattr(proposal_module, "save_proposal", _REAL_SAVE_PROPOSAL)
    monkeypatch.setenv(host.HOST_ENV_VAR, "claude_code")
    monkeypatch.setenv(host.CLAUDE_CODE_HOME_ENV_VAR, str(tmp_path))
    _stub_evaluators(monkeypatch, deterministic_passes=True, llm_judge_passes=True)

    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Old description.\n---\n\n# test-skill\n\nOld body.\n"
    )

    proposal = _make_proposal(target_skill="test-skill")
    proposal.proposed_changes = [ProposedChange(
        field="body", old_value="Old body.", new_value="New body.",
    )]
    path = proposal_module.save_proposal(proposal, directory=str(tmp_path / "proposals"))
    assert path.endswith("fixture-001.md")

    result = apply_proposal(proposal, min_confidence=0.5, directory=str(tmp_path / "proposals"))

    assert result["can_apply"] is True
    assert proposal.status == ProposalStatus.APPLIED
    assert result["applied_by"] == "direct"
    assert result["instructions"] == []  # no skill_manage dicts: the write already happened
    new_text = (skill_dir / "SKILL.md").read_text()
    assert "New body." in new_text
    assert "Old body." not in new_text


def test_create_new_with_placeholder_body_refuses_before_gate(monkeypatch, isolated_history):
    """KTD3: the real 20260729-002 bug -- a create_new proposal whose body is the
    placeholder "See proposal body for full draft content" can never create a skill.
    It must be refused before the gate spends a provider call, and its status must stay
    proposed (never applied)."""
    def _boom(*args, **kwargs):
        raise AssertionError("evaluation gate must not run for a placeholder-bodied create_new")

    monkeypatch.setattr(evaluate, "evaluate_and_record", _boom)

    proposal = SkillEvolutionProposal(
        proposal_id="placeholder-001",
        type=ProposalType.CREATE_NEW,
        target_skill=None,
        confidence=0.9,
        summary="Create my-new-skill",
        rationale="Fixture rationale.",
        proposed_changes=[
            ProposedChange(field="name", new_value="my-new-skill"),
            ProposedChange(field="description", new_value="A brand new skill."),
            ProposedChange(field="category", new_value="general-skills"),
            ProposedChange(field="body", new_value=(
                "---\nname: my-new-skill\ndescription: A brand new skill.\n---\n\n"
                "See proposal body for full draft content"
            )),
        ],
    )

    result = apply_proposal(proposal, min_confidence=0.5)

    assert result["can_apply"] is False
    assert "placeholder" in result["reason"].lower()
    assert proposal.status == ProposalStatus.PROPOSED


def test_create_new_without_body_change_refuses_before_gate(monkeypatch, isolated_history):
    """A create_new proposal that ships no body change at all is equally unapplicable."""
    def _boom(*args, **kwargs):
        raise AssertionError("evaluation gate must not run for a body-less create_new")

    monkeypatch.setattr(evaluate, "evaluate_and_record", _boom)

    proposal = SkillEvolutionProposal(
        proposal_id="nobody-001",
        type=ProposalType.CREATE_NEW,
        target_skill=None,
        confidence=0.9,
        summary="Create my-new-skill",
        rationale="Fixture rationale.",
        proposed_changes=[
            ProposedChange(field="name", new_value="my-new-skill"),
            ProposedChange(field="description", new_value="A brand new skill."),
        ],
    )

    result = apply_proposal(proposal, min_confidence=0.5)

    assert result["can_apply"] is False
    assert "body" in result["reason"].lower()
    assert proposal.status == ProposalStatus.PROPOSED


def test_apply_proposal_create_new_migrates_history(monkeypatch, isolated_history):
    """After a create_new proposal passes the gate, skill-text history is under
    skill:<name>, not proposal:<uuid>. Covers the end-to-end migration wiring."""
    _stub_evaluators(monkeypatch, deterministic_passes=True, llm_judge_passes=True)

    proposal = SkillEvolutionProposal(
        proposal_id="create-001",
        type=ProposalType.CREATE_NEW,
        target_skill=None,
        confidence=0.9,
        summary="Create my-new-skill",
        rationale="Fixture rationale.",
        proposed_changes=[
            ProposedChange(field="name", new_value="my-new-skill"),
            ProposedChange(field="description", new_value="A brand new skill."),
            ProposedChange(field="category", new_value="general-skills"),
            ProposedChange(field="body", new_value=(
                "---\nname: my-new-skill\ndescription: A brand new skill.\n---\n\n"
                "# my-new-skill\n\nGuidance body here."
            )),
        ],
    )

    result = apply_proposal(proposal, min_confidence=0.5)

    assert result["can_apply"] is True
    assert proposal.status == ProposalStatus.APPLIED

    # KTD3: the Hermes create instruction now carries name + the full body -- the
    # skill_manage tool rejects 'create' without content, and shipping a placeholder
    # body made even the Hermes path unapplicable (the 20260729-002 bug).
    assert result["instructions"] == [{
        "action": "create",
        "name": "my-new-skill",
        "target_skill": "",
        "description": "A brand new skill.",
        "category": "general-skills",
        "body": "---\nname: my-new-skill\ndescription: A brand new skill.\n---\n\n# my-new-skill\n\nGuidance body here.",
    }]

    # Skill-text history keyed under skill:my-new-skill, not proposal:create-001
    skill_entries = evaluate.read_history("skill:my-new-skill")
    assert len(skill_entries) == 1
    assert skill_entries[0]["evaluator_name"] == "gate"

    # The proposal-document gate entry (P2-1) is a separate lineage: it stays under
    # proposal:create-001 with kind="proposal" and must NOT be migrated into the skill's
    # lineage -- folding a document score in would skew RegressionEvaluator's baseline.
    old_entries = evaluate.read_history("proposal:create-001")
    assert len(old_entries) == 1
    assert old_entries[0]["kind"] == "proposal"
    assert len(evaluate.read_history("skill:my-new-skill", path=None)) == 1
