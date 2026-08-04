"""Integration tests for per-host processed-session state.

Verifies that each adapter owns its own state file, that the universal override
(SKILL_EVOLUTION_STATE_FILE) redirects whichever host is active, and that two hosts
don't see each other's entries.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import host
import state


@pytest.fixture
def hermes_state_file(tmp_path, monkeypatch):
    """Redirect Hermes state to a tmp file."""
    path = str(tmp_path / "hermes_state.json")
    monkeypatch.setattr(state, "STATE_FILE", path)
    monkeypatch.setattr(host.state, "STATE_FILE", path)
    return path


@pytest.fixture
def cc_home(tmp_path, monkeypatch):
    """Redirect Claude Code home to a tmp directory."""
    monkeypatch.setenv(host.CLAUDE_CODE_HOME_ENV_VAR, str(tmp_path))
    return tmp_path


class TestPerHostFileIsolation:
    """Each adapter's state lives in its own file by default."""

    def test_hermes_state_file_is_under_hermes_home(self, hermes_state_file):
        adapter = host.HermesAdapter()
        assert adapter._state_file() == hermes_state_file

    def test_claude_code_state_file_is_under_cc_home(self, cc_home):
        adapter = host.ClaudeCodeAdapter()
        expected = str(cc_home / "skill_evolution_state.json")
        assert adapter._state_file() == expected

    def test_two_hosts_have_different_default_state_files(self, hermes_state_file, cc_home):
        hermes_adapter = host.HermesAdapter()
        cc_adapter = host.ClaudeCodeAdapter()

        hermes_path = hermes_adapter._state_file()
        cc_path = cc_adapter._state_file()

        assert hermes_path != cc_path, (
            "Hermes and Claude Code must have different default state files"
        )

    def test_mark_and_read_are_isolated_between_hosts(self, hermes_state_file, cc_home):
        """Marking sessions on one host must not be visible to the other."""
        hermes_adapter = host.HermesAdapter()
        cc_adapter = host.ClaudeCodeAdapter()

        hermes_adapter.mark_processed(["hermes-session-1"])
        cc_adapter.mark_processed(["cc-session-1"])

        hermes_processed = hermes_adapter.iter_processed()
        cc_processed = cc_adapter.iter_processed()

        assert "hermes-session-1" in hermes_processed
        assert "cc-session-1" not in hermes_processed, (
            "Hermes adapter saw Claude Code's session — state files are not isolated"
        )
        assert "cc-session-1" in cc_processed
        assert "hermes-session-1" not in cc_processed, (
            "Claude Code adapter saw Hermes's session — state files are not isolated"
        )


class TestUniversalOverride:
    """SKILL_EVOLUTION_STATE_FILE redirects whichever host is active."""

    def test_override_redirects_hermes(self, tmp_path, monkeypatch):
        override_path = str(tmp_path / "shared_override.json")
        monkeypatch.setenv(state.STATE_FILE_ENV_VAR, override_path)

        hermes_adapter = host.HermesAdapter()
        assert hermes_adapter._state_file() == override_path

    def test_override_redirects_claude_code(self, cc_home, monkeypatch):
        override_path = str(cc_home / "shared_override.json")
        monkeypatch.setenv(state.STATE_FILE_ENV_VAR, override_path)

        cc_adapter = host.ClaudeCodeAdapter()
        assert cc_adapter._state_file() == override_path

    def test_override_makes_both_hosts_share_one_file(self, tmp_path, monkeypatch):
        """When the override is set, both hosts write to the same file.

        The shared file uses the flat dict shape (with timestamps) to support host
        prefixes. The documented shape ({"processed_sessions": [...]}) doesn't support
        multi-host because it predates the host concept.
        """
        override_path = str(tmp_path / "shared.json")
        monkeypatch.setenv(state.STATE_FILE_ENV_VAR, override_path)

        # Pre-seed with flat dict shape (the deployed shape that supports host prefixes)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with open(override_path, "w") as f:
            json.dump({"existing": now}, f)

        hermes_adapter = host.HermesAdapter()
        cc_adapter = host.ClaudeCodeAdapter()

        # Both should point to the override
        assert hermes_adapter._state_file() == override_path
        assert cc_adapter._state_file() == override_path

        # Mark on both — they share the file but host-prefix keeps them separate
        hermes_adapter.mark_processed(["hermes-sess"])
        cc_adapter.mark_processed(["cc-sess"])

        # Both see their own entries (via host-prefix)
        assert "hermes-sess" in hermes_adapter.iter_processed()
        assert "cc-sess" in cc_adapter.iter_processed()

        # But each host only sees its own namespace
        assert "cc-sess" not in hermes_adapter.iter_processed()
        assert "hermes-sess" not in cc_adapter.iter_processed()


class TestPathParameterization:
    """State functions accept an explicit path parameter."""

    def test_load_processed_with_explicit_path(self, tmp_path):
        path = str(tmp_path / "custom_state.json")
        # Write a known state
        with open(path, "w") as f:
            json.dump({"processed_sessions": ["custom-1", "custom-2"]}, f)

        result = state.load_processed(path=path)
        assert set(result) == {"custom-1", "custom-2"}

    def test_mark_processed_with_explicit_path(self, tmp_path):
        path = str(tmp_path / "custom_state.json")

        state.mark_processed(["marked-1"], path=path)

        with open(path) as f:
            data = json.load(f)
        assert "marked-1" in data["processed_sessions"]

    def test_two_paths_are_independent(self, tmp_path):
        path_a = str(tmp_path / "state_a.json")
        path_b = str(tmp_path / "state_b.json")

        state.mark_processed(["a-session"], host="host_a", path=path_a)
        state.mark_processed(["b-session"], host="host_b", path=path_b)

        a_processed = state.load_processed(host="host_a", path=path_a)
        b_processed = state.load_processed(host="host_b", path=path_b)

        assert "a-session" in a_processed
        assert "b-session" not in a_processed
        assert "b-session" in b_processed
        assert "a-session" not in b_processed


class TestFetchSessionsForHostRoutesThroughAdapter:
    """fetch_sessions_for_host() uses adapter state methods, not module functions."""

    def test_fetch_sessions_for_host_uses_adapter_state(self, cc_home, monkeypatch):
        """Verify that fetch_sessions_for_host calls adapter.iter_processed(), etc."""
        import fetch_sessions

        cc_adapter = host.ClaudeCodeAdapter()

        # Pre-mark a session as processed via the adapter
        cc_adapter.mark_processed(["already-processed-session"])

        # fetch_sessions_for_host should filter it out
        results = fetch_sessions.fetch_sessions_for_host(
            "claude_code",
            lookback_hours=10000,
            dry_run=True,
        )

        # The already-processed session should not appear
        result_ids = [s["session_id"] for s in results]
        assert "already-processed-session" not in result_ids
