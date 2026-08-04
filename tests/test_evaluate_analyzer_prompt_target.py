"""Tests for scripts/evaluate.py's evaluate_analyzer_prompt() target (U3, R5)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import EvalResult, evaluate_analyzer_prompt


@pytest.fixture(autouse=True)
def stub_run_evaluators(monkeypatch):
    captured = {}

    def fake_run_evaluators(content, target, context=None):
        captured["content"] = content
        captured["target"] = target
        captured["context"] = context or {}
        return [EvalResult(score=0.9, passed=True, feedback="ok", evaluator_name="stub")]

    monkeypatch.setattr(evaluate, "run_evaluators", fake_run_evaluators)
    return captured


@pytest.fixture
def stub_fetch_session_messages(monkeypatch):
    def _stub(messages):
        monkeypatch.setattr(evaluate, "_fetch_session_messages", lambda session_id: messages)
    return _stub


@pytest.fixture
def stub_fetch_proposal_markdown(monkeypatch):
    def _stub(markdown):
        monkeypatch.setattr(evaluate, "_fetch_proposal_markdown", lambda proposal_id: markdown)
    return _stub


def test_evaluate_analyzer_prompt_combines_session_and_proposal(
    stub_run_evaluators, stub_fetch_session_messages, stub_fetch_proposal_markdown
):
    stub_fetch_session_messages([
        {"role": "user", "content": "Fix the bug in login"},
        {"role": "assistant", "content": "I'll investigate the issue."},
    ])
    stub_fetch_proposal_markdown("# Proposal\n\nImprove login error handling.")

    results = evaluate_analyzer_prompt("session-123", "proposal-456")

    assert stub_run_evaluators["target"] == "analyzer_prompt:session-123"
    assert "[SESSION MESSAGES]" in stub_run_evaluators["content"]
    assert "[PROPOSAL]" in stub_run_evaluators["content"]
    assert "Fix the bug" in stub_run_evaluators["content"]
    assert "Improve login" in stub_run_evaluators["content"]
    assert results[0].passed is True


def test_evaluate_analyzer_prompt_truncates_long_messages(
    stub_run_evaluators, stub_fetch_session_messages, stub_fetch_proposal_markdown
):
    long_content = "x" * 500
    stub_fetch_session_messages([
        {"role": "user", "content": long_content},
    ])
    stub_fetch_proposal_markdown("# Proposal")

    evaluate_analyzer_prompt("session-123", "proposal-456")

    # Content should be truncated to 300 chars + "..."
    assert "x" * 300 in stub_run_evaluators["content"]
    assert "..." in stub_run_evaluators["content"]


def test_evaluate_analyzer_prompt_handles_missing_proposal(
    stub_run_evaluators, stub_fetch_session_messages, stub_fetch_proposal_markdown
):
    stub_fetch_session_messages([
        {"role": "user", "content": "test"},
    ])
    stub_fetch_proposal_markdown(None)

    results = evaluate_analyzer_prompt("session-123", "proposal-456")

    assert "(proposal not found)" in stub_run_evaluators["content"]
    assert results[0].passed is True


def test_evaluate_analyzer_prompt_handles_no_messages(
    stub_run_evaluators, stub_fetch_session_messages, stub_fetch_proposal_markdown
):
    stub_fetch_session_messages([])
    stub_fetch_proposal_markdown("# Proposal")

    results = evaluate_analyzer_prompt("session-123", "proposal-456")

    assert "(no messages)" in stub_run_evaluators["content"]
    assert results[0].passed is True


def test_evaluate_analyzer_prompt_does_not_modify_evaluate_skill_text(
    stub_run_evaluators, stub_fetch_session_messages, stub_fetch_proposal_markdown
):
    """KTD1: evaluate_analyzer_prompt is a sibling, not a branch in evaluate_skill_text."""
    stub_fetch_session_messages([{"role": "user", "content": "test"}])
    stub_fetch_proposal_markdown("# Proposal")

    evaluate_analyzer_prompt("session-123", "proposal-456")
    assert stub_run_evaluators["target"].startswith("analyzer_prompt:")
