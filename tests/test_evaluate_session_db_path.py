"""Regression tests for the state.db resolver the session-reading eval targets need (P0-1).

`evaluate._fetch_session_messages()` imported `fetch_sessions.get_state_db_path`, a function
that was never written, so `evaluate_tool_calls(session_id)` and
`evaluate_analyzer_prompt()` raised ImportError on every call that reached the DB. Their
existing tests all pass a list of message dicts instead of a session id -- the injection
path -- which left the DB branch unexecuted. These tests exercise the branch itself.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import fetch_sessions


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("SKILL_EVOLUTION_DB_PATH", raising=False)


def _make_db(path):
    """A state.db with the two columns _fetch_session_messages selects."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, "
        "tool_calls TEXT, timestamp TEXT)"
    )
    conn.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
        [
            ("s1", "user", "index my project", None, "2026-07-29T10:00:00"),
            ("s1", "assistant", "running it", '[{"name": "bash"}]', "2026-07-29T10:00:01"),
            ("s2", "user", "different session", None, "2026-07-29T11:00:00"),
        ],
    )
    conn.commit()
    conn.close()


def test_get_state_db_path_exists_and_defaults_to_the_hermes_db():
    """The function the import site has always expected."""
    assert hasattr(fetch_sessions, "get_state_db_path")
    assert fetch_sessions.get_state_db_path() == fetch_sessions.DEFAULT_DB_PATH


def test_get_state_db_path_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SKILL_EVOLUTION_DB_PATH", str(tmp_path / "other.db"))
    assert fetch_sessions.get_state_db_path() == str(tmp_path / "other.db")


def test_get_state_db_path_ignores_a_blank_env_value(monkeypatch):
    """Same posture as get_state_file(): a blank value must not redirect to ""."""
    monkeypatch.setenv("SKILL_EVOLUTION_DB_PATH", "   ")
    assert fetch_sessions.get_state_db_path() == fetch_sessions.DEFAULT_DB_PATH


def test_get_state_db_path_falls_back_to_the_module_global(monkeypatch, tmp_path):
    """Falls back to DEFAULT_DB_PATH, not the literal path, so monkeypatching it works --
    the same reason get_state_file() falls back to STATE_FILE."""
    monkeypatch.setattr(fetch_sessions, "DEFAULT_DB_PATH", str(tmp_path / "patched.db"))
    assert fetch_sessions.get_state_db_path() == str(tmp_path / "patched.db")


def test_fetch_session_messages_reads_the_db_instead_of_raising(monkeypatch, tmp_path):
    """The actual P0-1 regression: this path used to raise ImportError."""
    db = tmp_path / "state.db"
    _make_db(db)
    monkeypatch.setenv("SKILL_EVOLUTION_DB_PATH", str(db))

    messages = evaluate._fetch_session_messages("s1")

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "index my project"
    assert messages[1]["tool_calls"] == '[{"name": "bash"}]'


def test_fetch_session_messages_filters_by_session(monkeypatch, tmp_path):
    db = tmp_path / "state.db"
    _make_db(db)
    monkeypatch.setenv("SKILL_EVOLUTION_DB_PATH", str(db))

    assert len(evaluate._fetch_session_messages("s2")) == 1
    assert evaluate._fetch_session_messages("nonexistent") == []


def test_evaluate_tool_calls_accepts_a_session_id(monkeypatch, tmp_path):
    """End-to-end through the public function, with the provider stubbed.

    Asserting the seam end to end rather than each side of it: the isolated tests passed a
    message list, which is exactly why nothing caught the broken import.
    """
    db = tmp_path / "state.db"
    _make_db(db)
    monkeypatch.setenv("SKILL_EVOLUTION_DB_PATH", str(db))
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "llm_judge")
    monkeypatch.setattr(
        evaluate, "call_provider",
        lambda *a, **k: '{"correctness": 0.9, "procedure_following": 0.9, '
                        '"conciseness": 0.9, "feedback": "looks fine"}',
    )

    results = evaluate.evaluate_tool_calls("s1")

    assert results and all(r.passed for r in results)
