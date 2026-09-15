"""Tests for scripts/evaluate.py's provider adapter layer (U3)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import ProviderError, call_provider, redact_secrets, resolve_provider, resolve_provider_chain, resolve_provider_timeout


@pytest.fixture(autouse=True)
def clean_provider_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("SKILL_EVOLUTION_") and key.endswith("_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("SKILL_EVOLUTION_PROVIDER", raising=False)


def test_global_provider_used_when_no_override(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "ollama")
    assert resolve_provider(evaluator_name="llm_judge") == "ollama"


def test_default_provider_is_claude_when_nothing_set():
    assert resolve_provider() == "claude"


def test_per_evaluator_override_takes_precedence(monkeypatch):
    """Covers AE2: llm_judge overridden to a different provider than the global default."""
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "claude")
    monkeypatch.setenv("SKILL_EVOLUTION_LLM_JUDGE_PROVIDER", "ollama")

    assert resolve_provider(evaluator_name="llm_judge") == "ollama"
    assert resolve_provider(evaluator_name="regression") == "claude"


def test_explicit_provider_arg_wins_over_everything(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "claude")
    monkeypatch.setenv("SKILL_EVOLUTION_LLM_JUDGE_PROVIDER", "ollama")
    assert resolve_provider(provider="ollama", evaluator_name="llm_judge") == "ollama"


def test_redact_secrets_replaces_known_patterns():
    text = "here is a key sk-ant-api-abc123 please use it"
    redacted = redact_secrets(text)
    assert "sk-ant-api" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_secrets_removes_the_full_secret_value_not_just_the_marker():
    """A marker-only substring replace leaves the rest of the real key intact."""
    secret_suffix = "abcdefghijklmnopqrstuvwxyz0123456789"
    redacted = redact_secrets(f"here is a key sk-ant-api-{secret_suffix} please use it")
    assert secret_suffix not in redacted
    assert "[REDACTED]" in redacted


def test_redact_secrets_handles_key_value_shaped_secrets():
    redacted = redact_secrets("ANTHROPIC_API_KEY=sk-ant-api03-realvaluehere123")
    assert "sk-ant-api03-realvaluehere123" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_secrets_preserves_unrelated_lines():
    redacted = redact_secrets("normal line one\nsecret sk-ant-api-XXXX here\nnormal line two")
    assert "normal line one" in redacted
    assert "normal line two" in redacted
    assert "XXXX" not in redacted


def test_redaction_runs_before_request_construction(monkeypatch):
    captured = {}

    def fake_caller(prompt, timeout=60):
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "claude", fake_caller)

    secret_prompt = "evaluate this: sk-ant-api-shouldnotleak"
    call_provider(secret_prompt, provider="claude")

    assert "sk-ant-api-shouldnotleak" not in captured["prompt"]
    assert "[REDACTED]" in captured["prompt"]


def test_provider_selection_resolves_per_evaluator_through_call_provider(monkeypatch):
    calls = []

    def claude_caller(prompt, timeout=60):
        calls.append("claude")
        return "claude-response"

    def ollama_caller(prompt, timeout=60):
        calls.append("ollama")
        return "ollama-response"

    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "claude", claude_caller)
    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "ollama", ollama_caller)
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "claude")
    monkeypatch.setenv("SKILL_EVOLUTION_LLM_JUDGE_PROVIDER", "ollama")

    result = call_provider("hi", evaluator_name="llm_judge")
    assert result == "ollama-response"
    assert calls == ["ollama"]


def _capture_request(monkeypatch, response=b'{"ok": true}'):
    """Capture the urllib Request _post_json builds, without any network access."""
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return response

    def fake_urlopen(req, timeout=None):
        captured["request"] = req
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(evaluate.urllib.request, "urlopen", fake_urlopen)
    return captured


def test_post_json_sends_a_descriptive_user_agent(monkeypatch):
    """urllib's default UA ("Python-urllib/x.y") is blanket-blocked by some gateways.

    OpenCode Zen returns 403 for it while returning 200 for the identical request under
    any descriptive UA, so the client must identify itself by project name.
    """
    captured = _capture_request(monkeypatch)

    evaluate._post_json("https://example.invalid/v1/chat/completions",
                        {"model": "m"}, {}, 30, "Test")

    ua = captured["request"].get_header("User-agent") or ""
    assert "Python-urllib" not in ua
    assert "skill-evolution" in ua


def test_post_json_lets_callers_override_the_user_agent(monkeypatch):
    captured = _capture_request(monkeypatch)

    evaluate._post_json("https://example.invalid/v1/chat/completions",
                        {"model": "m"}, {"user-agent": "custom/1.0"}, 30, "Test")

    assert captured["request"].get_header("User-agent") == "custom/1.0"


def test_post_json_wraps_request_construction_value_error():
    """A scheme-less/malformed URL makes urllib.request.Request() raise ValueError
    before urlopen() is ever reached. Previously this escaped _post_json's
    try/except entirely (which only covered urlopen()) and propagated as a raw
    ValueError instead of ProviderError -- unreachable via the three original
    providers' hardcoded default URLs, but newly reachable once operator-set
    base-URL overrides (e.g. SKILL_EVOLUTION_GEMINI_BASE_URL) can be misconfigured."""
    with pytest.raises(ProviderError, match="Test provider call failed"):
        evaluate._post_json("not-a-valid-url-scheme", {"model": "m"}, {}, 30, "Test")


def test_post_json_wraps_json_decode_error(monkeypatch):
    """json.JSONDecodeError subclasses ValueError, so widening _post_json's except
    clause to ValueError (for the Request-construction fix above) also closes the
    pre-existing gap where a non-JSON response body escaped as an unhandled
    JSONDecodeError instead of failing closed as ProviderError."""
    captured = _capture_request(monkeypatch, response=b"<html>not json</html>")

    with pytest.raises(ProviderError, match="Test provider call failed"):
        evaluate._post_json("https://example.invalid/v1/chat/completions",
                            {"model": "m"}, {}, 30, "Test")


def test_unknown_provider_raises_clear_error():
    with pytest.raises(ProviderError, match="Unknown provider\\(s\\) 'bogus'"):
        call_provider("hi", provider="bogus")


def test_unknown_provider_error_lists_openai_and_gemini():
    with pytest.raises(ProviderError) as excinfo:
        call_provider("hi", provider="bogus")
    assert "gemini" in str(excinfo.value)
    assert "openai" in str(excinfo.value)


def test_provider_callers_has_five_registered_providers():
    assert set(evaluate.PROVIDER_CALLERS) == {"claude", "ollama", "opencode", "opencode_zen",
                                              "opencode_go", "openai", "gemini"}


def test_redact_secrets_removes_gemini_env_var():
    redacted = redact_secrets("GEMINI_API_KEY=AIzaSyRealValueGoesHere1234567890")
    assert "AIzaSyRealValueGoesHere1234567890" not in redacted
    assert "[REDACTED]" in redacted


def test_provider_call_failure_raises_provider_error(monkeypatch):
    def failing_caller(prompt, timeout=60):
        raise ProviderError("simulated timeout")

    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "claude", failing_caller)

    with pytest.raises(ProviderError, match="simulated timeout"):
        call_provider("hi", provider="claude")


# ── Provider timeout knob ────────────────────────────────────────────


def test_resolve_provider_timeout_defaults_to_60(monkeypatch):
    monkeypatch.delenv("SKILL_EVOLUTION_PROVIDER_TIMEOUT", raising=False)
    assert resolve_provider_timeout() == 60


def test_resolve_provider_timeout_reads_env(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER_TIMEOUT", "120")
    assert resolve_provider_timeout() == 120


def test_resolve_provider_timeout_explicit_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER_TIMEOUT", "120")
    assert resolve_provider_timeout(45) == 45


def test_resolve_provider_timeout_malformed_env_falls_back_with_warning(monkeypatch, capsys):
    """Same posture as the other numeric env vars (P0-3): degrade, don't crash."""
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER_TIMEOUT", "abc")
    assert resolve_provider_timeout() == 60
    assert "SKILL_EVOLUTION_PROVIDER_TIMEOUT" in capsys.readouterr().err


