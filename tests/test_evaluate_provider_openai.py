"""Tests for the OpenAI provider branch of evaluate.py's adapter layer.

OpenAI's Chat Completions API is the OpenAI-compatible shape other branches
(`_call_ollama`, `_call_opencode`) already follow, so request/response handling
mirrors those; what differs is the default base URL/model and the env-var names.
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
        "SKILL_EVOLUTION_OPENAI_BASE_URL",
        "SKILL_EVOLUTION_OPENAI_MODEL",
        "OPENAI_API_KEY",
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


def test_openai_is_a_registered_provider():
    assert "openai" in evaluate.PROVIDER_CALLERS


def test_openai_resolvable_globally_and_per_evaluator(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "openai")
    assert resolve_provider() == "openai"

    monkeypatch.setenv("SKILL_EVOLUTION_LLM_JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "claude")
    assert resolve_provider(evaluator_name="llm_judge") == "openai"


def test_defaults_to_openai_endpoint_and_gpt4o(monkeypatch, captured_post):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    assert evaluate._call_openai("hello") == "stubbed reply"
    assert captured_post["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured_post["body"]["model"] == "gpt-4o"
    assert captured_post["body"]["messages"] == [{"role": "user", "content": "hello"}]


def test_sends_bearer_token_auth(monkeypatch, captured_post):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    evaluate._call_openai("hello")

    auth = {k.lower(): v for k, v in captured_post["headers"].items()}["authorization"]
    assert auth == "Bearer test-key"


def test_base_url_and_model_are_overridable(monkeypatch, captured_post):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SKILL_EVOLUTION_OPENAI_BASE_URL", "https://my-proxy.example.com/v1/")
    monkeypatch.setenv("SKILL_EVOLUTION_OPENAI_MODEL", "gpt-4o-mini")

    evaluate._call_openai("hello")

    # trailing slash in the override must not produce a doubled separator
    assert captured_post["url"] == "https://my-proxy.example.com/v1/chat/completions"
    assert captured_post["body"]["model"] == "gpt-4o-mini"


def test_missing_api_key_fails_closed_with_actionable_message(monkeypatch):
    monkeypatch.setattr(evaluate, "_post_json",
                        lambda *a, **k: pytest.fail("must not attempt an unauthenticated call"))

    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        evaluate._call_openai("hello")


def test_malformed_response_shape_raises_provider_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(evaluate, "_post_json", lambda *a, **k: {"unexpected": "shape"})

    with pytest.raises(ProviderError, match="Unexpected OpenAI"):
        evaluate._call_openai("hello")


def test_redaction_runs_before_the_openai_request(monkeypatch, captured_post):
    """A secret in the prompt must never reach the hosted endpoint."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    call_provider("evaluate this: sk-ant-api-shouldnotleak", provider="openai")

    sent = captured_post["body"]["messages"][0]["content"]
    assert "sk-ant-api-shouldnotleak" not in sent
    assert "[REDACTED]" in sent


def test_api_key_is_not_placed_in_the_url(monkeypatch, captured_post):
    """Credentials belong in the header, never in a query string."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    evaluate._call_openai("hello")

    assert "test-key" not in captured_post["url"]
