"""Tests for scripts/host.py's HostAdapter registry and HermesAdapter (U1).

Two characterization tests (test_iter_sessions_matches_fetch_sessions_shape,
test_iter_skills_matches_scan_skills_shape) pin today's observable behaviour of
fetch_sessions.fetch_sessions() and skill_index.scan_skills() *before* any logic moves
behind the adapter seam. They are written parametrized over "host" with only "hermes" in
the list so a later unit (U4) can extend the parameter list to a second adapter without
restructuring the test -- see the plan's Execution note for U1.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import fetch_sessions
import host
import skill_index


# ── Fixtures shared by the characterization tests ───────────────────

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
    monkeypatch.delenv(fetch_sessions.DB_PATH_ENV_VAR, raising=False)
    return path


def _write_skill(root, category, name, description="does a thing"):
    skill_dir = root / category / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nBody.\n"
    )


@pytest.mark.parametrize("host_name", ["hermes"])
def test_iter_sessions_matches_fetch_sessions_shape(host_name, db_path):
    """Characterization: pins fetch_sessions()'s exact key set and types against a
    fixture DB, matching the live ~/.hermes/state.db schema (started_at, no
    total_tokens/created_at -- see test_fetch_sessions.py's own fixture docstring)."""
    conn = sqlite3.connect(db_path)
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

    results = fetch_sessions.fetch_sessions(db_path=db_path, lookback_hours=999999, dry_run=True)

    assert len(results) == 1
    session = results[0]
    assert set(session.keys()) == {
        "session_id", "started_at", "title", "model", "source",
        "message_count", "user_messages", "assistant_messages", "messages",
    }
    assert isinstance(session["session_id"], str)
    assert isinstance(session["started_at"], float)
    assert isinstance(session["title"], str)
    assert isinstance(session["model"], str)
    assert isinstance(session["source"], str)
    assert isinstance(session["message_count"], int)
    assert isinstance(session["user_messages"], int)
    assert isinstance(session["assistant_messages"], int)
    assert isinstance(session["messages"], list)
    assert "total_tokens" not in session
    assert "created_at" not in session


@pytest.mark.parametrize("host_name", ["hermes"])
def test_iter_skills_matches_scan_skills_shape(host_name, tmp_path):
    """Characterization: pins scan_skills()'s exact key set/types, including the
    dot-prefixed-category-skipping behaviour, against a fixture tree."""
    _write_skill(tmp_path, "general-skills", "money-admin-messaging")
    _write_skill(tmp_path, ".archive", "money-admin-messaging")

    found = skill_index.scan_skills(str(tmp_path))

    assert len(found) == 1, "the dot-prefixed .archive/ twin must be skipped"
    skill = found[0]
    assert set(skill.keys()) == {"name", "category", "description", "path", "size"}
    assert skill["name"] == "money-admin-messaging"
    assert skill["category"] == "general-skills"
    assert isinstance(skill["description"], str)
    assert isinstance(skill["path"], str)
    assert isinstance(skill["size"], int)


# ── Registry: resolve_host() / get_adapter() ────────────────────────

def test_resolve_host_defaults_to_hermes(monkeypatch):
    monkeypatch.delenv(host.HOST_ENV_VAR, raising=False)
    assert host.resolve_host() == "hermes"


def test_resolve_host_reads_env_var(monkeypatch):
    monkeypatch.setenv(host.HOST_ENV_VAR, "some_other_host")
    assert host.resolve_host() == "some_other_host"


def test_resolve_host_explicit_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv(host.HOST_ENV_VAR, "some_other_host")
    assert host.resolve_host("hermes") == "hermes"


def test_get_adapter_returns_hermes_adapter_by_default(monkeypatch):
    monkeypatch.delenv(host.HOST_ENV_VAR, raising=False)
    adapter = host.get_adapter()
    assert isinstance(adapter, host.HermesAdapter)
    assert adapter.name == "hermes"


def test_get_adapter_unknown_host_raises_with_available_names_listed(monkeypatch):
    monkeypatch.delenv(host.HOST_ENV_VAR, raising=False)
    with pytest.raises(ValueError) as excinfo:
        host.get_adapter("nonexistent_host")
    message = str(excinfo.value)
    assert "nonexistent_host" in message
    assert "hermes" in message


def test_get_adapter_unknown_host_via_env_var_raises(monkeypatch):
    monkeypatch.setenv(host.HOST_ENV_VAR, "nonexistent_host")
    with pytest.raises(ValueError):
        host.get_adapter()


# ── HermesAdapter's own methods (not just the registry) ─────────────

def test_hermes_adapter_iter_sessions_delegates_to_fetch_sessions(db_path, monkeypatch):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sessions (id, source, model, started_at, title) VALUES (?, ?, ?, ?, ?)",
        ("sess-1", "claude-code", "claude", 1000.0, "a session"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv(fetch_sessions.DB_PATH_ENV_VAR, db_path)

    adapter = host.HermesAdapter()
    results = list(adapter.iter_sessions())

    assert len(results) == 1
    assert results[0]["session_id"] == "sess-1"


def test_hermes_adapter_iter_sessions_never_marks_processed(db_path, monkeypatch):
    """iter_sessions() must be a pure read: no side-effect mutation of processed state,
    matching fetch_sessions(dry_run=True)'s contract."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sessions (id, source, model, started_at, title) VALUES (?, ?, ?, ?, ?)",
        ("sess-1", "claude-code", "claude", 1000.0, "a session"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv(fetch_sessions.DB_PATH_ENV_VAR, db_path)

    adapter = host.HermesAdapter()
    list(adapter.iter_sessions())

    assert fetch_sessions.load_processed() == []


def test_hermes_adapter_iter_sessions_since_bounds_the_window(db_path, monkeypatch):
    import time
    from datetime import datetime, timedelta, timezone

    now = time.time()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sessions (id, source, model, started_at, title) VALUES (?, ?, ?, ?, ?)",
        ("sess-old", "claude-code", "claude", now - (10 * 24 * 3600), "old session"),
    )
    conn.execute(
        "INSERT INTO sessions (id, source, model, started_at, title) VALUES (?, ?, ?, ?, ?)",
        ("sess-recent", "claude-code", "claude", now - 3600, "recent session"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv(fetch_sessions.DB_PATH_ENV_VAR, db_path)

    adapter = host.HermesAdapter()
    since = datetime.now(timezone.utc) - timedelta(hours=48)
    results = list(adapter.iter_sessions(since=since))

    assert {r["session_id"] for r in results} == {"sess-recent"}


def test_hermes_adapter_iter_sessions_none_since_is_effectively_unbounded(db_path, monkeypatch):
    now_ts = __import__("time").time()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sessions (id, source, model, started_at, title) VALUES (?, ?, ?, ?, ?)",
        ("sess-very-old", "claude-code", "claude", now_ts - (5 * 365 * 24 * 3600), "ancient session"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv(fetch_sessions.DB_PATH_ENV_VAR, db_path)

    adapter = host.HermesAdapter()
    results = list(adapter.iter_sessions(since=None))

    assert {r["session_id"] for r in results} == {"sess-very-old"}


def test_hermes_adapter_iter_skills_delegates_to_scan_skills(tmp_path, monkeypatch):
    _write_skill(tmp_path, "general-skills", "deploy-helper")
    original_scan_skills = skill_index.scan_skills
    monkeypatch.setattr(host.skill_index, "scan_skills", lambda: original_scan_skills(str(tmp_path)))

    adapter = host.HermesAdapter()
    found = adapter.iter_skills()

    assert {s["name"] for s in found} == {"deploy-helper"}


def test_hermes_adapter_read_skill_body_returns_body_text(tmp_path, monkeypatch):
    _write_skill(tmp_path, "general-skills", "deploy-helper")
    original_scan_skills = skill_index.scan_skills
    monkeypatch.setattr(host.skill_index, "scan_skills", lambda: original_scan_skills(str(tmp_path)))

    adapter = host.HermesAdapter()
    body = adapter.read_skill_body("deploy-helper")

    assert body is not None
    assert "deploy-helper" in body
    assert "Body." in body


def test_hermes_adapter_read_skill_body_returns_none_for_no_match(tmp_path, monkeypatch):
    original_scan_skills = skill_index.scan_skills
    monkeypatch.setattr(host.skill_index, "scan_skills", lambda: original_scan_skills(str(tmp_path)))

    adapter = host.HermesAdapter()
    assert adapter.read_skill_body("does-not-exist") is None


def test_hermes_adapter_read_skill_body_returns_none_for_ambiguous_match(tmp_path, monkeypatch):
    """Two skills sharing a name across categories (e.g. a live skill and its
    .archive/ twin's non-dot-filtered analogue) must not be silently disambiguated."""
    _write_skill(tmp_path, "general-skills", "dup-skill")
    _write_skill(tmp_path, "devops", "dup-skill")
    original_scan_skills = skill_index.scan_skills
    monkeypatch.setattr(host.skill_index, "scan_skills", lambda: original_scan_skills(str(tmp_path)))

    adapter = host.HermesAdapter()
    assert adapter.read_skill_body("dup-skill") is None


def test_hermes_adapter_read_skill_body_returns_none_on_read_failure(tmp_path, monkeypatch):
    _write_skill(tmp_path, "general-skills", "deploy-helper")
    found = skill_index.scan_skills(str(tmp_path))
    monkeypatch.setattr(host.skill_index, "scan_skills", lambda: found)

    # Remove the file after scan_skills() has already recorded its path, simulating a
    # read failure between index and read.
    os.remove(found[0]["path"])

    adapter = host.HermesAdapter()
    assert adapter.read_skill_body("deploy-helper") is None


def test_host_adapter_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        host.HostAdapter()
