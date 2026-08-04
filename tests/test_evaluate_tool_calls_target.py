"""Tests for scripts/evaluate.py's evaluate_tool_calls() target (U2, R5)."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import EvalResult, evaluate_tool_calls


@pytest.fixture(autouse=True)
def stub_run_evaluators(monkeypatch):
    captured = {}

    def fake_run_evaluators(content, target, context=None):
        captured["content"] = content
        captured["target"] = target
        captured["context"] = context or {}
        return [EvalResult(score=0.8, passed=True, feedback="ok", evaluator_name="stub")]

    monkeypatch.setattr(evaluate, "run_evaluators", fake_run_evaluators)
    return captured


def test_evaluate_tool_calls_extracts_tool_calls_from_messages(stub_run_evaluators):
    messages = [
        {
            "role": "assistant",
            "content": "I'll search for the file.",
            "tool_calls": [
                {"name": "read_file", "arguments": {"path": "/tmp/test.py"}, "result": "file contents"},
            ],
        },
    ]
    results = evaluate_tool_calls(messages)

    assert stub_run_evaluators["target"] == "tool_calls:unknown-session"
    assert "read_file" in stub_run_evaluators["content"]
    assert results[0].passed is True


def test_evaluate_tool_calls_with_session_id_queries_db(monkeypatch, stub_run_evaluators):
    """When given a session_id string, it should query state.db."""
    fake_messages = [
        {
            "role": "assistant",
            "content": "Searching...",
            "tool_calls": [
                {"name": "grep", "arguments": {"pattern": "test"}, "result": "found 3 matches"},
            ],
        },
    ]

    def fake_fetch(session_id):
        assert session_id == "test-session-123"
        return fake_messages

    monkeypatch.setattr(evaluate, "_fetch_session_messages", fake_fetch)

    results = evaluate_tool_calls("test-session-123")

    assert stub_run_evaluators["target"] == "tool_calls:test-session-123"
    assert "grep" in stub_run_evaluators["content"]
    assert results[0].passed is True


def test_evaluate_tool_calls_no_tool_calls_returns_passing(stub_run_evaluators):
    """Edge: a session with no tool_calls returns a passing 'no data' result."""
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    results = evaluate_tool_calls(messages)

    assert "(no tool calls recorded" in stub_run_evaluators["content"]
    assert stub_run_evaluators["target"] == "tool_calls:unknown-session"
    assert results[0].passed is True


def test_evaluate_tool_calls_handles_malformed_json(stub_run_evaluators):
    """Edge: malformed tool_calls JSON is handled defensively."""
    messages = [
        {
            "role": "assistant",
            "content": "test",
            "tool_calls": "not valid json {{{",
        },
    ]
    results = evaluate_tool_calls(messages)

    assert results[0].passed is True


def test_evaluate_tool_calls_caps_result_snippets(stub_run_evaluators):
    """Result snippets are capped at 200 chars to control cost."""
    long_result = "x" * 500
    messages = [
        {
            "role": "assistant",
            "content": "test",
            "tool_calls": [
                {"name": "read_file", "arguments": {}, "result": long_result},
            ],
        },
    ]
    evaluate_tool_calls(messages)

    # The content should contain the truncated result
    assert "x" in stub_run_evaluators["content"]
    # ... but not the full 500 chars
    assert len(stub_run_evaluators["content"]) < 1000


def test_evaluate_tool_calls_does_not_modify_evaluate_skill_text(stub_run_evaluators):
    """KTD1: evaluate_tool_calls is a sibling, not a branch in evaluate_skill_text."""
    messages = [
        {
            "role": "assistant",
            "content": "test",
            "tool_calls": [{"name": "grep", "arguments": {}, "result": "ok"}],
        },
    ]
    evaluate_tool_calls(messages)
    assert stub_run_evaluators["target"].startswith("tool_calls:")
