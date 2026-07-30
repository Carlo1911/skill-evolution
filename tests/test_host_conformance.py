"""Shared conformance test (U4): "same shape on every host" enforced by ONE test file
that runs identical assertions against every registered HostAdapter, rather than left as
prose or as two separate per-adapter suites that could silently drift apart while both
stay green.

Fixture-building is per-adapter (a small SQLite DB + skill tree for Hermes, a JSONL file
+ flat skill tree for Claude Code -- each host's real on-disk format), reusing/adapting
the fixture-building helpers already written in test_host_registry.py (U1) and
test_host_claude_code.py (U3). The assertion bodies below are written exactly once and
run against both adapters via parametrize.

Do NOT add a third per-adapter suite here -- extend _FIXTURE_BUILDERS and the two
EXPECTED_*_KEYS constants instead, so a future host is forced through the same shape
check the first two are.
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import fetch_sessions
import host
import skill_index


# ── Expected shapes (the "one enforcement point" this unit exists for) ──

EXPECTED_SESSION_KEYS = {
    "session_id", "started_at", "title", "model", "source",
    "message_count", "user_messages", "assistant_messages", "messages",
}

EXPECTED_SKILL_KEYS = {"name", "category", "description", "path", "size"}


# ── Hermes fixture: SQLite state.db + ~/.hermes/skills/-shaped tree ─────
# Adapted from test_host_registry.py's _make_db()/_write_skill() helpers.

def _make_hermes_db(path):
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
    conn.execute(
        "INSERT INTO sessions (id, source, model, started_at, title) VALUES (?, ?, ?, ?, ?)",
        ("sess-1", "claude-code", "claude", 1000.0, "a session"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        ("sess-1", "user", "hello there", 1000.0),
    )
    conn.commit()
    conn.close()


def _write_hermes_skill(root, category, name, description="does a thing"):
    skill_dir = root / category / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nBody.\n"
    )


def _build_hermes_fixture(tmp_path, monkeypatch):
    """One session, one live skill, one dot-prefixed-category twin that must be
    excluded -- exercising both iter_sessions() and iter_skills() in one adapter."""
    db_path = str(tmp_path / "state.db")
    _make_hermes_db(db_path)
    monkeypatch.setattr(fetch_sessions, "STATE_FILE", str(tmp_path / "skill_evolution_state.json"))
    monkeypatch.setenv(fetch_sessions.DB_PATH_ENV_VAR, db_path)

    skills_root = tmp_path / "skills"
    _write_hermes_skill(skills_root, "general-skills", "money-admin-messaging")
    _write_hermes_skill(skills_root, ".archive", "money-admin-messaging")  # must be excluded

    original_scan_skills = skill_index.scan_skills
    monkeypatch.setattr(host.skill_index, "scan_skills", lambda: original_scan_skills(str(skills_root)))

    return host.HermesAdapter()


# ── Claude Code fixture: JSONL session file + flat skills/ tree ────────
# Adapted from test_host_claude_code.py's _write_jsonl()/_write_skill()/user_record()/
# assistant_record() helpers.

def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _cc_user_record(session_id, ts, text):
    return {
        "type": "user",
        "sessionId": session_id,
        "timestamp": ts,
        "message": {"role": "user", "content": text},
    }


def _cc_assistant_record(session_id, ts, text, model="claude-sonnet-5"):
    return {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": ts,
        "message": {"role": "assistant", "model": model, "content": text},
    }


def _write_claude_code_skill(root, skill_dir_name, name=None, description="does a thing"):
    skill_dir = root / "skills" / skill_dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_name = name if name is not None else skill_dir_name
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: {description}\n---\n\n# {skill_name}\n\nBody text.\n"
    )


def _build_claude_code_fixture(tmp_path, monkeypatch):
    """One session (one user + one assistant message), one live skill, one
    dot-prefixed skill directory that must be excluded."""
    monkeypatch.setenv(host.CLAUDE_CODE_HOME_ENV_VAR, str(tmp_path))

    project_dir = tmp_path / "projects" / "proj-1"
    project_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(project_dir / "sess-file.jsonl", [
        _cc_user_record("sess-abc", "2026-07-20T10:00:00Z", "hello there"),
        _cc_assistant_record("sess-abc", "2026-07-20T10:00:05Z", "hi"),
    ])

    _write_claude_code_skill(tmp_path, "money-admin-messaging")
    _write_claude_code_skill(tmp_path, ".archive")  # must be excluded

    return host.ClaudeCodeAdapter()


# ── Fixture-factory registry keyed by host_name ─────────────────────────
# Extend this (and EXPECTED_*_KEYS above) for any future adapter, rather than adding a
# parallel test suite -- that's the whole point of this unit.

_FIXTURE_BUILDERS = {
    "hermes": _build_hermes_fixture,
    "claude_code": _build_claude_code_fixture,
}


@pytest.fixture
def built_adapter(request, tmp_path, monkeypatch):
    """Indirect fixture: request.param is a host_name key, resolved to a ready-to-query
    adapter instance built against a fixture appropriate to that host's on-disk format."""
    host_name = request.param
    return host_name, _FIXTURE_BUILDERS[host_name](tmp_path, monkeypatch)