def test_call_provider_passes_env_timeout_to_the_caller(monkeypatch):
    captured = {}

    def fake_caller(prompt, timeout=None):
        captured["timeout"] = timeout
        return "ok"

    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "claude", fake_caller)
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER_TIMEOUT", "180")

    call_provider("hi", provider="claude")
    assert captured["timeout"] == 180


def test_call_provider_explicit_timeout_beats_env(monkeypatch):
    captured = {}

    def fake_caller(prompt, timeout=None):
        captured["timeout"] = timeout
        return "ok"

    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "claude", fake_caller)
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER_TIMEOUT", "180")

    call_provider("hi", provider="claude", timeout=30)
    assert captured["timeout"] == 30


def test_call_provider_timeout_is_generic_across_providers(monkeypatch):
    """The knob applies to every provider, not just Ollama: the latency it exists for
    (local reasoning models burning output on chain-of-thought before the JSON) is a
    property of the model, not of one adapter."""
    captured = {}

    def fake_caller(prompt, timeout=None):
        captured.setdefault("timeouts", []).append(timeout)
        return "ok"

    for name in ("claude", "ollama", "opencode", "opencode_zen", "opencode_go", "openai", "gemini"):
        monkeypatch.setitem(evaluate.PROVIDER_CALLERS, name, fake_caller)
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER_TIMEOUT", "90")

    for name in ("claude", "ollama", "opencode", "opencode_zen", "opencode_go", "openai", "gemini"):
        call_provider("hi", provider=name)

    assert captured["timeouts"] == [90, 90, 90, 90, 90, 90, 90]


