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


# ── Automatic state pruning (SKILL_EVOLUTION_STATE_RETENTION) ────────────
#
# Mirrors evaluate.py's auto-prune: a no-op unless the retention env var is configured,
# wired into fetch_sessions() itself (the only real orchestrator of prune_processed() --
# state.py has none of its own) right after mark_processed(), so it fires on every real,
# non-dry-run invocation including the unattended nightly cron run.

def _seed_state(path, entries):
    import json
    with open(path, "w") as f:
        json.dump(entries, f)


def _insert_session(conn, session_id, started_at):
    conn.execute(
        "INSERT INTO sessions (id, source, model, started_at, title) VALUES (?, ?, ?, ?, ?)",
        (session_id, "claude-code", "claude", started_at, "t"),
    )


def test_fetch_sessions_prunes_automatically_when_retention_configured(db_path, monkeypatch, tmp_path):
    import time
    now = time.time()
    state_path = str(tmp_path / "skill_evolution_state.json")
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", state_path)
    monkeypatch.setenv(fetch_sessions.STATE_RETENTION_ENV_VAR, "1d")
    old_iso = "2020-01-01T00:00:00+00:00"
    _seed_state(state_path, {"already-old": old_iso})

    conn = sqlite3.connect(db_path)
    _insert_session(conn, "sess-new", now - 60)
    conn.commit()
    conn.close()

    fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=48, dry_run=False)

    processed = set(fetch_sessions.load_processed())
    assert "already-old" not in processed  # pruned: far older than the 1-day retention
    assert "sess-new" in processed


def test_fetch_sessions_does_not_prune_when_retention_unset(db_path, monkeypatch, tmp_path):
    import time
    now = time.time()
    state_path = str(tmp_path / "skill_evolution_state.json")
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", state_path)
    monkeypatch.delenv(fetch_sessions.STATE_RETENTION_ENV_VAR, raising=False)
    old_iso = "2020-01-01T00:00:00+00:00"
    _seed_state(state_path, {"already-old": old_iso})

    conn = sqlite3.connect(db_path)
    _insert_session(conn, "sess-new", now - 60)
    conn.commit()
    conn.close()

    fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=48, dry_run=False)

    processed = set(fetch_sessions.load_processed())
    assert "already-old" in processed  # regression lock: default behavior is unchanged
    assert "sess-new" in processed


def test_fetch_sessions_keep_ids_protects_just_written_entries(db_path, monkeypatch, tmp_path):
    """The self-defeating-loop regression test: mark_processed() stamps an entire run's
    batch with the same timestamp, so a naive recency-sort at a tight count retention has
    no tiebreak among them. Without the keep_ids floor, this run's own new sessions could
    be pruned in the very call that just wrote them."""
    import time
    now = time.time()
    state_path = str(tmp_path / "skill_evolution_state.json")
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", state_path)
    monkeypatch.setenv(fetch_sessions.STATE_RETENTION_ENV_VAR, "1")  # count=1 -- as tight as it gets

    conn = sqlite3.connect(db_path)
    for i in range(3):
        _insert_session(conn, f"sess-new-{i}", now - 60 - i)
    conn.commit()
    conn.close()

    fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=48, dry_run=False)

    processed = set(fetch_sessions.load_processed())
    # All three survive despite retention=1 -- a naive sort-and-truncate would have kept
    # only one of them, since they share this run's single now() timestamp.
    assert processed == {"sess-new-0", "sess-new-1", "sess-new-2"}


def test_fetch_sessions_dry_run_never_prunes(db_path, monkeypatch, tmp_path):
    state_path = str(tmp_path / "skill_evolution_state.json")
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", state_path)
    monkeypatch.setenv(fetch_sessions.STATE_RETENTION_ENV_VAR, "1")

    def fail_if_called(*a, **kw):
        raise AssertionError("prune_processed must not be called during --dry-run")
    monkeypatch.setattr(fetch_sessions, "prune_processed", fail_if_called)

    fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=48, dry_run=True)  # must not raise


