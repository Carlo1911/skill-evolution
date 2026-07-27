"""Three ways the pipeline lost information quietly, from the 2026-07-26 backlog.

Grouped because they share a failure shape rather than a code path: each degraded without
saying so, which is the mode this repo has repeatedly decided against (see env_float's
stderr fallback, and R21's fail-closed posture).

1. list_proposals() swallowed every parse error, so a model-authored `status` value made a
   proposal vanish from --list and --retroactive with no signal. Observed for real.
2. sessions_for_skill() raised sqlite3.OperationalError on a missing DB -- and sqlite's
   connect() had already created a stray empty file at the bad path.
3. STATE_FILE was a module constant with no override, so isolating state required
   monkeypatching internals.
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import fetch_sessions
import proposal as proposal_module
import state as state_module

VALID = """---
proposal_id: p-good
created_at: '2026-07-26T00:00:00Z'
type: improve_existing
target_skill: demo
confidence: 0.8
status: proposed
summary: A valid proposal
---

# Body
"""

# `already_covered` is not in ProposalStatus. Seen for real in the ai-memory-stack
# proposals directory, written by the analysing model.
INVALID_STATUS = VALID.replace("status: proposed", "status: already_covered").replace(
    "proposal_id: p-good", "proposal_id: p-bad")


# ── 1. A proposal must never disappear silently ─────────────────────────

def test_unparseable_proposal_is_reported_not_dropped(tmp_path, capsys):
    (tmp_path / "good.md").write_text(VALID)
    (tmp_path / "bad.md").write_text(INVALID_STATUS)

    proposals = proposal_module.list_proposals(directory=str(tmp_path))

    assert [p.proposal_id for p in proposals] == ["p-good"]
    err = capsys.readouterr().err
    assert "bad.md" in err
    assert "already_covered" in err


def test_one_bad_file_does_not_take_down_the_listing(tmp_path, capsys):
    """Skipping is still correct behaviour -- it just has to be audible."""
    (tmp_path / "a.md").write_text(VALID.replace("p-good", "p-a"))
    (tmp_path / "b.md").write_text("not a proposal at all")
    (tmp_path / "c.md").write_text(VALID.replace("p-good", "p-c"))

    proposals = proposal_module.list_proposals(directory=str(tmp_path))

    assert sorted(p.proposal_id for p in proposals) == ["p-a", "p-c"]
    assert "b.md" in capsys.readouterr().err


def test_a_clean_directory_warns_about_nothing(tmp_path, capsys):
    (tmp_path / "good.md").write_text(VALID)

    proposal_module.list_proposals(directory=str(tmp_path))

    assert capsys.readouterr().err == ""


# ── 2. sessions_for_skill() degrades instead of crashing ────────────────

def test_missing_db_returns_empty_and_warns(tmp_path, capsys):
    missing = str(tmp_path / "nope.db")

    assert fetch_sessions.sessions_for_skill("demo", db_path=missing) == []
    assert "not found" in capsys.readouterr().err


def test_missing_db_does_not_get_created(tmp_path):
    """sqlite3.connect() creates the file for a missing path, so a typo'd --db-path left a
    stray 0-byte DB behind *and* produced a confusing 'no such table' error."""
    missing = str(tmp_path / "nope.db")

    fetch_sessions.sessions_for_skill("demo", db_path=missing)

    assert not os.path.exists(missing)


def test_db_without_the_expected_schema_returns_empty_and_warns(tmp_path, capsys):
    path = str(tmp_path / "empty.db")
    sqlite3.connect(path).close()  # exists, but has no tables

    assert fetch_sessions.sessions_for_skill("demo", db_path=path) == []
    assert "not readable" in capsys.readouterr().err


def test_a_real_but_empty_schema_is_not_treated_as_an_error(tmp_path, capsys):
    """A valid DB with zero matching sessions is a normal answer, not a misconfiguration,
    and must not produce a warning."""
    path = str(tmp_path / "state.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, "
        "title TEXT, started_at REAL);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
        "content TEXT, tool_calls TEXT, timestamp REAL);"
    )
    conn.commit()
    conn.close()

    assert fetch_sessions.sessions_for_skill("demo", db_path=path) == []
    assert capsys.readouterr().err == ""


# ── 3. STATE_FILE is redirectable without monkeypatching ────────────────

@pytest.mark.parametrize("module", [fetch_sessions, state_module],
                         ids=["fetch_sessions", "state"])
def test_state_file_env_override_is_honoured(module, tmp_path, monkeypatch):
    """Both copies must behave identically -- they are duplicated on purpose."""
    target = tmp_path / "isolated_state.json"
    monkeypatch.setenv(module.STATE_FILE_ENV_VAR, str(target))

    module.mark_processed(["s1", "s2"])

    assert target.exists()
    assert set(module.load_processed()) == {"s1", "s2"}


@pytest.mark.parametrize("module", [fetch_sessions, state_module],
                         ids=["fetch_sessions", "state"])
def test_unset_env_still_falls_back_to_the_module_constant(module, tmp_path, monkeypatch):
    """Falling back to the global rather than the literal path keeps the existing
    monkeypatch-STATE_FILE approach working, which several tests rely on."""
    monkeypatch.delenv(module.STATE_FILE_ENV_VAR, raising=False)
    monkeypatch.setattr(module, "STATE_FILE", str(tmp_path / "patched.json"))

    assert module.get_state_file() == str(tmp_path / "patched.json")


@pytest.mark.parametrize("module", [fetch_sessions, state_module],
                         ids=["fetch_sessions", "state"])
def test_blank_env_value_is_ignored(module, monkeypatch):
    """An exported-but-empty variable must not redirect state to "" ."""
    monkeypatch.setenv(module.STATE_FILE_ENV_VAR, "   ")

    assert module.get_state_file() == module.STATE_FILE
