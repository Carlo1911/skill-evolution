"""LM Studio uses its own provider name/config, never Ollama IDs or credentials."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import pytest
import evaluate


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith('SKILL_EVOLUTION_'):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def captured(monkeypatch):
    captured = {}
    def post(url, body, headers, timeout, label):
        captured.update(url=url, body=body, headers=headers, timeout=timeout, label=label)
        return {'choices': [{'message': {'content': 'reply'}}]}
    monkeypatch.setattr(evaluate, '_post_json', post)
    return captured


def test_registered_and_resolved(monkeypatch):
    monkeypatch.setenv('SKILL_EVOLUTION_PROVIDER', 'opencode,lmstudio')
    assert evaluate.resolve_provider_chain() == ['opencode', 'lmstudio']
    assert 'lmstudio' in evaluate.PROVIDER_CALLERS


def test_local_request_without_hosted_key(monkeypatch, captured):
    monkeypatch.setenv('SKILL_EVOLUTION_LMSTUDIO_MODEL', 'phi-3.5-mini-instruct')
    assert evaluate.call_provider('hello', provider='lmstudio', timeout=17) == 'reply'
    assert captured['url'] == 'http://127.0.0.1:1234/v1/chat/completions'
    assert captured['body']['model'] == 'phi-3.5-mini-instruct'
    assert captured['body']['messages'] == [{'role': 'user', 'content': 'hello'}]
    assert captured['headers'] == {}
    assert captured['timeout'] == 17
    assert captured['label'] == 'LM Studio'


def test_overrides_do_not_read_ollama_configuration(monkeypatch, captured):
    monkeypatch.setenv('SKILL_EVOLUTION_LMSTUDIO_BASE_URL', 'http://localhost:2345/v1/')
    monkeypatch.setenv('SKILL_EVOLUTION_LMSTUDIO_MODEL', 'my-phi')
    monkeypatch.setenv('SKILL_EVOLUTION_OLLAMA_MODEL', 'wrong-model')
    monkeypatch.setenv('SKILL_EVOLUTION_OLLAMA_BASE_URL', 'http://localhost:11434/v1')
    evaluate._call_lmstudio('hello')
    assert captured['url'] == 'http://localhost:2345/v1/chat/completions'
    assert captured['body']['model'] == 'my-phi'


def test_missing_model_fails_before_http(monkeypatch):
    monkeypatch.setattr(evaluate, '_post_json', lambda *a: pytest.fail('must not send captured'))
    with pytest.raises(evaluate.ProviderError, match='SKILL_EVOLUTION_LMSTUDIO_MODEL'):
        evaluate._call_lmstudio('hello')


def test_bad_response_fails_closed(monkeypatch):
    monkeypatch.setenv('SKILL_EVOLUTION_LMSTUDIO_MODEL', 'phi')
    monkeypatch.setattr(evaluate, '_post_json', lambda *a: {'choices': []})
    with pytest.raises(evaluate.ProviderError, match='Unexpected LM Studio'):
        evaluate._call_lmstudio('hello')


def test_redaction_preserved(monkeypatch, captured):
    monkeypatch.setenv('SKILL_EVOLUTION_LMSTUDIO_MODEL', 'phi')
    evaluate.call_provider('sk-ant-api-shouldnotleak', provider='lmstudio')
    assert 'shouldnotleak' not in captured['body']['messages'][0]['content']
    assert '[REDACTED]' in captured['body']['messages'][0]['content']


def test_fallback_reaches_lmstudio_not_ollama(monkeypatch, captured):
    monkeypatch.setenv('SKILL_EVOLUTION_LMSTUDIO_MODEL', 'phi')
    def failure(*args, **kwargs):
        raise evaluate.ProviderError('hosted provider unavailable')
    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, 'opencode', failure)
    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, 'ollama', lambda *a, **k: pytest.fail('Ollama must not be called'))
    assert evaluate.call_provider('hello', provider='opencode,lmstudio') == 'reply'
    assert captured['label'] == 'LM Studio'
