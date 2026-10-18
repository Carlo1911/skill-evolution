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
        "SKILL_EVOLUTION_OPENCODE_ZEN_BASE_URL",
        "SKILL_EVOLUTION_OPENCODE_ZEN_MODEL",
        "SKILL_EVOLUTION_OPENCODE_GO_BASE_URL",
        "SKILL_EVOLUTION_OPENCODE_GO_MODEL",
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
    assert "opencode_zen" in evaluate.PROVIDER_CALLERS
    assert "opencode_go" in evaluate.PROVIDER_CALLERS


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


def test_opencode_is_legacy_alias_of_zen(monkeypatch, captured_post):
    """provider=opencode keeps byte-for-byte Zen behavior (backward compat)."""
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")

    evaluate._call_opencode("hello")
    opencode_url = captured_post["url"]
    opencode_model = captured_post["body"]["model"]

    evaluate._call_opencode_zen("hello")

    assert captured_post["url"] == opencode_url == "https://opencode.ai/zen/v1/chat/completions"
    assert captured_post["body"]["model"] == opencode_model == "big-pickle"


def test_zen_defaults_to_zen_endpoint_and_big_pickle(monkeypatch, captured_post):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")

    assert evaluate._call_opencode_zen("hello") == "stubbed reply"
    assert captured_post["url"] == "https://opencode.ai/zen/v1/chat/completions"
    assert captured_post["body"]["model"] == "big-pickle"


def test_go_defaults_to_go_endpoint_without_big_pickle(monkeypatch, captured_post):
    """The Go catalogue has no big-pickle; the default must be a Go model id (checked 2026-10-18)."""
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")

    assert evaluate._call_opencode_go("hello") == "stubbed reply"
    assert captured_post["url"] == "https://opencode.ai/zen/go/v1/chat/completions"
    assert captured_post["body"]["model"] == "deepseek-v4.1-flash"
    assert captured_post["provider_label"] == "OpenCode Go"


def test_go_sends_a_stable_session_header(monkeypatch, captured_post):
    """Go refuses requests without x-opencode-session (HTTP 400 MissingSessionID)."""
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")

    evaluate._call_opencode_go("hello")
    first = captured_post["headers"]["x-opencode-session"]
    evaluate._call_opencode_go("again")

    assert first and captured_post["headers"]["x-opencode-session"] == first
    assert captured_post["headers"]["authorization"] == "Bearer test-key"


def test_zen_sends_no_session_header(monkeypatch, captured_post):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    evaluate._call_opencode_zen("hello")
    assert "x-opencode-session" not in captured_post["headers"]


def test_zen_tier_env_vars_win_over_legacy_generic(monkeypatch, captured_post):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setenv("SKILL_EVOLUTION_OPENCODE_BASE_URL", "https://legacy.example.com/v1")
    monkeypatch.setenv("SKILL_EVOLUTION_OPENCODE_MODEL", "legacy-model")
    monkeypatch.setenv("SKILL_EVOLUTION_OPENCODE_ZEN_BASE_URL", "https://zen.example.com/v1/")
    monkeypatch.setenv("SKILL_EVOLUTION_OPENCODE_ZEN_MODEL", "zen-model")

    evaluate._call_opencode_zen("hello")

    assert captured_post["url"] == "https://zen.example.com/v1/chat/completions"
    assert captured_post["body"]["model"] == "zen-model"


def test_go_ignores_legacy_generic_vars(monkeypatch, captured_post):
    """A Zen-pointed generic override must never silently redirect the Go tier."""
    monkeypatch.setenv("OPENCODE_API_KEY", "test-key")
    monkeypatch.setenv("SKILL_EVOLUTION_OPENCODE_BASE_URL", "https://opencode.ai/zen/v1")
    monkeypatch.setenv("SKILL_EVOLUTION_OPENCODE_MODEL", "big-pickle")
    monkeypatch.setenv("SKILL_EVOLUTION_OPENCODE_GO_MODEL", "mimo-v2.5")

    evaluate._call_opencode_go("hello")

    assert captured_post["url"] == "https://opencode.ai/zen/go/v1/chat/completions"
    assert captured_post["body"]["model"] == "mimo-v2.5"


def test_chain_normalizes_hyphen_to_underscore(monkeypatch):
    from evaluate import resolve_provider_chain
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "claude, opencode-go, opencode-zen")
    assert resolve_provider_chain() == ["claude", "opencode_go", "opencode_zen"]


def test_zen_go_chain_falls_back_in_order(monkeypatch):
    calls = []

    def failing_zen(prompt, timeout=60):
        calls.append("zen")
        raise ProviderError("zen down")

    def succeeding_go(prompt, timeout=60):
        calls.append("go")
        return "go-success"

    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "opencode_zen", failing_zen)
    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "opencode_go", succeeding_go)

    assert call_provider("hi", provider="opencode_zen,opencode_go") == "go-success"
    assert calls == ["zen", "go"]
