"""Tests for tests/conftest.py's isolation: the real-path guard and the hermetic env.

The guard is what turns a leaking test into a failed run instead of silent data loss (the
conformance fixture that patched the wrong module global pruned the real Hermes state
file on every run), so its detection logic is tested directly on temp paths.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import conftest
import fetch_sessions
import host
import state


def test_guard_detects_a_modified_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{}")
    before = conftest.snapshot([str(path)])

    path.write_text('{"hermes:test-session-1": "x"}')

    assert conftest.changed_paths(before) == [str(path)]


def test_guard_detects_a_created_and_a_deleted_file(tmp_path):
    created, deleted = tmp_path / "new.json", tmp_path / "old.json"
    deleted.write_text("{}")
    before = conftest.snapshot([str(created), str(deleted)])

    created.write_text("{}")
    deleted.unlink()

    assert sorted(conftest.changed_paths(before)) == sorted([str(created), str(deleted)])


def test_guard_detects_a_new_entry_in_a_watched_directory(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    before = conftest.snapshot([str(reports)])

    (reports / "skill-quality-2026-10-17.md").write_text("")

    assert conftest.changed_paths(before) == [str(reports)]


def test_guard_is_quiet_when_nothing_changed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{}")
    before = conftest.snapshot([str(path), str(tmp_path / "missing.json"), str(tmp_path)])

    assert conftest.changed_paths(before) == []


def test_guard_watches_the_real_state_files_and_trees():
    home = os.path.expanduser("~")
    assert os.path.join(home, ".hermes", "skill_evolution_state.json") in conftest.WATCHED_FILES
    assert os.path.join(home, ".hermes", "skills") in conftest.WATCHED_DIRS
    assert os.path.join(conftest.REPO_ROOT, "reports") in conftest.WATCHED_DIRS


def test_every_host_resolves_state_away_from_the_real_home():
    real_home = conftest._REAL_HOME
    for name in ("hermes", "claude_code"):
        assert not host.get_adapter(name)._state_file().startswith(
            os.path.join(real_home, ".hermes")), name
    assert not state.get_state_file().startswith(os.path.join(real_home, ".hermes"))
    assert not fetch_sessions.get_state_file().startswith(os.path.join(real_home, ".hermes"))


def test_shell_configuration_does_not_leak_into_tests():
    leaked = [k for k in os.environ if k.startswith("SKILL_EVOLUTION_")
              and k not in {"SKILL_EVOLUTION_HISTORY_PATH", "SKILL_EVOLUTION_PROPOSALS_DIR",
                            "SKILL_EVOLUTION_QUALITY_REPORT_DIR", "SKILL_EVOLUTION_DB_PATH",
                            "SKILL_EVOLUTION_CLAUDE_CODE_HOME"}]
    assert leaked == []
    assert "CLAUDE_CONFIG_DIR" not in os.environ
