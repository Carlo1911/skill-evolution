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


def _write_flat_skill(root, name, description="does a thing"):
    """Write a skill directly under a root (no category level), like ~/.agents/skills/."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nBody.\n"
    )


def test_scan_skills_extra_roots_flat_layout(tmp_path):
    """A flat extra root (root/<skill>/SKILL.md, e.g. ~/.agents/skills) is scanned too."""
    primary = tmp_path / "primary"
    _write_skill(primary, "general-skills", "money-admin-messaging")
    extra = tmp_path / "agents-skills"
    _write_flat_skill(extra, "ce-plan")
    _write_flat_skill(extra, "post-writer-sms")

    found = skill_index.scan_skills(str(primary), extra_roots=[str(extra)])

    names = {s["name"] for s in found}
    assert names == {"money-admin-messaging", "ce-plan", "post-writer-sms"}
    # flat-root skills get the root's basename as category
    ce = next(s for s in found if s["name"] == "ce-plan")
    assert ce["category"] == "agents-skills"


def test_scan_skills_extra_roots_nested_layout(tmp_path):
    """A category-nested extra root (root/<category>/<skill>/SKILL.md) is scanned too."""
    primary = tmp_path / "primary"
    _write_skill(primary, "general-skills", "money-admin-messaging")
    extra = tmp_path / "extra-nested"
    _write_skill(extra, "devops", "deploy-helper")

    found = skill_index.scan_skills(str(primary), extra_roots=[str(extra)])

    assert {s["name"] for s in found} == {"money-admin-messaging", "deploy-helper"}
    deploy = next(s for s in found if s["name"] == "deploy-helper")
    assert deploy["category"] == "devops"


def test_scan_skills_extra_roots_dedupe_primary_wins(tmp_path):
    """A name present in both the primary tree and an extra root resolves to the primary."""
    primary = tmp_path / "primary"
    _write_skill(primary, "general-skills", "money-admin-messaging")
    extra = tmp_path / "agents-skills"
    _write_flat_skill(extra, "money-admin-messaging", description="external copy")

    found = skill_index.scan_skills(str(primary), extra_roots=[str(extra)])

    matches = [s for s in found if s["name"] == "money-admin-messaging"]
    assert len(matches) == 1
    assert matches[0]["category"] == "general-skills"
    assert "external copy" not in matches[0]["description"]


def test_scan_skills_extra_roots_multiple(tmp_path):
    """1+ extra roots all contribute; order is stable (primary, then extras in order)."""
    primary = tmp_path / "primary"
    _write_skill(primary, "general-skills", "money-admin-messaging")
    extra_a = tmp_path / "agents-a"
    _write_flat_skill(extra_a, "ce-plan")
    extra_b = tmp_path / "agents-b"
    _write_flat_skill(extra_b, "post-writer-sms")

    found = skill_index.scan_skills(str(primary), extra_roots=[str(extra_a), str(extra_b)])

    assert [s["name"] for s in found] == ["money-admin-messaging", "ce-plan", "post-writer-sms"]


def test_scan_skills_extra_roots_missing_dir_ignored(tmp_path):
    """A non-existent extra root is skipped, not fatal."""
    primary = tmp_path / "primary"
    _write_skill(primary, "general-skills", "money-admin-messaging")

    found = skill_index.scan_skills(str(primary), extra_roots=[str(tmp_path / "nope")])

    assert [s["name"] for s in found] == ["money-admin-messaging"]


def test_scan_skills_extra_roots_skips_dot_dirs(tmp_path):
    """Dot-prefixed entries inside an extra root are internal, not live skills."""
    primary = tmp_path / "primary"
    _write_skill(primary, "general-skills", "money-admin-messaging")
    extra = tmp_path / "agents-skills"
    _write_flat_skill(extra, "ce-plan")
    _write_flat_skill(extra / ".archive", "retired-thing")

    found = skill_index.scan_skills(str(primary), extra_roots=[str(extra)])

    assert {s["name"] for s in found} == {"money-admin-messaging", "ce-plan"}


def test_scan_skills_extra_roots_expands_tilde(tmp_path, monkeypatch):
    """~ in an extra root path is expanded to $HOME."""
    primary = tmp_path / "primary"
    _write_skill(primary, "general-skills", "money-admin-messaging")
    extra = tmp_path / "agents-skills"
    _write_flat_skill(extra, "ce-plan")
    monkeypatch.setenv("HOME", str(tmp_path))

    found = skill_index.scan_skills(str(primary), extra_roots=["~/agents-skills"])

    assert {s["name"] for s in found} == {"money-admin-messaging", "ce-plan"}


def test_scan_skills_no_extra_roots_identical_to_before(tmp_path):
    """Without extra_roots the result is exactly the historical single-root scan."""
    _write_skill(tmp_path, "general-skills", "money-admin-messaging")
    _write_skill(tmp_path, ".archive", "retired-thing")

    assert skill_index.scan_skills(str(tmp_path)) == skill_index.scan_skills(
        str(tmp_path), extra_roots=None
    )
    assert skill_index.scan_skills(str(tmp_path)) == skill_index.scan_skills(
        str(tmp_path), extra_roots=[]
    )


def _write_flat_skill(root, name, description="does a thing"):
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nBody.\n"
    )
    return skill_dir


def test_scan_skills_primary_root_indexes_flat_skills(tmp_path):
    """`<root>/<skill>/SKILL.md` directly under the primary root is a live skill.

    The primary walk used to treat every top-level dir as a category, so a flat skill's
    own SKILL.md was never read and the analyzer was told it did not exist.
    """
    root = tmp_path / "skills"
    _write_skill(root, "general-skills", "money-admin-messaging")
    flat = _write_flat_skill(root, "deploy-helper")
    (flat / "references").mkdir()
    (flat / "references" / "notes.md").write_text("not a skill")

    found = skill_index.scan_skills(str(root))

    by_name = {s["name"]: s for s in found}
    assert set(by_name) == {"money-admin-messaging", "deploy-helper"}
    assert by_name["deploy-helper"]["category"] == "skills"
    assert by_name["deploy-helper"]["path"] == str(flat / "SKILL.md")


def test_scan_skills_dir_that_is_both_skill_and_category(tmp_path):
    """A flat skill can also hold nested skills (e.g. `github/` + `github/github-issue-to-pr/`).
    Both must be indexed -- in the primary root and in extra roots alike."""
    for root in (tmp_path / "primary", tmp_path / "extra"):
        _write_flat_skill(root, "github")
        _write_skill(root, "github", "github-issue-to-pr")

    primary = skill_index.scan_skills(str(tmp_path / "primary"))
    assert {s["name"] for s in primary} == {"github", "github-issue-to-pr"}

    empty = tmp_path / "empty"
    empty.mkdir()
    extra = skill_index.scan_skills(str(empty), extra_roots=[str(tmp_path / "extra")])
    assert {s["name"] for s in extra} == {"github", "github-issue-to-pr"}


def test_scan_skills_symlinked_flat_twin_of_nested_skill_is_one_record(tmp_path):
    """A flat entry symlinked to the same SKILL.md as a nested one is one skill, not an
    ambiguous pair -- and the nested record (historical category) is the one kept."""
    store = tmp_path / "store"
    _write_flat_skill(store, "handoff")
    root = tmp_path / "skills"
    (root / "general-skills").mkdir(parents=True)
    os.symlink(store / "handoff", root / "general-skills" / "handoff")
    os.symlink(store / "handoff", root / "handoff")

    found = skill_index.scan_skills(str(root))

    assert len(found) == 1
    assert found[0]["category"] == "general-skills"


def test_scan_skills_distinct_flat_and_nested_same_name_stay_ambiguous(tmp_path):
    """Two different files claiming one name are a real conflict: both are reported so
    read_skill_body() returns None instead of silently picking one as the baseline."""
    root = tmp_path / "skills"
    _write_flat_skill(root, "github", description="flat copy")
    _write_skill(root, "software-development", "github", description="nested copy")

    found = skill_index.scan_skills(str(root))

    assert sorted(s["description"] for s in found if s["name"] == "github") == [
        "flat copy", "nested copy",
    ]
