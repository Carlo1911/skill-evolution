"""Tests for scripts/fetch_sessions.py's sessions_for_skill() (U1).

sessions_for_skill() answers a different question than fetch_sessions():
given a skill name, return that skill's *entire* recorded session history,
regardless of the cron pipeline's processed-state or lookback window. It has
no side effects (no mark_processed(), no state-file writes).

The fixture DB schema below mirrors the columns actually present in the live
~/.hermes/state.db (verified via `sqlite3 ~/.hermes/state.db ".schema
sessions"` / `".schema messages"`) — notably `sessions.started_at` (not
`created_at`) and `messages.timestamp` (not `created_at`), and no
`sessions.total_tokens` column at all. This is a deliberate correction to a
pre-existing bug in fetch_sessions()'s own SESSION_QUERY/MESSAGE_QUERY.
"""

import json
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
            tool_calls TEXT,
            timestamp REAL NOT NULL
        );
        """
    )
    conn.commit()
    return conn


def _tool_call(function_name, name_arg):
    """Build a tool_calls JSON array string matching the live schema shape:
    [{"function": {"name": "<fn>", "arguments": "<json-string>"}}]
    """
    return json.dumps(
        [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": function_name,
                    "arguments": json.dumps({"name": name_arg}),
                },
            }
        ]
    )


def _insert_session(conn, session_id, source="claude-code", model="claude", started_at=1000.0, title="t"):
    conn.execute(
        "INSERT INTO sessions (id, source, model, started_at, title) VALUES (?, ?, ?, ?, ?)",
        (session_id, source, model, started_at, title),
    )


def _insert_message(conn, session_id, role, content=None, tool_calls=None, timestamp=1000.0):
    conn.execute(
        "INSERT INTO messages (session_id, role, content, tool_calls, timestamp) VALUES (?, ?, ?, ?, ?)",
        (session_id, role, content, tool_calls, timestamp),
    )


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "state.db")
    conn = _make_db(path)
    conn.commit()
    conn.close()
    return path


def test_function_exists():
    assert hasattr(fetch_sessions, "sessions_for_skill")


def test_skill_view_matches_across_three_sessions(db_path):
    conn = sqlite3.connect(db_path)
    for i in range(3):
        sid = f"sess-{i}"
        _insert_session(conn, sid)
        _insert_message(conn, sid, "user", content="hello")
        _insert_message(
            conn, sid, "assistant", tool_calls=_tool_call("skill_view", "my-skill")
        )
    # An unrelated session that should not match.
    _insert_session(conn, "sess-other")
    _insert_message(
        conn, "sess-other", "assistant", tool_calls=_tool_call("skill_view", "other-skill")
    )
    conn.commit()
    conn.close()

    results = fetch_sessions.sessions_for_skill("my-skill", db_path=db_path)

    assert {r["session_id"] for r in results} == {"sess-0", "sess-1", "sess-2"}


def test_skill_manage_also_matches(db_path):
    conn = sqlite3.connect(db_path)
    _insert_session(conn, "sess-manage")
    _insert_message(
        conn,
        "sess-manage",
        "assistant",
        tool_calls=_tool_call("skill_manage", "my-skill"),
    )
    conn.commit()
    conn.close()

    results = fetch_sessions.sessions_for_skill("my-skill", db_path=db_path)

    assert [r["session_id"] for r in results] == ["sess-manage"]


def test_skill_never_invoked_returns_empty_list(db_path):
    conn = sqlite3.connect(db_path)
    _insert_session(conn, "sess-unrelated")
    _insert_message(conn, "sess-unrelated", "user", content="no tool calls here")
    conn.commit()
    conn.close()

    results = fetch_sessions.sessions_for_skill("never-invoked-skill", db_path=db_path)

    assert results == []


def test_secret_bearing_message_is_redacted(db_path):
    conn = sqlite3.connect(db_path)
    _insert_session(conn, "sess-secret")
    _insert_message(
        conn, "sess-secret", "assistant", tool_calls=_tool_call("skill_view", "my-skill")
    )
    _insert_message(
        conn, "sess-secret", "user", content="here is my key sk-ant-api03-abc123"
    )
    _insert_message(conn, "sess-secret", "assistant", content="normal reply, no secret")
    conn.commit()
    conn.close()

    results = fetch_sessions.sessions_for_skill("my-skill", db_path=db_path)

    assert len(results) == 1
    contents = [m["content_preview"] for m in results[0]["messages"]]
    assert not any("sk-ant-api" in c for c in contents)
    # The non-secret message should still be present.
    assert any("normal reply" in c for c in contents)


def test_cron_source_sessions_excluded(db_path):
    conn = sqlite3.connect(db_path)
    _insert_session(conn, "sess-cron", source="cron")
    _insert_message(
        conn, "sess-cron", "assistant", tool_calls=_tool_call("skill_view", "my-skill")
    )
    conn.commit()
    conn.close()

    results = fetch_sessions.sessions_for_skill("my-skill", db_path=db_path)

    assert results == []


def test_query_runs_against_live_schema_column_names(db_path):
    """Regression: uses started_at/timestamp, not fetch_sessions()'s buggy
    created_at/total_tokens column names, and doesn't blow up on a fixture
    schema that only has the live columns."""
    conn = sqlite3.connect(db_path)
    _insert_session(conn, "sess-schema", started_at=12345.0)
    _insert_message(
        conn,
        "sess-schema",
        "assistant",
        tool_calls=_tool_call("skill_view", "my-skill"),
        timestamp=12345.5,
    )
    conn.commit()
    conn.close()

    results = fetch_sessions.sessions_for_skill("my-skill", db_path=db_path)

    assert len(results) == 1
    assert results[0]["session_id"] == "sess-schema"
    assert results[0]["started_at"] == 12345.0


def test_long_message_content_is_truncated(db_path):
    conn = sqlite3.connect(db_path)
    _insert_session(conn, "sess-long")
    _insert_message(
        conn, "sess-long", "assistant", tool_calls=_tool_call("skill_view", "my-skill")
    )
    long_content = "x" * (fetch_sessions.MAX_MESSAGE_CHARS + 500)
    _insert_message(conn, "sess-long", "user", content=long_content)
    conn.commit()
    conn.close()

    results = fetch_sessions.sessions_for_skill("my-skill", db_path=db_path)

    preview = next(m for m in results[0]["messages"] if m["role"] == "user")
    assert preview["content_length"] == fetch_sessions.MAX_MESSAGE_CHARS + len("\n[...truncated]")
    assert preview["content_preview"] == (
        long_content[:fetch_sessions.MAX_MESSAGE_CHARS] + "\n[...truncated]"
    )[:500]


def test_malformed_tool_calls_json_does_not_crash_query(db_path):
    """The json_valid() SQL guards must filter out malformed tool_calls/arguments
    rather than raising sqlite3.OperationalError: malformed JSON."""
    conn = sqlite3.connect(db_path)
    _insert_session(conn, "sess-malformed")
    _insert_message(conn, "sess-malformed", "assistant", tool_calls="not valid json{{{")
    _insert_session(conn, "sess-bad-args")
    _insert_message(
        conn,
        "sess-bad-args",
        "assistant",
        tool_calls=json.dumps([{
            "id": "call_1", "type": "function",
            "function": {"name": "skill_view", "arguments": "not valid json{{{"},
        }]),
    )
    conn.commit()
    conn.close()

    # Must not raise; neither malformed row matches, so no sessions come back.
    results = fetch_sessions.sessions_for_skill("my-skill", db_path=db_path)

    assert results == []


def test_max_sessions_caps_and_prefers_most_recent(db_path):
    """Regression: an unbounded match count must not grow the returned excerpt
    set without limit -- cap at max_sessions, keeping the most recent."""
    conn = sqlite3.connect(db_path)
    for i in range(5):
        sid = f"sess-{i}"
        _insert_session(conn, sid, started_at=float(i))  # sess-4 is most recent
        _insert_message(conn, sid, "assistant", tool_calls=_tool_call("skill_view", "my-skill"))
    conn.commit()
    conn.close()

    results = fetch_sessions.sessions_for_skill("my-skill", db_path=db_path, max_sessions=2)

    assert [r["session_id"] for r in results] == ["sess-4", "sess-3"]


def test_no_side_effects_no_state_file_written(db_path, tmp_path, monkeypatch):
    """sessions_for_skill() must not call mark_processed() or touch the
    cron pipeline's state file."""
    fake_state_file = str(tmp_path / "skill_evolution_state.json")
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", fake_state_file)

    conn = sqlite3.connect(db_path)
    _insert_session(conn, "sess-1")
    _insert_message(
        conn, "sess-1", "assistant", tool_calls=_tool_call("skill_view", "my-skill")
    )
    conn.commit()
    conn.close()

    fetch_sessions.sessions_for_skill("my-skill", db_path=db_path)

    assert not os.path.exists(fake_state_file)