def test_fetch_sessions_warns_when_state_retention_below_lookback(db_path, monkeypatch, tmp_path, capsys):
    import time
    now = time.time()
    state_path = str(tmp_path / "skill_evolution_state.json")
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", state_path)
    monkeypatch.setenv(fetch_sessions.STATE_RETENTION_ENV_VAR, "1d")  # 24h < the 48h lookback below

    conn = sqlite3.connect(db_path)
    _insert_session(conn, "sess-new", now - 60)
    conn.commit()
    conn.close()

    fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=48, dry_run=False)

    err = capsys.readouterr().err
    assert "SKILL_EVOLUTION_STATE_RETENTION" in err
    assert "lookback" in err.lower()


def test_fetch_sessions_no_warning_when_state_retention_at_or_above_lookback(db_path, monkeypatch, tmp_path, capsys):
    import time
    now = time.time()
    state_path = str(tmp_path / "skill_evolution_state.json")
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", state_path)
    monkeypatch.setenv(fetch_sessions.STATE_RETENTION_ENV_VAR, "30d")  # far above the 48h lookback
    # A fresh state file starts documented-shape (mark_processed()'s own fallback), which
    # prune_processed() can never prune and always warns about -- seed a pre-existing
    # flat-dict entry so the file stays flat-dict-shaped, matching how the real deployed
    # file actually got that shape, and exercising the branch this test is about.
    _seed_state(state_path, {"already-processed": "2026-01-01T00:00:00+00:00"})

    conn = sqlite3.connect(db_path)
    _insert_session(conn, "sess-new", now - 60)
    conn.commit()
    conn.close()

    fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=48, dry_run=False)

    assert "SKILL_EVOLUTION_STATE_RETENTION" not in capsys.readouterr().err


def test_cross_run_reprocessing_gap_is_a_documented_limitation(db_path, monkeypatch, tmp_path):
    """Documents, rather than fixes, the one hole keep_ids doesn't close.

    keep_ids only protects a run's own writes from itself, so a session freshly marked
    processed can never be pruned in the same call. The gap is cross-run: a session marked
    processed *in the past* (here seeded directly, simulating a real prior run) ages past
    the configured retention and gets pruned from state on this run's prune_processed()
    call -- but if it's still inside a later run's --lookback-hours window (a deliberately
    wide 200h one here), it no longer looks "already processed" and gets re-fetched. The
    mitigation in scope is the warning tested above, not fixing this -- operators should
    keep retention >= lookback_hours.
    """
    import time
    now = time.time()
    state_path = str(tmp_path / "skill_evolution_state.json")
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", state_path)
    monkeypatch.setenv(fetch_sessions.STATE_RETENTION_ENV_VAR, "1d")
    # Seeded as already processed, well past the 1-day retention -- simulating a session a
    # real prior run (not this test) marked processed a long time ago.
    _seed_state(state_path, {"sess-borderline": "2020-01-01T00:00:00+00:00"})

    conn = sqlite3.connect(db_path)
    # Still inside a deliberately wide (misconfigured) 200-hour lookback window.
    _insert_session(conn, "sess-borderline", now - (3 * 24 * 3600))
    conn.commit()
    conn.close()

    # Run 1: already in `processed`, so the query excludes it -- nothing "new" this run,
    # but prune_processed() removes its now-ancient state entry regardless (it isn't in
    # this run's keep_ids, since nothing was newly processed).
    first = fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=200, dry_run=False)
    assert first == []
    assert "sess-borderline" not in set(fetch_sessions.load_processed())

    # Run 2: with the state entry gone and the session still inside the lookback window,
    # it no longer looks processed -- re-fetched. This is the documented gap.
    second = fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=200, dry_run=False)
    assert {r["session_id"] for r in second} == {"sess-borderline"}


def test_prune_state_cli_reports_to_stderr_and_returns_before_touching_the_db(monkeypatch, tmp_path, capsys):
    state_path = str(tmp_path / "skill_evolution_state.json")
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", state_path)
    _seed_state(state_path, {"old": "2020-01-01T00:00:00+00:00"})
    monkeypatch.setenv(fetch_sessions.STATE_RETENTION_ENV_VAR, "1d")

    def fail_if_called(*a, **kw):
        raise AssertionError("--prune-state must not call fetch_sessions()")
    monkeypatch.setattr(fetch_sessions, "fetch_sessions", fail_if_called)
    monkeypatch.setattr(sys, "argv", ["fetch_sessions.py", "--prune-state"])

    fetch_sessions.main()  # must not raise, must not call fetch_sessions()

    captured = capsys.readouterr()
    assert captured.out == ""  # nothing on the NDJSON channel
    assert "Pruned state" in captured.err
