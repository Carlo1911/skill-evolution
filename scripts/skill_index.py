#!/usr/bin/env python3
"""Scan ~/.hermes/skills/ (plus optional extra roots) and output a compact index.

Usage:
    python skill_index.py                                # Full index as JSON
    python skill_index.py --extra-root ~/.agents/skills  # include an external root
    python skill_index.py --extra-root A --extra-root B  # include several roots
    python skill_index.py --categories-only              # Just category names
    python skill_index.py --name "debugging"             # Find specific skill
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional


SKILLS_DIR = os.path.expanduser("~/.hermes/skills")


def parse_name_description_frontmatter(content: str) -> dict:
    """Extract name/description fields from a SKILL.md-style YAML frontmatter block.

    Returns an empty (or partial) dict when frontmatter is missing or has no
    closing delimiter -- callers decide whether that's an error.
    """
    fields = {}
    if not content.startswith("---"):
        return fields
    parts = content.split("---", 2)
    if len(parts) < 3:
        return fields
    for line in parts[1].split("\n"):
        line = line.strip()
        if line.startswith("name:"):
            fields["name"] = line.split(":", 1)[1].strip().strip("'\"")
        elif line.startswith("description:"):
            fields["description"] = line.split(":", 1)[1].strip().strip("'\"")
    return fields


def build_skill_record(skill_md: Path, category: str) -> Optional[dict]:
    """Build one skill's index record from its SKILL.md path, or None if it can't be read.

    Shared by scan_skills()'s category-nested walk (Hermes) and
    host.ClaudeCodeAdapter.iter_skills()'s flat walk -- the record shape and the
    fields-then-fallback-to-dirname logic are identical either way; only how `category`
    is derived differs (a real directory level for Hermes, a constant for Claude Code).
    """
    try:
        content = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    fields = parse_name_description_frontmatter(content)
    name = fields.get("name", skill_md.parent.name)
    description = fields.get("description", "")
    return {
        "name": name,
        "category": category,
        "description": description,
        "path": str(skill_md),
        "size": len(content),
    }


def scan_skills(skills_dir: str = SKILLS_DIR, extra_roots: Optional[list] = None) -> list:
    """Scan skills directory and return structured index.

    Every root (primary and extra) may hold either layout, decided per entry:
    `<root>/<category>/<skill>/SKILL.md` (nested) or `<root>/<skill>/SKILL.md` (flat --
    the skill's category is then the root's basename). An entry can be both at once
    (`github/SKILL.md` alongside `github/github-issue-to-pr/SKILL.md`), so both are read.

    `extra_roots` optionally adds more roots to scan. Duplicate skill names resolve
    to the FIRST root that reports them (the primary root wins), so pointing this at a
    root that overlaps the Hermes tree (e.g. a `skills.external_dirs` entry that is
    already symlinked under `~/.hermes/skills`) only ADDS the missing skills instead of
    shadowing or doubling the known ones.
    """
    base = Path(skills_dir)
    if not base.exists():
        return []

    # Dot-prefixed dirs are internal, not live skill categories: `.archive/` holds
    # retired skills, `.curator_backups/` timestamped snapshots, `.hub/` lockfiles
    # and quarantine. Indexing them would tell the analyzer a retired skill still
    # exists, and an archived twin of a live skill would make optimize_skill.py's
    # duplicate-name check reject the live one as ambiguous.
    def _scan_root(root: Path) -> list:
        walked = []  # (is_flat, record) in directory order
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if (entry / "SKILL.md").exists():
                record = build_skill_record(entry / "SKILL.md", root.name)
                if record is not None:
                    walked.append((True, record))
            for skill_dir in sorted(entry.iterdir()):
                if skill_dir.name.startswith("."):
                    continue
                record = build_skill_record(skill_dir / "SKILL.md", entry.name)
                if record is not None:
                    walked.append((False, record))

        # A flat entry is often a symlink to the same SKILL.md a category already holds.
        # That is one skill, not an ambiguous pair, so the nested record (the historical
        # one, with its real category) is kept. Distinct files sharing a name are a real
        # conflict and both stay, so read_skill_body() reports ambiguity instead of
        # silently picking one.
        nested_files = {os.path.realpath(r["path"]) for is_flat, r in walked if not is_flat}
        return [
            r for is_flat, r in walked
            if not (is_flat and os.path.realpath(r["path"]) in nested_files)
        ]

    results = _scan_root(base)
    seen_names = {r["name"] for r in results}

    for extra in extra_roots or []:
        extra_path = Path(os.path.expanduser(extra))
        if not extra_path.exists():
            continue
        for record in _scan_root(extra_path):
            if record["name"] not in seen_names:
                seen_names.add(record["name"])
                results.append(record)

    return results


def find_duplicate_names(skills: list) -> dict:
    """Map each skill name declared by more than one distinct file to its sorted real paths.

    Records resolving to the same file (a symlinked twin) count once -- that is one
    skill, not a conflict. Pure on purpose: scan_skills() runs on every skill-body
    lookup, so only the CLI reports this, not the scan itself.
    """
    paths_by_name = {}
    for skill in skills:
        paths_by_name.setdefault(skill["name"], set()).add(os.path.realpath(skill["path"]))
    return {name: sorted(paths) for name, paths in paths_by_name.items() if len(paths) > 1}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scan installed skills for the active host")
    parser.add_argument("--categories-only", action="store_true")
    parser.add_argument("--name", type=str, default=None)
    parser.add_argument("--extra-root", action="append", default=None, metavar="PATH",
                        help="additional skill root to scan (repeatable; e.g. a "
                             "skills.external_dirs entry like ~/.agents/skills)")
    args = parser.parse_args()

    # host is imported locally: host.py imports this module (HermesAdapter delegates to
    # scan_skills()), so a module-scope `import host` here would cycle.
    import host as host_module

    skills = host_module.get_adapter().iter_skills(extra_roots=args.extra_root)

    # stderr only: stdout is the JSON index other tooling parses, and it must not change.
    for name, paths in find_duplicate_names(skills).items():
        print(f"warning: skill name {name!r} is declared by {len(paths)} files: "
              f"{', '.join(paths)}", file=sys.stderr)

    if args.name:
        skills = [s for s in skills if args.name.lower() in s["name"].lower()]

    if args.categories_only:
        categories = sorted(set(s["category"] for s in skills))
        print("\n".join(categories))
    else:
        print(json.dumps(skills, indent=2))


if __name__ == "__main__":
    main()