# ── Shared OpenAI-compatible caller (P3-3) ───────────────────────────


def test_openai_compatible_builds_chat_completions_url(monkeypatch):
    """The shared helper must not double a trailing slash on an overridden base URL."""
    captured = {}

    def fake_post_json(url, body, headers, timeout, provider_label):
        captured.update(url=url, body=body, headers=headers,
                        timeout=timeout, provider_label=provider_label)
        return {"choices": [{"message": {"content": "reply"}}]}

    monkeypatch.setattr(evaluate, "_post_json", fake_post_json)

    assert evaluate._call_openai_compatible(
        "hello", "https://proxy.example.com/v1/", "m", {"authorization": "Bearer k"},
        "Proxy", 30,
    ) == "reply"
    assert captured["url"] == "https://proxy.example.com/v1/chat/completions"
    assert captured["body"] == {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
    assert captured["headers"] == {"authorization": "Bearer k"}
    assert captured["timeout"] == 30
    assert captured["provider_label"] == "Proxy"


def test_openai_compatible_wraps_shape_error_with_provider_label(monkeypatch):
    """The fail-closed message names the provider, so each caller keeps its distinct text."""
    monkeypatch.setattr(evaluate, "_post_json", lambda *a, **k: {"choices": []})

    with pytest.raises(ProviderError, match="Unexpected Proxy response shape"):
        evaluate._call_openai_compatible("hello", "https://x/v1", "m", {}, "Proxy")


def test_openai_compatible_passes_headers_through(monkeypatch):
    """The helper must not swallow the per-provider auth headers."""
    captured = {}

    def fake_post_json(url, body, headers, timeout, provider_label):
        captured["headers"] = headers
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(evaluate, "_post_json", fake_post_json)

    evaluate._call_openai_compatible("hello", "https://x/v1", "m",
                                     {"authorization": "Bearer secret"}, "T")
    assert captured["headers"] == {"authorization": "Bearer secret"}


# ── Provider fallback chain ──────────────────────────────────────────


def test_resolve_provider_chain_single_value_is_backward_compat(monkeypatch):
    """A single provider name (no commas) returns a one-element chain."""
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "claude")
    assert resolve_provider_chain() == ["claude"]


def test_resolve_provider_chain_comma_separated_returns_ordered_list(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "claude, opencode , ollama")
    assert resolve_provider_chain() == ["claude", "opencode", "ollama"]


def test_resolve_provider_chain_explicit_arg_wins(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "claude")
    assert resolve_provider_chain(provider="ollama") == ["ollama"]


def test_resolve_provider_chain_empty_parts_filtered(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "claude,,opencode")
    assert resolve_provider_chain() == ["claude", "opencode"]


def test_call_provider_falls_back_when_first_fails_permanently(monkeypatch):
    """OpenCode raises ProviderError for missing key -> chain moves to ollama."""
    def failing_opencode(prompt, timeout=60):
        raise ProviderError("OpenCode provider selected but OPENCODE_API_KEY is not set")
    def succeeding(prompt, timeout=60):
        return "fallback-success"

    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "opencode", failing_opencode)
    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "fallback", succeeding)

    result = call_provider("test", provider="opencode,fallback")
    assert result == "fallback-success"


def test_call_provider_fails_when_all_providers_exhausted(monkeypatch):
    def always_fails(prompt, timeout=60):
        raise ProviderError("down")

    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "a", always_fails)
    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "b", always_fails)

    with pytest.raises(ProviderError, match="All 2 provider\\(s\\) failed"):
        call_provider("test", provider="a,b")


def test_call_provider_succeeds_on_first_provider(monkeypatch):
    def ok(prompt, timeout=60):
        return "first-wins"

    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "first", ok)
    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "second", ok)

    result = call_provider("test", provider="first,second")
    assert result == "first-wins"


def test_fallback_error_message_names_all_providers(monkeypatch):
    def fail_a(prompt, timeout=60):
        raise ProviderError("auth expired")
    def fail_b(prompt, timeout=60):
        raise ProviderError("timeout")

    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "one", fail_a)
    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "two", fail_b)

    with pytest.raises(ProviderError) as excinfo:
        call_provider("test", provider="one,two")

    msg = str(excinfo.value)
    assert "one: auth expired" in msg
    assert "two: timeout" in msg
    assert "All 2 provider(s) failed" in msg
