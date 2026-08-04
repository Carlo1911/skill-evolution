"""Tests for ClaudeCodeAdapter.apply_skill_write() (P2-2, U3).

Claude Code "applies" a proposal by writing skill files directly under
SKILL_EVOLUTION_CLAUDE_CODE_HOME/skills/ -- no skill_manage instructions. These tests
exercise every write path against an isolated tmp home: create, improve (body +
description), deprecate/merge (archive into .archive/), the symlink refusal, and the
never-raise fail-closed contract.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import host


# ── Fixtures / plan builders ─────────────────────────────────────────

@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    """Point the adapter at an isolated synthetic ~/.claude-shaped tree."""
    monkeypatch.setenv(host.CLAUDE_CODE_HOME_ENV_VAR, str(tmp_path))
    return tmp_path


def _write_skill(root, skill_dir_name, name=None, description="does a thing", body="Body text."):
    skill_dir = root / "skills" / skill_dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_name = name if name is not None else skill_dir_name
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: {description}\n---\n\n# {skill_name}\n\n{body}\n"
    )
    return skill_dir


def _improve_plan(target="test-skill", changes=None, **overrides):
    plan = {
        "type": "improve_existing",
        "target_skill": target,
        "proposal_id": "fixture-001",
        "changes": changes if changes is not None else [
            {"field": "body", "old_value": "Old body.", "new_value": "New body.", "description": None},
        ],
        "body": None,
    }
    plan.update(overrides)
    return plan


def _create_plan(name="brand-new-skill", body=None, **overrides):
    plan = {
        "type": "create_new",
        "target_skill": None,
        "proposal_id": "create-001",
        "body_name": name,
        "body": body if body is not None else (
            "---\nname: brand-new-skill\ndescription: A brand new skill.\n---\n\n"
            "# brand-new-skill\n\nGuidance body here."
        ),
        "description": "A brand new skill.",
        "category": "general-skills",
        "changes": [],
    }
    plan.update(overrides)
    return plan


def _deprecate_plan(target="stale-skill", **overrides):
    plan = {
        "type": "deprecate_skill",
        "target_skill": target,
        "proposal_id": "dep-001",
        "changes": [],
        "body": None,
    }
    plan.update(overrides)
    return plan


def _merge_plan(umbrella="umbrella-skill", sources=("skill-a", "skill-b"), **overrides):
    plan = {
        "type": "merge_skills",
        "target_skill": umbrella,
        "proposal_id": "merge-001",
        "changes": [
            {"field": f"source_{i}", "new_value": src, "old_value": None, "description": None}
            for i, src in enumerate(sources)
        ],
        "body": None,
    }
    plan.update(overrides)
    return plan


# ── create ───────────────────────────────────────────────────────────

def test_create_writes_new_skill_file(claude_home):
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_create_plan())

    assert result["can_apply"] is True
    assert result["applied_by"] == "direct"
    skill_md = claude_home / "skills" / "brand-new-skill" / "SKILL.md"
    assert result["writes"] == [str(skill_md)]
    assert skill_md.read_text().startswith("---")


def test_create_refuses_existing_name(claude_home):
    _write_skill(claude_home, "brand-new-skill")
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_create_plan())

    assert result["can_apply"] is False
    assert "already exists" in result["reason"]


@pytest.mark.parametrize("bad_name", ["", ".hidden", "a/b", "a\\b"])
def test_create_refuses_invalid_names(claude_home, bad_name):
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_create_plan(name=bad_name))

    assert result["can_apply"] is False
    assert "skill name" in result["reason"].lower()


def test_create_refuses_empty_body(claude_home):
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_create_plan(body=""))

    assert result["can_apply"] is False
    assert "body" in result["reason"].lower()


# ── improve ──────────────────────────────────────────────────────────

def test_improve_body_old_value_exact_replace(claude_home):
    _write_skill(claude_home, "test-skill", body="Old body.")
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_improve_plan())

    assert result["can_apply"] is True
    content = (claude_home / "skills" / "test-skill" / "SKILL.md").read_text()
    assert "New body." in content
    assert "Old body." not in content


def test_improve_body_old_value_mismatch_refuses_without_writing(claude_home):
    _write_skill(claude_home, "test-skill", body="Different body.")
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_improve_plan())

    assert result["can_apply"] is False
    assert "not found" in result["reason"]
    content = (claude_home / "skills" / "test-skill" / "SKILL.md").read_text()
    assert "Different body." in content  # untouched


def test_improve_body_full_replacement_without_old_value(claude_home):
    _write_skill(claude_home, "test-skill", body="Old body.")
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_improve_plan(changes=[
        {"field": "body", "old_value": "", "new_value": "---\nname: test-skill\n---\n\nEntirely new.", "description": None},
    ]))

    assert result["can_apply"] is True
    content = (claude_home / "skills" / "test-skill" / "SKILL.md").read_text()
    assert "Entirely new." in content
    assert "Old body." not in content


def test_improve_description_rewrites_frontmatter_line(claude_home):
    _write_skill(claude_home, "test-skill", description="Old description.")
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_improve_plan(changes=[
        {"field": "description", "old_value": "Old description.", "new_value": "New description.", "description": None},
    ]))

    assert result["can_apply"] is True
    content = (claude_home / "skills" / "test-skill" / "SKILL.md").read_text()
    assert "description: New description." in content
    assert "Old description." not in content


def test_improve_unsupported_field_refuses(claude_home):
    _write_skill(claude_home, "test-skill")
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_improve_plan(changes=[
        {"field": "category", "old_value": "user", "new_value": "devops", "description": None},
    ]))

    assert result["can_apply"] is False
    assert "unsupported" in result["reason"]


def test_improve_unknown_skill_refuses(claude_home):
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_improve_plan(target="no-such-skill"))

    assert result["can_apply"] is False
    assert "not found" in result["reason"]


def test_improve_refuses_symlinked_skill_dir(claude_home, tmp_path):
    """Owner decision 3: 38/41 real ~/.claude/skills/ entries are symlinks into
    ~/.agents/skills/ -- writing through one would mutate a tree this adapter does not
    own. Refuse with the reason naming the symlink."""
    external = tmp_path / "agents-skills"
    _write_skill(external, "test-skill", body="Old body.")
    (claude_home / "skills").mkdir(parents=True, exist_ok=True)
    os.symlink(external / "skills" / "test-skill", claude_home / "skills" / "test-skill")

    adapter = host.ClaudeCodeAdapter()
    assert len(adapter.iter_skills()) == 1  # readable through the symlink...

    result = adapter.apply_skill_write(_improve_plan())

    assert result["can_apply"] is False
    assert "symlink" in result["reason"]
    assert (external / "skills" / "test-skill" / "SKILL.md").read_text() == (
        "---\nname: test-skill\ndescription: does a thing\n---\n\n# test-skill\n\nOld body.\n"
    )


# ── deprecate / merge (archive into .archive/) ───────────────────────

def test_deprecate_archives_skill_dir(claude_home):
    _write_skill(claude_home, "stale-skill")
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_deprecate_plan())

    assert result["can_apply"] is True
    assert not (claude_home / "skills" / "stale-skill").exists()
    archived = claude_home / "skills" / ".archive" / "stale-skill"
    assert archived.exists()
    assert result["writes"] == [str(archived)]
    assert adapter.iter_skills() == []  # the read side skips .archive/: skill vanished


def test_deprecate_timestamp_suffixes_archive_on_collision(claude_home):
    _write_skill(claude_home, "stale-skill")
    _write_skill(claude_home, "other-skill")
    # Pre-existing archive entry under the same name
    archive_base = claude_home / "skills" / ".archive"
    archive_base.mkdir(parents=True, exist_ok=True)
    (archive_base / "stale-skill").mkdir()

    adapter = host.ClaudeCodeAdapter()
    result = adapter.apply_skill_write(_deprecate_plan())

    assert result["can_apply"] is True
    remaining = [p for p in archive_base.iterdir() if p.is_dir()]
    assert len(remaining) == 2
    assert any(p.name == "stale-skill" for p in remaining)
    assert any(p.name.startswith("stale-skill-") for p in remaining)


def test_deprecate_unknown_skill_refuses(claude_home):
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_deprecate_plan(target="no-such-skill"))

    assert result["can_apply"] is False
    assert "not found" in result["reason"]


def test_merge_archives_each_source_and_keeps_umbrella(claude_home):
    _write_skill(claude_home, "skill-a")
    _write_skill(claude_home, "skill-b")
    _write_skill(claude_home, "umbrella-skill")
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_merge_plan())

    assert result["can_apply"] is True
    assert not (claude_home / "skills" / "skill-a").exists()
    assert not (claude_home / "skills" / "skill-b").exists()
    assert (claude_home / "skills" / "umbrella-skill").exists()
    assert (claude_home / "skills" / ".archive" / "skill-a").exists()
    assert (claude_home / "skills" / ".archive" / "skill-b").exists()


def test_merge_validates_absorbed_into_umbrella_exists(claude_home):
    _write_skill(claude_home, "skill-a")
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_merge_plan(umbrella="no-such-umbrella"))

    assert result["can_apply"] is False
    assert "absorbed_into" in result["reason"]
    assert (claude_home / "skills" / "skill-a").exists()  # nothing archived


def test_merge_refuses_source_same_as_umbrella(claude_home):
    _write_skill(claude_home, "skill-a")
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write(_merge_plan(umbrella="skill-a", sources=("skill-a",)))

    assert result["can_apply"] is False
    assert "absorbed_into" in result["reason"]


# ── the never-raise fail-closed contract ─────────────────────────────

def test_apply_skill_write_never_raises_on_malformed_plan(claude_home):
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write({})  # no "type" key at all

    assert result["can_apply"] is False
    assert "reason" in result


def test_apply_skill_write_unknown_type_never_raises(claude_home):
    adapter = host.ClaudeCodeAdapter()

    result = adapter.apply_skill_write({"type": "no_such_type", "target_skill": "x", "changes": []})

    assert result["can_apply"] is False
    assert "no_such_type" in result["reason"]
