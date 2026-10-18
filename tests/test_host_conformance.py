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
import shutil
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


def _skills_root(tmp_path):
    """Every fixture builder roots its host's skill tree here, so the archive tests below
    can add to (or remove) `<skills root>/.archive` without knowing which host they run on."""
    return tmp_path / "skills"


def _write_archived_skill(archive_dir, *parts):
    """Write a minimal SKILL.md at archive_dir/<parts...>/SKILL.md, named after the last part."""
    skill_dir = archive_dir.joinpath(*parts)
    skill_dir.mkdir(parents=True, exist_ok=True)
    name = parts[-1]
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: retired\n---\n\n# {name}\n\nBody.\n"
    )


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

    skills_root = _skills_root(tmp_path)
    _write_hermes_skill(skills_root, "general-skills", "money-admin-messaging")
    _write_hermes_skill(skills_root, ".archive", "money-admin-messaging")  # must be excluded

    # iter_skills() calls scan_skills() with no arguments, and scan_skills's default root
    # was bound at definition time, so the zero-arg call is redirected here. An explicit
    # root (iter_archived_skills() passes `<SKILLS_DIR>/.archive`) is forwarded untouched,
    # and SKILLS_DIR itself is redirected because that method reads it at call time.
    original_scan_skills = skill_index.scan_skills
    monkeypatch.setattr(
        host.skill_index, "scan_skills",
        lambda skills_dir=str(skills_root), extra_roots=None: original_scan_skills(skills_dir, extra_roots),
    )
    monkeypatch.setattr(host.skill_index, "SKILLS_DIR", str(skills_root))

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
    # A real archived twin, where _archive_skill() puts one -- two levels down, so
    # iter_skills()' flat walk never reaches it whatever its dot filter does.
    _write_archived_skill(_skills_root(tmp_path) / ".archive", "money-admin-messaging")
    # Which is why the skill-level dot filter needs its own case: a dot-prefixed skill
    # directory holding a SKILL.md directly, which only that filter excludes.
    _write_claude_code_skill(tmp_path, ".money-admin-messaging-draft", name="money-admin-messaging")

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


# ── The one shared conformance assertion for the write side (P2-2) ─────

@pytest.mark.parametrize("built_adapter", ["hermes", "claude_code"], indirect=True)
def test_supports_write_is_a_bool_on_every_adapter(built_adapter):
    host_name, adapter = built_adapter

    assert isinstance(adapter.supports_write, bool), (
        f"{host_name} adapter's supports_write must be a bool, "
        f"got {type(adapter.supports_write).__name__}"
    )


@pytest.mark.parametrize("built_adapter", ["hermes", "claude_code"], indirect=True)
def test_apply_skill_write_fails_closed_on_unknown_type_never_raises(built_adapter):
    """The write contract is fail-closed on BOTH adapters: an unhandled proposal type
    returns can_apply: False with a reason, never raises -- apply_proposal() must not
    be able to crash on a plan its host doesn't understand."""
    host_name, adapter = built_adapter

    result = adapter.apply_skill_write(
        {"type": "no_such_type", "target_skill": "x", "proposal_id": "x", "changes": []}
    )

    assert result["can_apply"] is False, f"{host_name} adapter applied an unknown type"
    assert isinstance(result["reason"], str) and result["reason"]
    assert "no_such_type" in result["reason"]


# ── Conformance assertions for processed-session state (P2-2 follow-up) ─

@pytest.mark.parametrize("built_adapter", ["hermes", "claude_code"], indirect=True)
def test_iter_processed_returns_a_list(built_adapter):
    """Every adapter's iter_processed() returns a list (possibly empty), never raises."""
    host_name, adapter = built_adapter

    result = adapter.iter_processed()

    assert isinstance(result, list), (
        f"{host_name} adapter's iter_processed() must return a list, "
        f"got {type(result).__name__}"
    )


