"""Tests for scripts/fetch_sessions.py's fetch_sessions().

Regression coverage for a bug found during code review: the per-message loop
used `msg.get("content", "")` on a `sqlite3.Row`, which has no `.get()` method
(`AttributeError: 'sqlite3.Row' object has no attribute 'get'`) -- so
fetch_sessions() crashed on any session that had at least one message. Fixed
by extracting the shared `_summarize_messages()` helper (also used by
sessions_for_skill()), which uses bracket access throughout.

The fixture schema here matches the live ~/.hermes/state.db schema
(`sessions.started_at`, no `sessions.total_tokens`; `messages.timestamp`, no
`messages.created_at`) -- see sessions_for_skill()'s own
SKILL_SESSION_QUERY/SKILL_MESSAGE_QUERY, which were already written against
these same live column names.
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
            started_at REAL NOT NULL,
            title TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            timestamp REAL NOT NULL
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
        "INSERT INTO sessions (id, source, model, started_at, title) VALUES (?, ?, ?, ?, ?)",
        ("sess-1", "claude-code", "claude", 1000.0, "a session"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        ("sess-1", "user", "hello there", 1000.0),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        ("sess-1", "assistant", "hi, how can I help?", 1001.0),
    )
    conn.commit()
    conn.close()

    results = fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=999999, dry_run=True)

    assert len(results) == 1
    assert results[0]["session_id"] == "sess-1"
    assert results[0]["started_at"] == 1000.0
    assert "total_tokens" not in results[0]
    assert results[0]["user_messages"] == 1
    assert results[0]["assistant_messages"] == 1
    contents = [m["content_preview"] for m in results[0]["messages"]]
    assert "hello there" in contents
    assert "hi, how can I help?" in contents


def test_fetch_sessions_redacts_secret_in_message(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sessions (id, source, model, started_at, title) VALUES (?, ?, ?, ?, ?)",
        ("sess-secret", "claude-code", "claude", 1000.0, "t"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        ("sess-secret", "user", "here is my key sk-ant-api03-abc123", 1000.0),
    )
    conn.commit()
    conn.close()

    results = fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=999999, dry_run=True)

    assert results[0]["messages"] == []


def test_fetch_sessions_filters_by_lookback_window(db_path):
    """R2: a session whose started_at is outside --lookback-hours is excluded;
    one inside the window is included."""
    import time

    now = time.time()
    conn = sqlite3.connect(db_path)
    # Well outside a 48-hour lookback window (10 days ago).
    conn.execute(
        "INSERT INTO sessions (id, source, model, started_at, title) VALUES (?, ?, ?, ?, ?)",
        ("sess-old", "claude-code", "claude", now - (10 * 24 * 3600), "old session"),
    )
    # Well inside a 48-hour lookback window (1 hour ago).
    conn.execute(
        "INSERT INTO sessions (id, source, model, started_at, title) VALUES (?, ?, ?, ?, ?)",
        ("sess-recent", "claude-code", "claude", now - 3600, "recent session"),
    )
    conn.commit()
    conn.close()

    results = fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=48, dry_run=True)

    session_ids = {r["session_id"] for r in results}
    assert session_ids == {"sess-recent"}
