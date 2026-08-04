"""Tests for scripts/evaluate.py's --eval-target CLI flag (U4, R5)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate


def test_cli_eval_target_proposal_requires_proposal_id(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--eval-target", "proposal"])

    with pytest.raises(SystemExit) as exc_info:
        evaluate.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "--proposal-id" in captured.err


def test_cli_eval_target_tool_calls_requires_session_id(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--eval-target", "tool_calls"])

    with pytest.raises(SystemExit) as exc_info:
        evaluate.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "--session-id" in captured.err


def test_cli_eval_target_analyzer_prompt_requires_both_ids(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--eval-target", "analyzer_prompt",
                                       "--session-id", "s1"])

    with pytest.raises(SystemExit) as exc_info:
        evaluate.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "--proposal-id" in captured.err


def test_cli_eval_target_proposal_runs_and_writes_history(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--eval-target", "proposal",
                                       "--proposal-id", "test-proposal"])

    # Stub the proposal loading
    class FakeProposal:
        proposal_id = "test-proposal"
        summary = "Test summary"
        rationale = "Test rationale"
        proposed_changes = []

    import proposal as proposal_module
    monkeypatch.setattr(proposal_module, "load_proposal", lambda pid: FakeProposal())

    # Stub the evaluation
    monkeypatch.setattr(evaluate, "evaluate_proposal", lambda p: [
        evaluate.EvalResult(score=0.8, passed=True, feedback="ok", evaluator_name="stub"),
    ])

    # Stub history path
    history_path = str(tmp_path / "eval_history.jsonl")
    monkeypatch.setattr(evaluate, "get_history_path", lambda: history_path)

    evaluate.main()

    captured = capsys.readouterr()
    assert "stub" in captured.out
    assert "0.80" in captured.out
    assert "History entry written" in captured.out

    # Verify history was written
    entries = evaluate._read_all_entries(history_path)
    assert len(entries) == 1
    assert entries[0]["target"] == "proposal:test-proposal"


def test_cli_eval_target_tool_calls_runs_and_writes_history(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--eval-target", "tool_calls",
                                       "--session-id", "test-session"])

    # Stub the evaluation
    monkeypatch.setattr(evaluate, "evaluate_tool_calls", lambda sid: [
        evaluate.EvalResult(score=0.7, passed=True, feedback="ok", evaluator_name="stub"),
    ])

    # Stub history path
    history_path = str(tmp_path / "eval_history.jsonl")
    monkeypatch.setattr(evaluate, "get_history_path", lambda: history_path)

    evaluate.main()

    captured = capsys.readouterr()
    assert "stub" in captured.out
    assert "0.70" in captured.out
    assert "History entry written" in captured.out

    # Verify history was written
    entries = evaluate._read_all_entries(history_path)
    assert len(entries) == 1
    assert entries[0]["target"] == "tool_calls:test-session"


def test_cli_eval_target_analyzer_prompt_runs_and_writes_history(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--eval-target", "analyzer_prompt",
                                       "--session-id", "test-session",
                                       "--proposal-id", "test-proposal"])

    # Stub the evaluation
    monkeypatch.setattr(evaluate, "evaluate_analyzer_prompt", lambda sid, pid: [
        evaluate.EvalResult(score=0.9, passed=True, feedback="ok", evaluator_name="stub"),
    ])

    # Stub history path
    history_path = str(tmp_path / "eval_history.jsonl")
    monkeypatch.setattr(evaluate, "get_history_path", lambda: history_path)

    evaluate.main()

    captured = capsys.readouterr()
    assert "stub" in captured.out
    assert "0.90" in captured.out
    assert "History entry written" in captured.out

    # Verify history was written
    entries = evaluate._read_all_entries(history_path)
    assert len(entries) == 1
    assert entries[0]["target"] == "analyzer_prompt:test-session"


def _seed_cli_proposal(proposals_dir, proposal_id, status):
    import proposal as proposal_module
    from proposal import ProposalStatus, ProposalType, ProposedChange, SkillEvolutionProposal, save_proposal

    proposal = SkillEvolutionProposal(
        proposal_id=proposal_id,
        type=ProposalType.IMPROVE_EXISTING,
        target_skill=f"{proposal_id}-skill",
        confidence=0.9,
        summary=f"Improve {proposal_id}",
        rationale="Fixture rationale.",
        status=status,
        proposed_changes=[ProposedChange(field="body", new_value="Body content.")],
    )
    save_proposal(proposal, directory=proposals_dir)
    return proposal


def _stub_retroactive_main(monkeypatch, tmp_path):
    import proposal as proposal_module

    proposals_dir = str(tmp_path / "proposals")
    os.makedirs(proposals_dir, exist_ok=True)
    monkeypatch.setattr(proposal_module, "get_proposals_dir", lambda: proposals_dir)
    monkeypatch.setattr(evaluate, "get_history_path", lambda: str(tmp_path / "eval_history.jsonl"))
    monkeypatch.setattr(
        evaluate, "run_evaluators",
        lambda content, target, context=None: [
            evaluate.EvalResult(score=0.9, passed=True, feedback="stub", evaluator_name="stub"),
        ],
    )
    return proposals_dir


def test_cli_retroactive_defaults_to_proposed_only(monkeypatch, capsys, tmp_path):
    from proposal import ProposalStatus

    proposals_dir = _stub_retroactive_main(monkeypatch, tmp_path)
    _seed_cli_proposal(proposals_dir, "cli-proposed", ProposalStatus.PROPOSED)
    _seed_cli_proposal(proposals_dir, "cli-rejected", ProposalStatus.REJECTED)

    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--retroactive"])
    evaluate.main()

    captured = capsys.readouterr()
    assert "cli-proposed" in captured.out
    assert "cli-rejected" not in captured.out


def test_cli_retroactive_all_statuses_includes_rejected_and_applied(monkeypatch, capsys, tmp_path):
    from proposal import ProposalStatus

    proposals_dir = _stub_retroactive_main(monkeypatch, tmp_path)
    _seed_cli_proposal(proposals_dir, "cli-proposed", ProposalStatus.PROPOSED)
    _seed_cli_proposal(proposals_dir, "cli-rejected", ProposalStatus.REJECTED)
    _seed_cli_proposal(proposals_dir, "cli-applied", ProposalStatus.APPLIED)

    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--retroactive", "--all-statuses"])
    evaluate.main()

    captured = capsys.readouterr()
    assert "cli-proposed" in captured.out
    assert "cli-rejected" in captured.out
    assert "cli-applied" in captured.out
