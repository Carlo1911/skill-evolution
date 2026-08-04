"""Tests for P2-1: the multi-target evaluation gate.

evaluate_and_record() now runs every target in SKILL_EVOLUTION_GATE_TARGETS (default
skill,proposal) through the evaluator registry, combines each per its own strictness, and
ANDs the per-target decisions. This file covers the resolver, the per-target strictness
override, the no-data skip semantics, the per-target history entries, and the interaction
with migrate_proposal_history()'s kind filter.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import proposal as proposal_module
from evaluate import EvalResult
from proposal import ProposalStatus, ProposalType, ProposedChange, SkillEvolutionProposal, apply_proposal


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    history_path = str(tmp_path / "eval_history.jsonl")
    monkeypatch.setattr(evaluate, "get_history_path", lambda: history_path)
    monkeypatch.setattr(proposal_module, "save_proposal", lambda p, directory=None: "noop")
    return history_path


@pytest.fixture(autouse=True)
def clean_gate_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("SKILL_EVOLUTION_GATE_TARGETS") or key.startswith("SKILL_EVOLUTION_GATE_STRICTNESS"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("SKILL_EVOLUTION_EVALUATORS", raising=False)


def _proposal(proposal_id="fixture-001", target_skill="test-skill", session_ids=None):
    return SkillEvolutionProposal(
        proposal_id=proposal_id,
        type=ProposalType.IMPROVE_EXISTING,
        target_skill=target_skill,
        confidence=0.9,
        summary="Improve test-skill",
        rationale="Fixture rationale.",
        proposed_changes=[ProposedChange(field="body", new_value="Well-formed body.")],
        session_ids=session_ids or [],
    )


def _result(passed=True, score=0.9, name="stub", transport_failure=False):
    return EvalResult(score=score, feedback=f"{name}", passed=passed,
                      evaluator_name=name, transport_failure=transport_failure)


def _stub_targets(monkeypatch, skill=None, proposal=None, tool_calls=None, analyzer_prompt=None):
    """Stub the four target functions with single-result evaluators per target."""
    def _wrap(result):
        return lambda *a, **k: [result] if result is not None else []

    monkeypatch.setattr(evaluate, "evaluate_skill_text", _wrap(skill) if skill is not None else _wrap(_result()))
    monkeypatch.setattr(evaluate, "evaluate_proposal", _wrap(proposal) if proposal is not None else _wrap(_result()))
    monkeypatch.setattr(evaluate, "evaluate_tool_calls", _wrap(tool_calls) if tool_calls is not None else _wrap(_result()))
    monkeypatch.setattr(evaluate, "evaluate_analyzer_prompt", _wrap(analyzer_prompt) if analyzer_prompt is not None else _wrap(_result()))


# ── resolve_gate_targets ──────────────────────────────────────────────

def test_resolve_gate_targets_defaults_to_skill_and_proposal():
    assert evaluate.resolve_gate_targets() == ["skill", "proposal"]


def test_resolve_gate_targets_env_override(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_TARGETS", "skill,tool_calls")
    assert evaluate.resolve_gate_targets() == ["skill", "tool_calls"]


def test_resolve_gate_targets_empty_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_TARGETS", "")
    assert evaluate.resolve_gate_targets() == ["skill", "proposal"]


def test_resolve_gate_targets_unknown_name_raises(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_TARGETS", "skill,bogus")
    with pytest.raises(ValueError, match="Unknown gate target 'bogus'"):
        evaluate.resolve_gate_targets()


def test_resolve_gate_targets_explicit_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_TARGETS", "proposal")
    assert evaluate.resolve_gate_targets(explicit=["skill"]) == ["skill"]


def test_resolve_gate_targets_explicit_empty_falls_back_to_default():
    assert evaluate.resolve_gate_targets(explicit=[]) == ["skill", "proposal"]


# ── Per-target strictness override ────────────────────────────────────

def test_per_target_gate_strictness_override_relaxes_only_that_target(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_STRICTNESS_TOOL_CALLS", "majority")
    assert evaluate.resolve_gate_strictness("improve_existing", target="tool_calls") == "majority"
    assert evaluate.resolve_gate_strictness("improve_existing", target="skill") == "strict"
    assert evaluate.resolve_gate_strictness("improve_existing") == "strict"


def test_target_override_wins_over_proposal_type_override(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_STRICTNESS_SKILL", "majority")
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_STRICTNESS_IMPROVE_EXISTING", "strict")
    assert evaluate.resolve_gate_strictness("improve_existing", target="skill") == "majority"


def test_tool_calls_majority_override_makes_a_failure_non_blocking(monkeypatch, isolated_history):
    """A single failing tool_calls result under majority (2 of 3 pass) must not block."""
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_STRICTNESS_TOOL_CALLS", "majority")
    _stub_targets(monkeypatch, tool_calls=_result(passed=False))

    def _two_results(*a, **k):
        return [_result(passed=False), _result(passed=True), _result(passed=True)]

    monkeypatch.setattr(evaluate, "evaluate_tool_calls", _two_results)

    p = _proposal(session_ids=["s1"])
    _, combined, gate_passed = evaluate.evaluate_and_record(p, gate_targets=["tool_calls"])
    assert gate_passed is True
    assert combined.passed is True


# ── Gate decision semantics ───────────────────────────────────────────

def test_failing_proposal_target_blocks_the_and_gate(monkeypatch, isolated_history):
    """Skill text passes but the proposal document fails -> overall gate blocks."""
    _stub_targets(monkeypatch, skill=_result(passed=True), proposal=_result(passed=False))
    p = _proposal()

    _, combined, gate_passed = evaluate.evaluate_and_record(p)

    assert gate_passed is False
    assert combined.passed is False


def test_apply_proposal_blocks_when_proposal_target_fails(monkeypatch, isolated_history):
    _stub_targets(monkeypatch, skill=_result(passed=True), proposal=_result(passed=False))
    p = _proposal()

    result = apply_proposal(p, min_confidence=0.5)

    assert result["can_apply"] is False
    assert p.status == ProposalStatus.PROPOSED
    # Both targets' results surface in the report
    assert len(result["evaluation_results"]) == 2


def test_apply_proposal_requires_all_default_targets(monkeypatch, isolated_history):
    _stub_targets(monkeypatch, skill=_result(passed=True), proposal=_result(passed=True))
    p = _proposal()

    result = apply_proposal(p, min_confidence=0.5)

    assert result["can_apply"] is True
    assert p.status == ProposalStatus.APPLIED


def test_opted_in_tool_calls_target_failure_blocks(monkeypatch, isolated_history):
    _stub_targets(monkeypatch, tool_calls=_result(passed=False))
    p = _proposal(session_ids=["s1"])

    _, combined, gate_passed = evaluate.evaluate_and_record(p, gate_targets=["skill", "tool_calls"])

    assert gate_passed is False
    assert combined.passed is False


# ── No-data skip semantics ────────────────────────────────────────────

def test_no_session_ids_skips_session_based_targets_without_blocking(monkeypatch, isolated_history):
    p = _proposal(session_ids=[])

    _, _, gate_passed = evaluate.evaluate_and_record(p, gate_targets=["tool_calls", "analyzer_prompt"])

    assert gate_passed is True
    # Nothing ran, so nothing was recorded
    all_entries = evaluate._read_all_entries(isolated_history)
    assert all_entries == []


def test_no_session_ids_still_gates_skill_and_proposal(monkeypatch, isolated_history):
    _stub_targets(monkeypatch, proposal=_result(passed=False))
    p = _proposal(session_ids=[])

    _, combined, gate_passed = evaluate.evaluate_and_record(p)

    assert gate_passed is False
    assert combined.passed is False


# ── Per-target history entries ────────────────────────────────────────

def test_one_combined_entry_per_gating_target(monkeypatch, isolated_history):
    _stub_targets(monkeypatch)
    p = _proposal(session_ids=["s1"])

    evaluate.evaluate_and_record(p, gate_targets=["skill", "proposal", "tool_calls"])

    skill_entries = evaluate.read_history("skill:test-skill")
    assert len(skill_entries) == 1
    assert skill_entries[0]["evaluator_name"] == "gate"
    assert skill_entries[0]["kind"] == "skill_text"

    proposal_entries = evaluate.read_history("proposal:fixture-001")
    assert len(proposal_entries) == 1
    assert proposal_entries[0]["kind"] == "proposal"

    tool_entries = evaluate.read_history("tool_calls:s1")
    assert len(tool_entries) == 1
    assert tool_entries[0]["kind"] == "tool_calls"


def test_transport_failure_flagged_only_on_the_affected_targets_entry(monkeypatch, isolated_history):
    _stub_targets(monkeypatch, skill=_result(passed=True), proposal=_result(passed=False, transport_failure=True))
    p = _proposal()

    _, _, gate_passed = evaluate.evaluate_and_record(p)

    assert gate_passed is False
    skill_entry, = evaluate.read_history("skill:test-skill")
    assert "transport_failure" not in skill_entry
    proposal_entry, = evaluate.read_history("proposal:fixture-001")
    assert proposal_entry["transport_failure"] is True


def test_skill_entry_records_sizes_others_do_not(monkeypatch, isolated_history):
    _stub_targets(monkeypatch)
    p = _proposal()

    evaluate.evaluate_and_record(p)

    skill_entry, = evaluate.read_history("skill:test-skill")
    assert "content_size" in skill_entry
    proposal_entry, = evaluate.read_history("proposal:fixture-001")
    assert "content_size" not in proposal_entry
    assert "baseline_size" not in proposal_entry


def test_combined_feedback_prefixes_each_target(monkeypatch, isolated_history):
    _stub_targets(monkeypatch, skill=_result(passed=True, name="det"), proposal=_result(passed=False, name="judge"))
    p = _proposal()

    _, combined, _ = evaluate.evaluate_and_record(p)

    assert "skill/det=pass" in combined.feedback
    assert "proposal/judge=fail" in combined.feedback


# ── migrate_proposal_history kind filter ──────────────────────────────

def test_migrate_proposal_history_skips_proposal_kind_entries(monkeypatch, isolated_history):
    """A proposal-document entry (kind=proposal) is a separate lineage and must not be
    folded into the skill lineage by the create_new migration -- only legacy untyped
    entries (what the migration exists to reconnect) and skill_text entries migrate."""
    evaluate.append_history("proposal:abc-123",
                            EvalResult(score=0.5, passed=False, feedback="doc", evaluator_name="gate"),
                            kind="proposal")
    evaluate.append_history("proposal:abc-123",
                            EvalResult(score=0.6, passed=True, feedback="legacy", evaluator_name="gate"))
    evaluate.append_history("skill:other",
                            EvalResult(score=0.7, passed=True, feedback="unrelated", evaluator_name="gate"))

    migrated = evaluate.migrate_proposal_history("abc-123", "skill:new-name")

    assert migrated == 1
    assert [e["target"] for e in evaluate.read_history("skill:new-name")] == ["skill:new-name"]
    assert [e["feedback"] for e in evaluate.read_history("skill:new-name")] == ["legacy"]
    # The proposal-document entry stays put
    remaining = [e["kind"] for e in evaluate.read_history("proposal:abc-123")]
    assert remaining == ["proposal"]


def test_migrate_proposal_history_migrates_skill_text_kind(monkeypatch, isolated_history):
    evaluate.append_history("proposal:xyz-789",
                            EvalResult(score=0.8, passed=True, feedback="text", evaluator_name="gate"),
                            kind="skill_text")
    evaluate.append_history("proposal:xyz-789",
                            EvalResult(score=0.9, passed=True, feedback="doc", evaluator_name="gate"),
                            kind="proposal")

    migrated = evaluate.migrate_proposal_history("xyz-789", "skill:new-name")

    assert migrated == 1
    assert [e["feedback"] for e in evaluate.read_history("skill:new-name")] == ["text"]