# ── The one shared conformance assertion for iter_sessions() ───────────

@pytest.mark.parametrize("built_adapter", ["hermes", "claude_code"], indirect=True)
def test_iter_sessions_shape_is_identical_across_hosts(built_adapter):
    host_name, adapter = built_adapter

    results = list(adapter.iter_sessions())

    assert len(results) >= 1, f"{host_name} fixture produced no sessions to check"
    for session in results:
        assert set(session.keys()) == EXPECTED_SESSION_KEYS, (
            f"{host_name} adapter's iter_sessions() dict has keys "
            f"{sorted(session.keys())}, expected {sorted(EXPECTED_SESSION_KEYS)}"
        )
        assert isinstance(session["messages"], list), (
            f"{host_name} adapter's session['messages'] must be a list, "
            f"got {type(session['messages']).__name__}"
        )
        for msg in session["messages"]:
            assert isinstance(msg, dict), (
                f"{host_name} adapter's session['messages'] must contain only dicts, "
                f"got {type(msg).__name__}"
            )
        # R4 requires "same keys, same types" -- key-set equality alone doesn't catch a
        # host returning started_at as a raw ISO string instead of Hermes's epoch float
        # (a real bug this exact check caught: adversarial review, tests/test_host_claude_code.py).
        assert isinstance(session["started_at"], float), (
            f"{host_name} adapter's session['started_at'] must be a float (epoch seconds, "
            f"matching Hermes's SQLite REAL column), got {type(session['started_at']).__name__}"
        )
        for key in ("session_id", "title", "model", "source"):
            assert isinstance(session[key], str), (
                f"{host_name} adapter's session['{key}'] must be a str, "
                f"got {type(session[key]).__name__}"
            )
        for key in ("message_count", "user_messages", "assistant_messages"):
            assert isinstance(session[key], int), (
                f"{host_name} adapter's session['{key}'] must be an int, "
                f"got {type(session[key]).__name__}"
            )


# ── The one shared conformance assertion for iter_skills() ─────────────

@pytest.mark.parametrize("built_adapter", ["hermes", "claude_code"], indirect=True)
def test_iter_skills_shape_is_identical_across_hosts(built_adapter):
    host_name, adapter = built_adapter

    found = adapter.iter_skills()

    assert len(found) == 1, (
        f"{host_name} fixture: expected exactly the one live skill, with the "
        f"dot-prefixed twin excluded; got {[s.get('name') for s in found]}"
    )
    for skill in found:
        assert set(skill.keys()) == EXPECTED_SKILL_KEYS, (
            f"{host_name} adapter's iter_skills() dict has keys "
            f"{sorted(skill.keys())}, expected {sorted(EXPECTED_SKILL_KEYS)}"
        )
        assert isinstance(skill["category"], str) and skill["category"], (
            f"{host_name} adapter's skill['category'] must be a non-empty string, "
            f"got {skill['category']!r}"
        )
