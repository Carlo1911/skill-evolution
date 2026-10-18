"""The per-run session cap: 200 by default, SKILL_EVOLUTION_MAX_SESSIONS, --max-sessions.

Before: Hermes applied `LIMIT 20` *before* skipping processed sessions, so with the newest
20 processed and 5 older ones pending inside the window a run returned nothing, and a busy
day's overflow was never analyzed. Claude Code had no cap at all and ignored
--max-sessions (250 pending -> 250 returned). Now both hosts cap the *unprocessed*
sessions, newest first, and leave the overflow unmarked for the next run.
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import fetch_sessions
from host_sandbox import HOSTS, build_host

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "fetch_sessions.py")


@pytest.fixture(params=HOSTS)
def box(request, tmp_path, monkeypatch):
    return build_host(request.param, tmp_path, monkeypatch)


def _ids(n):
    """n sessions in the last hour, newest first: s000 is the newest."""
    now = time.time()
    return [(f"s{i:03d}", now - 120 - i * 10) for i in range(n)]


def _fetch(box, dry_run=False, max_sessions=None):
    if box.name == "hermes":
        return fetch_sessions.fetch_sessions(db_path=str(box.db_path), dry_run=dry_run,
                                             max_sessions=max_sessions, host="hermes")
    return fetch_sessions.fetch_sessions_for_host("claude_code", dry_run=dry_run,
                                                  max_sessions=max_sessions)


def _run_cli(box, *args):
    run = subprocess.run([sys.executable, SCRIPT, "--db-path", str(box.db_path), "--dry-run", *args],
                         capture_output=True, text=True, env=dict(os.environ))
    assert run.returncode == 0, run.stderr
    return [json.loads(line)["session_id"] for line in run.stdout.splitlines()]


def test_older_pending_sessions_are_not_hidden_by_processed_ones(box):
    sessions = _ids(25)
    box.add_sessions(sessions)
    box.mark_processed([sid for sid, _ in sessions[:20]])

    got = [s["session_id"] for s in _fetch(box, dry_run=True)]

    assert got == [sid for sid, _ in sessions[20:]]


def test_default_cap_is_200_and_the_overflow_waits_for_the_next_run(box):
    sessions = _ids(250)
    box.add_sessions(sessions)

    first = _fetch(box)
    second = _fetch(box)

    assert len(first) == fetch_sessions.DEFAULT_MAX_SESSIONS == 200
    assert [s["session_id"] for s in first] == [sid for sid, _ in sessions[:200]]
    assert [s["session_id"] for s in second] == [sid for sid, _ in sessions[200:]]
    assert box.processed() == {sid for sid, _ in sessions}


def test_overflow_is_not_marked_processed(box):
    sessions = _ids(5)
    box.add_sessions(sessions)

    _fetch(box, max_sessions=2)

    assert box.processed() == {"s000", "s001"}


def test_env_var_sets_the_cap(box, monkeypatch):
    box.add_sessions(_ids(10))
    monkeypatch.setenv(fetch_sessions.MAX_SESSIONS_ENV_VAR, "3")

    assert _run_cli(box) == ["s000", "s001", "s002"]


def test_flag_beats_env_var(box, monkeypatch):
    box.add_sessions(_ids(10))
    monkeypatch.setenv(fetch_sessions.MAX_SESSIONS_ENV_VAR, "3")

    assert len(_run_cli(box, "--max-sessions", "5")) == 5


@pytest.mark.parametrize("raw", ["abc", "0", "-4"])
def test_invalid_env_var_warns_and_uses_the_default(raw, monkeypatch, capsys):
    monkeypatch.setenv(fetch_sessions.MAX_SESSIONS_ENV_VAR, raw)

    assert fetch_sessions.resolve_max_sessions() == 200
    assert fetch_sessions.MAX_SESSIONS_ENV_VAR in capsys.readouterr().err


def test_explicit_value_beats_the_env_var(monkeypatch):
    monkeypatch.setenv(fetch_sessions.MAX_SESSIONS_ENV_VAR, "3")

    assert fetch_sessions.resolve_max_sessions(7) == 7


def test_hermes_text_started_at_rows_are_still_considered(tmp_path, monkeypatch):
    """ISO-string started_at values pass the SQL window filter and are checked in Python."""
    box = build_host("hermes", tmp_path, monkeypatch)
    import sqlite3
    from datetime import datetime, timedelta, timezone
    conn = sqlite3.connect(box.db_path)
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    for sid, ts in [("iso-recent", recent), ("iso-old", old)]:
        conn.execute("INSERT INTO sessions (id, source, model, title, started_at) "
                     "VALUES (?, 'cli', 'm', 't', ?)", (sid, ts))
    conn.commit()
    conn.close()

    got = [s["session_id"] for s in _fetch(box, dry_run=True)]

    assert got == ["iso-recent"]