@pytest.mark.parametrize("built_adapter", ["hermes", "claude_code"], indirect=True)
def test_mark_processed_makes_sessions_visible(built_adapter):
    """mark_processed() followed by iter_processed() must show the marked IDs."""
    host_name, adapter = built_adapter

    adapter.mark_processed(["test-session-1", "test-session-2"])
    processed = adapter.iter_processed()

    assert "test-session-1" in processed, (
        f"{host_name} adapter: mark_processed wrote but iter_processed didn't see it"
    )
    assert "test-session-2" in processed


@pytest.mark.parametrize("built_adapter", ["hermes", "claude_code"], indirect=True)
def test_prune_processed_never_raises(built_adapter):
    """prune_processed() must not raise even with no retention configured (no-op)."""
    host_name, adapter = built_adapter

    # Should not raise — either a no-op (no retention) or actual pruning
    adapter.prune_processed()
    adapter.prune_processed(retention="30d", keep_ids=["keep-me"])


@pytest.mark.parametrize("built_adapter", ["hermes", "claude_code"], indirect=True)
def test_state_file_returns_a_string(built_adapter):
    """Every adapter's _state_file() returns a non-empty string path."""
    host_name, adapter = built_adapter

    path = adapter._state_file()

    assert isinstance(path, str) and path, (
        f"{host_name} adapter's _state_file() must return a non-empty string, "
        f"got {path!r}"
    )


# ── Conformance assertions for archived skills (R10) ───────────────────
# The diagnostic asks the active adapter whether a skill is archived rather than probing
# one host's paths, so every adapter must answer from its own `<skills root>/.archive`.

@pytest.mark.parametrize("built_adapter", ["hermes", "claude_code"], indirect=True)
def test_archived_twin_is_reported_as_archived_and_not_as_live(built_adapter):
    host_name, adapter = built_adapter

    archived = adapter.iter_archived_skills()
    live_paths = {s["path"] for s in adapter.iter_skills()}

    assert [s["name"] for s in archived] == ["money-admin-messaging"], (
        f"{host_name} adapter's iter_archived_skills() must report the archived twin, "
        f"got {[s.get('name') for s in archived]}"
    )
    for skill in archived:
        assert set(skill.keys()) == EXPECTED_SKILL_KEYS, (
            f"{host_name} adapter's iter_archived_skills() dict has keys "
            f"{sorted(skill.keys())}, expected {sorted(EXPECTED_SKILL_KEYS)}"
        )
        assert skill["path"] not in live_paths, (
            f"{host_name} adapter reports {skill['path']} as both archived and live"
        )


@pytest.mark.parametrize("built_adapter", ["hermes", "claude_code"], indirect=True)
def test_archived_skill_in_a_nested_group_is_reported(built_adapter, tmp_path):
    host_name, adapter = built_adapter
    _write_archived_skill(_skills_root(tmp_path) / ".archive", "retired-group", "old-helper")

    names = {s["name"] for s in adapter.iter_archived_skills()}

    assert "old-helper" in names, (
        f"{host_name} adapter missed an archived skill under .archive/<group>/<skill>/, "
        f"got {sorted(names)}"
    )


@pytest.mark.parametrize("built_adapter", ["hermes", "claude_code"], indirect=True)
def test_no_archive_directory_reports_no_archived_skills(built_adapter, tmp_path):
    host_name, adapter = built_adapter
    shutil.rmtree(_skills_root(tmp_path) / ".archive")

    assert adapter.iter_archived_skills() == [], (
        f"{host_name} adapter must report no archived skills when .archive is absent"
    )


def test_adapter_without_an_archive_reports_no_archived_skills():
    """A host that does not implement archiving inherits the empty default, so the
    diagnostic says "unresolved" for it, never "archived"."""

    class _NoArchiveAdapter(host.HostAdapter):
        name = "no_archive"

        def iter_sessions(self, since=None):
            return []

        def iter_skills(self, extra_roots=None):
            return []

    assert _NoArchiveAdapter().iter_archived_skills() == []
