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
