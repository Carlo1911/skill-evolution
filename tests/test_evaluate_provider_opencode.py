"""Tests for the OpenCode Zen provider branch of evaluate.py's adapter layer.

OpenCode Zen is an OpenAI-compatible gateway (https://opencode.ai/zen/v1), so the
request/response handling mirrors the Ollama caller; what differs is the bearer-token
auth and the hosted default model.

Note the two distinct catalogues: Zen (`/zen/v1/models`) carries `big-pickle` and the
`*-free` variants; the Go subscription tier (`/zen/go/v1/models`) is a different, smaller
list that does NOT include `big-pickle`.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import ProviderError, call_provider, resolve_provider


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "SKILL_EVOLUTION_PROVIDER",
        "SKILL_EVOLUTION_OPENCODE_BASE_URL",
        "SKILL_EVOLUTION_OPENCODE_MODEL",
        "OPENCODE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    for key in list(os.environ):
        if key.startswith("SKILL_EVOLUTION_") and key.endswith("_PROVIDER"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def captured_post(monkeypatch):
    """Stub _post_json so no HTTP leaves the machine; capture what would have been sent."""
    captured = {}

    def fake_post_json(url, body, headers, timeout, provider_label):
        captured.update(url=url, body=body, headers=headers,
                        timeout=timeout, provider_label=provider_label)
        return {"choices": [{"message": {"content": "stubbed reply"}}]}

    monkeypatch.setattr(evaluate, "_post_json", fake_post_json)
    return captured


def test_opencode_is_a_registered_provider():
    assert "opencode" in evaluate.PROVIDER_CALLERS


def test_opencode_resolvable_globally_and_per_evaluator(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "opencode")
    assert resolve_provider() == "opencode"

    monkeypatch.setenv("SKILL_EVOLUTION_LLM_JUDGE_PROVIDER", "opencode")
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "claude")
    assert resolve_provider(evaluator_name="llm_judge") == "opencode"


def test_defaults_to_zen_endpoint_and_big_pickle(monkeypatch, captured_post):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")

    assert evaluate._call_opencode("hello") == "stubbed reply"
    assert captured_post["url"] == "https://opencode.ai/zen/v1/chat/completions"
    assert captured_post["body"]["model"] == "big-pickle"
    assert captured_post["body"]["messages"] == [{"role": "user", "content": "hello"}]


def test_sends_bearer_token_auth(monkeypatch, captured_post):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")

    evaluate._call_opencode("hello")

    auth = {k.lower(): v for k, v in captured_post["headers"].items()}["authorization"]
    assert auth == "Bearer test-key"


def test_base_url_and_model_are_overridable(monkeypatch, captured_post):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setenv("SKILL_EVOLUTION_OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1/")
    monkeypatch.setenv("SKILL_EVOLUTION_OPENCODE_MODEL", "deepseek-v4-flash")

    evaluate._call_opencode("hello")

    # trailing slash in the override must not produce a doubled separator
    assert captured_post["url"] == "https://opencode.ai/zen/go/v1/chat/completions"
    assert captured_post["body"]["model"] == "deepseek-v4-flash"


def test_missing_api_key_fails_closed_with_actionable_message(monkeypatch):
    monkeypatch.setattr(evaluate, "_post_json",
                        lambda *a, **k: pytest.fail("must not attempt an unauthenticated call"))

    with pytest.raises(ProviderError, match="OPENCODE_API_KEY"):
        evaluate._call_opencode("hello")


def test_malformed_response_shape_raises_provider_error(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setattr(evaluate, "_post_json", lambda *a, **k: {"unexpected": "shape"})

    with pytest.raises(ProviderError, match="Unexpected OpenCode"):
        evaluate._call_opencode("hello")


def test_redaction_runs_before_the_opencode_request(monkeypatch, captured_post):
    """A secret in the prompt must never reach the hosted endpoint."""
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")

    call_provider("evaluate this: sk-ant-api-shouldnotleak", provider="opencode")

    sent = captured_post["body"]["messages"][0]["content"]
    assert "sk-ant-api-shouldnotleak" not in sent
    assert "[REDACTED]" in sent


def test_api_key_is_not_placed_in_the_url(monkeypatch, captured_post):
    """Credentials belong in the header, never in a query string."""
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")

    evaluate._call_opencode("hello")

    assert "test-key" not in captured_post["url"]
