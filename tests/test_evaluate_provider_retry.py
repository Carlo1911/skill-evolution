"""Tests for the provider retry/backoff (P3-4) and transport-failure tagging."""

import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import LLMJudgeEvaluator, ProviderError, append_history, _post_json


@pytest.fixture(autouse=True)
def clean_retry_env(monkeypatch):
    for key in ("SKILL_EVOLUTION_PROVIDER_RETRIES",
                "SKILL_EVOLUTION_PROVIDER_RETRY_BASE_SECONDS",
                "SKILL_EVOLUTION_PROVIDER_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(code, headers=None):
    return urllib.error.HTTPError("https://x.test", code, "boom", headers, None)


def _retrying_urlopen(monkeypatch, calls):
    """Monkeypatch urlopen to record calls and return a fake JSON response."""

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(evaluate.urllib.request, "urlopen", fake_urlopen)


# ── _is_retryable_transport_error classification ────────────────────


def test_retryable_codes_are_retryable():
    for code in (408, 429, 500, 502, 503, 504):
        assert evaluate._is_retryable_transport_error(_http_error(code)), code


def test_auth_and_client_errors_are_not_retryable():
    for code in (400, 401, 403, 404):
        assert not evaluate._is_retryable_transport_error(_http_error(code)), code


def test_timeout_and_connection_errors_are_retryable():
    assert evaluate._is_retryable_transport_error(TimeoutError("slow"))
    assert evaluate._is_retryable_transport_error(urllib.error.URLError("no route"))
    assert not evaluate._is_retryable_transport_error(ValueError("bad url"))


# ── _post_json retry behavior ────────────────────────────────────────


def test_retries_on_transient_5xx_then_fails_closed(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        raise _http_error(503)

    monkeypatch.setattr(evaluate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(evaluate.time, "sleep", lambda s: None)

    with pytest.raises(ProviderError, match="test provider call failed"):
        _post_json("https://x.test", {}, {}, 60, "test")

    assert len(calls) == 3  # default 2 retries → 3 attempts


def test_succeeds_after_transient_failure(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise _http_error(503)
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(evaluate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(evaluate.time, "sleep", lambda s: None)

    assert _post_json("https://x.test", {}, {}, 60, "test") == {"ok": True}
    assert len(calls) == 2


def test_auth_error_is_not_retried(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        raise _http_error(401)

    monkeypatch.setattr(evaluate.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ProviderError):
        _post_json("https://x.test", {}, {}, 60, "test")

    assert len(calls) == 1


def test_non_json_body_is_not_retried(monkeypatch):
    """A non-JSON response body (ValueError from json.loads) is a content fault, not a
    transient transport condition -- retrying it cannot help and would only burn time."""

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(b"<html>not json</html>")

    monkeypatch.setattr(evaluate.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ProviderError):
        _post_json("https://x.test", {}, {}, 60, "test")


def test_timeout_error_is_retried(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        raise TimeoutError("too slow")

    monkeypatch.setattr(evaluate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(evaluate.time, "sleep", lambda s: None)

    with pytest.raises(ProviderError):
        _post_json("https://x.test", {}, {}, 60, "test")

    assert len(calls) == 3


def test_backoff_schedule_is_exponential(monkeypatch):
    sleeps = []

    def fake_urlopen(req, timeout=None):
        raise _http_error(500)

    monkeypatch.setattr(evaluate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(evaluate.time, "sleep", sleeps.append)

    with pytest.raises(ProviderError):
        _post_json("https://x.test", {}, {}, 60, "test")

    assert sleeps == [1.0, 2.0]  # base 1.0 × 2**attempt


def test_retry_after_header_overrides_schedule_on_429(monkeypatch):
    sleeps = []

    def fake_urlopen(req, timeout=None):
        raise _http_error(429, {"Retry-After": "7"})

    monkeypatch.setattr(evaluate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(evaluate.time, "sleep", sleeps.append)

    with pytest.raises(ProviderError):
        _post_json("https://x.test", {}, {}, 60, "test")

    assert sleeps == [7.0, 7.0]


def test_retries_zero_disables_retry(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        raise _http_error(503)

    monkeypatch.setattr(evaluate.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ProviderError):
        _post_json("https://x.test", {}, {}, 60, "test", retries=0)

    assert len(calls) == 1


def test_retries_and_base_seconds_from_env(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER_RETRIES", "1")
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER_RETRY_BASE_SECONDS", "0.25")
    sleeps = []
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        raise _http_error(500)

    monkeypatch.setattr(evaluate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(evaluate.time, "sleep", sleeps.append)

    with pytest.raises(ProviderError):
        _post_json("https://x.test", {}, {}, 60, "test")

    assert len(calls) == 2  # 1 retry
    assert sleeps == [0.25]


def test_resolve_retries_explicit_arg_beats_env(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER_RETRIES", "5")
    assert evaluate.resolve_provider_retries(1) == 1
    assert evaluate.resolve_provider_retries() == 5
    assert evaluate.resolve_provider_retries(0) == 0


# ── Transport-failure tagging ────────────────────────────────────────


def test_judge_marks_provider_error_as_transport_failure(monkeypatch):
    def boom(prompt, evaluator_name=None):
        raise ProviderError("provider call failed: 503")

    monkeypatch.setattr(evaluate, "call_provider", boom)
    result = LLMJudgeEvaluator().evaluate("some skill content")
    assert result.passed is False
    assert result.score == 0.0
    assert result.transport_failure is True


def test_judge_does_not_tag_malformed_content_as_transport(monkeypatch):
    monkeypatch.setattr(evaluate, "call_provider",
                        lambda prompt, evaluator_name=None: "not json")
    result = LLMJudgeEvaluator().evaluate("some skill content")
    assert result.passed is False
    assert result.transport_failure is False


def test_append_history_writes_flag_only_when_set(tmp_path):
    path = tmp_path / "hist.jsonl"
    ok = evaluate.EvalResult(0.9, "fine", True, "gate")
    broken = evaluate.EvalResult(0.0, "outage", False, "gate", transport_failure=True)

    append_history("skill:x", ok, path=str(path))
    append_history("skill:x", broken, path=str(path))

    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert "transport_failure" not in entries[0]
    assert entries[1]["transport_failure"] is True


def test_find_low_scoring_targets_skips_transport_flagged(monkeypatch, tmp_path):
    import optimize_skill

    from host_sandbox import build_host
    box = build_host("claude_code", tmp_path, monkeypatch)
    for name in ("good", "outage"):
        box.write_skill(name, f"---\nname: {name}\ndescription: d\n---\n")
    path = tmp_path / "hist.jsonl"
    lines = [
        {"target": "skill:good", "timestamp": "2026-07-01T00:00:00+00:00", "score": 0.5, "passed": False, "feedback": "genuine regression", "evaluator_name": "gate", "subject": "installed"},
        {"target": "skill:outage", "timestamp": "2026-07-02T00:00:00+00:00", "score": 0.0, "passed": False, "feedback": "503", "evaluator_name": "gate", "transport_failure": True, "subject": "installed"},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    monkeypatch.setattr(evaluate, "get_history_path", lambda: str(path))

    result = optimize_skill.find_low_scoring_targets()

    targets = [entry["target"] for entry in result]
    assert targets == ["skill:good"]
