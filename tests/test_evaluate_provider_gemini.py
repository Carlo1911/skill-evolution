"""Tests for the Gemini provider branch of evaluate.py's adapter layer.

Gemini's `v1beta/models/{model}:generateContent` endpoint uses a different
request/response shape than the OpenAI-compatible callers: `contents[].parts[].text`
in, `candidates[].content.parts[].text` out, and header-based auth (`x-goog-api-key`)
rather than a bearer token.
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
        "SKILL_EVOLUTION_GEMINI_BASE_URL",
        "SKILL_EVOLUTION_GEMINI_MODEL",
        "GEMINI_API_KEY",
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
        return {"candidates": [{"content": {"parts": [{"text": "stubbed reply"}]}}]}

    monkeypatch.setattr(evaluate, "_post_json", fake_post_json)
    return captured


def test_gemini_is_a_registered_provider():
    assert "gemini" in evaluate.PROVIDER_CALLERS


def test_gemini_resolvable_globally_and_per_evaluator(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "gemini")
    assert resolve_provider() == "gemini"

    monkeypatch.setenv("SKILL_EVOLUTION_LLM_JUDGE_PROVIDER", "gemini")
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "claude")
    assert resolve_provider(evaluator_name="llm_judge") == "gemini"


def test_defaults_to_gemini_endpoint_and_flash_model(monkeypatch, captured_post):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    assert evaluate._call_gemini("hello") == "stubbed reply"
    assert captured_post["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    )


def test_sends_gemini_shaped_body(monkeypatch, captured_post):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    evaluate._call_gemini("hello")

    assert captured_post["body"] == {"contents": [{"parts": [{"text": "hello"}]}]}
    assert "messages" not in captured_post["body"]


def test_parses_gemini_shaped_response(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        evaluate, "_post_json",
        lambda *a, **k: {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
    )

    assert evaluate._call_gemini("hello") == "ok"


def test_handles_empty_candidates(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(evaluate, "_post_json", lambda *a, **k: {"candidates": []})

    with pytest.raises(ProviderError, match="Unexpected Gemini"):
        evaluate._call_gemini("hello")


def test_sends_x_goog_api_key_header(monkeypatch, captured_post):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    evaluate._call_gemini("hello")

    headers = {k.lower(): v for k, v in captured_post["headers"].items()}
    assert headers["x-goog-api-key"] == "test-key"
    assert "authorization" not in headers


def test_base_url_and_model_are_overridable(monkeypatch, captured_post):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("SKILL_EVOLUTION_GEMINI_BASE_URL", "https://my-proxy.example.com/")
    monkeypatch.setenv("SKILL_EVOLUTION_GEMINI_MODEL", "gemini-2.5-pro")

    evaluate._call_gemini("hello")

    # trailing slash in the override must not produce a doubled separator
    assert captured_post["url"] == (
        "https://my-proxy.example.com/v1beta/models/gemini-2.5-pro:generateContent"
    )


def test_missing_api_key_fails_closed_with_actionable_message(monkeypatch):
    monkeypatch.setattr(evaluate, "_post_json",
                        lambda *a, **k: pytest.fail("must not attempt an unauthenticated call"))

    with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
        evaluate._call_gemini("hello")


def test_malformed_response_shape_raises_provider_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(evaluate, "_post_json", lambda *a, **k: {"unexpected": "shape"})

    with pytest.raises(ProviderError, match="Unexpected Gemini"):
        evaluate._call_gemini("hello")


def test_redaction_runs_before_the_gemini_request(monkeypatch, captured_post):
    """A secret in the prompt must never reach the hosted endpoint."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    call_provider("evaluate this: sk-ant-api-shouldnotleak", provider="gemini")

    sent = captured_post["body"]["contents"][0]["parts"][0]["text"]
    assert "sk-ant-api-shouldnotleak" not in sent
    assert "[REDACTED]" in sent


def test_api_key_is_not_placed_in_the_url(monkeypatch, captured_post):
    """Credentials belong in the header, never in a query string."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    evaluate._call_gemini("hello")

    assert "test-key" not in captured_post["url"]
