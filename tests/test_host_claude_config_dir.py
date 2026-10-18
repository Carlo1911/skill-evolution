"""The Claude Code adapter follows CLAUDE_CONFIG_DIR, Claude Code's own config-dir override.

It always read `~/.claude`, so a profile kept elsewhere was invisible: on the machine this
was found on, the shell's CLAUDE_CONFIG_DIR named `~/.claude-personal` while the adapter
read `~/.claude`, a different profile. SKILL_EVOLUTION_CLAUDE_CODE_HOME still wins.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import host


def test_skill_evolution_home_beats_claude_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(host.CLAUDE_CODE_HOME_ENV_VAR, str(tmp_path / "explicit"))
    monkeypatch.setenv(host.CLAUDE_CONFIG_DIR_ENV_VAR, str(tmp_path / "profile"))

    assert host._resolve_claude_code_home() == str(tmp_path / "explicit")


def test_claude_config_dir_beats_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv(host.CLAUDE_CODE_HOME_ENV_VAR, raising=False)
    monkeypatch.setenv(host.CLAUDE_CONFIG_DIR_ENV_VAR, str(tmp_path / "profile"))

    assert host._resolve_claude_code_home() == str(tmp_path / "profile")


def test_default_is_dot_claude_under_home(tmp_path, monkeypatch):
    monkeypatch.delenv(host.CLAUDE_CODE_HOME_ENV_VAR, raising=False)
    monkeypatch.delenv(host.CLAUDE_CONFIG_DIR_ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert host._resolve_claude_code_home() == str(tmp_path / ".claude")


def test_blank_values_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv(host.CLAUDE_CODE_HOME_ENV_VAR, "  ")
    monkeypatch.setenv(host.CLAUDE_CONFIG_DIR_ENV_VAR, "")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert host._resolve_claude_code_home() == str(tmp_path / ".claude")


def test_a_tilde_in_claude_config_dir_is_expanded(tmp_path, monkeypatch):
    monkeypatch.delenv(host.CLAUDE_CODE_HOME_ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(host.CLAUDE_CONFIG_DIR_ENV_VAR, "~/.claude-personal")

    assert host._resolve_claude_code_home() == str(tmp_path / ".claude-personal")


def test_skills_sessions_and_state_all_follow_claude_config_dir(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    (profile / "skills" / "demo").mkdir(parents=True)
    (profile / "skills" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n")
    monkeypatch.delenv(host.CLAUDE_CODE_HOME_ENV_VAR, raising=False)
    monkeypatch.delenv("SKILL_EVOLUTION_STATE_FILE", raising=False)
    monkeypatch.setenv(host.CLAUDE_CONFIG_DIR_ENV_VAR, str(profile))
    adapter = host.get_adapter("claude_code")

    assert [s["name"] for s in adapter.iter_skills()] == ["demo"]
    assert adapter._state_file() == str(profile / "skill_evolution_state.json")
