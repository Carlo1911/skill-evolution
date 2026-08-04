"""Tests for scripts/skill_index.py's scan of the installed-skills tree."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import skill_index


def _write_skill(root, category, name, description="does a thing"):
    skill_dir = root / category / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nBody.\n"
    )


def test_scan_skills_indexes_live_categories(tmp_path):
    _write_skill(tmp_path, "general-skills", "money-admin-messaging")
    _write_skill(tmp_path, "devops", "deploy-helper")

    found = skill_index.scan_skills(str(tmp_path))

    assert {s["name"] for s in found} == {"money-admin-messaging", "deploy-helper"}
    assert {s["category"] for s in found} == {"general-skills", "devops"}


def test_scan_skills_skips_dot_directories(tmp_path):
    """Dot-prefixed dirs under skills/ are internal, not live skill categories.

    `~/.hermes/skills/` really holds `.archive/` (retired skills),
    `.curator_backups/` (timestamped snapshots) and `.hub/` (lockfiles, audit log,
    quarantine). Indexing them tells the analyzer a retired skill "currently exists",
    and -- because the same name can live in both places -- makes
    optimize_skill.py's duplicate-name check reject the live skill as ambiguous.
    """
    _write_skill(tmp_path, "general-skills", "money-admin-messaging")
    _write_skill(tmp_path, ".archive", "money-admin-messaging")
    _write_skill(tmp_path, ".curator_backups", "old-thing")

    found = skill_index.scan_skills(str(tmp_path))

    assert [s["category"] for s in found] == ["general-skills"]
    assert len(found) == 1, "a retired copy must not shadow or duplicate the live skill"


def test_scan_skills_dot_filter_keeps_names_unambiguous(tmp_path):
    """The live skill must resolve to exactly one record even with an archived twin."""
    _write_skill(tmp_path, "general-skills", "money-admin-messaging")
    _write_skill(tmp_path, ".archive", "money-admin-messaging")

    matches = [s for s in skill_index.scan_skills(str(tmp_path))
               if s["name"] == "money-admin-messaging"]

    assert len(matches) == 1
    assert matches[0]["category"] == "general-skills"


def test_scan_skills_returns_empty_for_missing_dir(tmp_path):
    assert skill_index.scan_skills(str(tmp_path / "nope")) == []
