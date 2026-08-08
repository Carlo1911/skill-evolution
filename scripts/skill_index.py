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

    `extra_roots` optionally adds more roots to scan, each interpreted with the same
    category-nested layout as the primary root: `<root>/<category>/<skill>/SKILL.md`,
    or — for a flat external root like `~/.agents/skills/` — `<root>/<skill>/SKILL.md`
    (the skill's category is then the root's basename). Duplicate skill names resolve
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
    def _scan_nested(root: Path) -> list:
        found = []
        for category_dir in sorted(root.iterdir()):
            if not category_dir.is_dir():
                continue
            if category_dir.name.startswith("."):
                continue
            for skill_dir in sorted(category_dir.iterdir()):
                if skill_dir.name.startswith("."):
                    continue
                record = build_skill_record(skill_dir / "SKILL.md", category_dir.name)
                if record is not None:
                    found.append(record)
        return found

    results = []
    seen_names = set()

    # First root: exactly the historical behavior, byte for byte, so existing callers
    # (and downstream de-duping by name) behave identically when extra_roots is absent.
    results.extend(_scan_nested(base))

    seen_names.update(r["name"] for r in results)

    for extra in extra_roots or []:
        extra_path = Path(os.path.expanduser(extra))
        if not extra_path.exists():
            continue

        # A root may hold either layout (or both). Decide per-entry instead of per-root:
        # `<root>/<skill>/SKILL.md` (flat) when a child directly carries SKILL.md,
        # `<root>/<category>/<skill>/SKILL.md` (nested) when the child is a category.
        found = []
        for entry in sorted(extra_path.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if (entry / "SKILL.md").exists():
                # flat skill
                record = build_skill_record(entry / "SKILL.md", extra_path.name)
                if record is not None:
                    found.append(record)
            else:
                # nested category
                for skill_dir in sorted(entry.iterdir()):
                    if skill_dir.name.startswith("."):
                        continue
                    record = build_skill_record(skill_dir / "SKILL.md", entry.name)
                    if record is not None:
                        found.append(record)
        for record in found:
            if record["name"] not in seen_names:
                seen_names.add(record["name"])
                results.append(record)

    return results


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

    if args.name:
        skills = [s for s in skills if args.name.lower() in s["name"].lower()]

    if args.categories_only:
        categories = sorted(set(s["category"] for s in skills))
        print("\n".join(categories))
    else:
        print(json.dumps(skills, indent=2))


if __name__ == "__main__":
    main()
