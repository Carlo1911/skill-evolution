"""Tests for scripts/fetch_sessions.py's fetch_sessions().

Regression coverage for a bug found during code review: the per-message loop
used `msg.get("content", "")` on a `sqlite3.Row`, which has no `.get()` method
(`AttributeError: 'sqlite3.Row' object has no attribute 'get'`) -- so
fetch_sessions() crashed on any session that had at least one message. Fixed
by extracting the shared `_summarize_messages()` helper (also used by
sessions_for_skill()), which uses bracket access throughout.

The fixture schema here intentionally matches fetch_sessions()'s own
SESSION_QUERY/MESSAGE_QUERY column names (`created_at`, `total_tokens`) --
NOT the live ~/.hermes/state.db schema (`started_at`, no `total_tokens`),
which those queries do not actually match. That schema/query mismatch is a
separate, pre-existing bug outside this fix's scope (see
sessions_for_skill()'s own SKILL_SESSION_QUERY/SKILL_MESSAGE_QUERY, which
were deliberately written against the real live column names instead).
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import fetch_sessions


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            model TEXT,
            created_at REAL NOT NULL,
            total_tokens INTEGER,
            title TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            created_at REAL NOT NULL
        );
        """
    )
    conn.commit()
    return conn


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "state.db")
    _make_db(path).close()
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", str(tmp_path / "skill_evolution_state.json"))
    return path


def test_fetch_sessions_with_messages_does_not_crash(db_path):
    """Regression: previously raised AttributeError on any session with messages."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sessions (id, source, model, created_at, total_tokens, title) VALUES (?, ?, ?, ?, ?, ?)",
        ("sess-1", "claude-code", "claude", 1000.0, 42, "a session"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("sess-1", "user", "hello there", 1000.0),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("sess-1", "assistant", "hi, how can I help?", 1001.0),
    )
    conn.commit()
    conn.close()

    results = fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=999999, dry_run=True)

    assert len(results) == 1
    assert results[0]["session_id"] == "sess-1"
    assert results[0]["user_messages"] == 1
    assert results[0]["assistant_messages"] == 1
    contents = [m["content_preview"] for m in results[0]["messages"]]
    assert "hello there" in contents
    assert "hi, how can I help?" in contents


def test_fetch_sessions_redacts_secret_in_message(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sessions (id, source, model, created_at, total_tokens, title) VALUES (?, ?, ?, ?, ?, ?)",
        ("sess-secret", "claude-code", "claude", 1000.0, 10, "t"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        ("sess-secret", "user", "here is my key sk-ant-api03-abc123", 1000.0),
    )
    conn.commit()
    conn.close()

    results = fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=999999, dry_run=True)

    assert results[0]["messages"] == []
