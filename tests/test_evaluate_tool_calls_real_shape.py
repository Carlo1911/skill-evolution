"""evaluate_tool_calls() must read the tool-call shape Hermes actually stores (P0-5).

Found immediately after fixing P0-1, by running the now-working DB path against a real
session. The extraction reads `call["name"]` / `call["arguments"]` at the top level, but
`~/.hermes/state.db` stores OpenAI function-call objects:

    {"id": "call_00_...", "call_id": ..., "type": "function",
     "function": {"name": "cronjob", "arguments": "{\\"action\\": \\"list\\"}"}}

...so on a real 281-message session with 133 tool-call messages, all 137 extracted snippets
came out as `{"name": "", "arguments": {}, "result": ""}` and the judge was handed 14 bytes.

Same root cause as P0-1 one layer up: the existing tests inject messages in the *documented*
flat shape, which no real row uses. Both shapes are supported here -- the nested one because
it is what Hermes writes, the flat one because it is what the tests and any other host may
provide.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate

NESTED = [{
    "id": "call_00_abc",
    "call_id": "call_00_abc",
    "type": "function",
    "function": {"name": "cronjob", "arguments": '{"action": "list"}'},
}]

FLAT = [{"name": "cronjob", "arguments": {"action": "list"}, "result": "3 jobs"}]


def _content_seen_by_the_judge(messages, monkeypatch):
    """Capture the text evaluate_tool_calls() hands to the evaluators."""
    seen = {}

    def fake_run(content, target, context=None):
        seen["content"] = content
        seen["target"] = target
        return [evaluate.EvalResult(score=1.0, passed=True, feedback="ok",
                                    evaluator_name="stub")]

    monkeypatch.setattr(evaluate, "run_evaluators", fake_run)
    evaluate.evaluate_tool_calls(messages)
    return seen


def test_nested_openai_function_shape_is_extracted(monkeypatch):
    """The shape Hermes actually stores. This is the regression."""
    seen = _content_seen_by_the_judge(
        [{"session_id": "s1", "role": "assistant", "tool_calls": json.dumps(NESTED)}],
        monkeypatch,
    )
    assert "cronjob" in seen["content"], f"tool name lost: {seen['content']}"
    assert "list" in seen["content"], f"arguments lost: {seen['content']}"


def test_flat_documented_shape_still_works(monkeypatch):
    """Don't regress the shape the existing tests and other hosts may use."""
    seen = _content_seen_by_the_judge(
        [{"session_id": "s1", "role": "assistant", "tool_calls": json.dumps(FLAT)}],
        monkeypatch,
    )
    assert "cronjob" in seen["content"]
    assert "3 jobs" in seen["content"]


def test_a_call_with_no_recoverable_name_is_skipped(monkeypatch):
    """An unusable entry must not pad the payload with empty snippets -- that is exactly
    what made the real session look like it had 137 tool calls and no content."""
    seen = _content_seen_by_the_judge(
        [{"session_id": "s1", "role": "assistant",
          "tool_calls": json.dumps([{"id": "x", "type": "function"}])}],
        monkeypatch,
    )
    assert '"name": ""' not in seen["content"]


def test_arguments_given_as_a_json_string_are_not_double_encoded(monkeypatch):
    """Hermes stores `arguments` as a JSON *string*; passing it through verbatim would
    reach the judge as escaped noise."""
    seen = _content_seen_by_the_judge(
        [{"session_id": "s1", "role": "assistant", "tool_calls": json.dumps(NESTED)}],
        monkeypatch,
    )
    assert "\\\"action\\\"" not in seen["content"]
