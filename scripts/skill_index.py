#!/usr/bin/env python3
"""Scan ~/.hermes/skills/ and output a compact index.

Usage:
    python skill_index.py                        # Full index as JSON
    python skill_index.py --categories-only      # Just category names
    python skill_index.py --name "debugging"     # Find specific skill
"""

import json
import os
import sys
from pathlib import Path


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


def scan_skills(skills_dir: str = SKILLS_DIR) -> list:
    """Scan skills directory and return structured index."""
    base = Path(skills_dir)
    if not base.exists():
        return []

    results = []

    for category_dir in sorted(base.iterdir()):
        if not category_dir.is_dir():
            continue

        # Dot-prefixed dirs are internal, not live skill categories: `.archive/` holds
        # retired skills, `.curator_backups/` timestamped snapshots, `.hub/` lockfiles
        # and quarantine. Indexing them would tell the analyzer a retired skill still
        # exists, and an archived twin of a live skill would make optimize_skill.py's
        # duplicate-name check reject the live one as ambiguous.
        if category_dir.name.startswith("."):
            continue

        category = category_dir.name

        for skill_dir in sorted(category_dir.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            content = skill_md.read_text()
            fields = parse_name_description_frontmatter(content)
            name = fields.get("name", skill_dir.name)
            description = fields.get("description", "")

            results.append({
                "name": name,
                "category": category,
                "description": description,
                "path": str(skill_md),
                "size": len(content),
            })

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scan installed Hermes skills")
    parser.add_argument("--categories-only", action="store_true")
    parser.add_argument("--name", type=str, default=None)
    args = parser.parse_args()

    skills = scan_skills()

    if args.name:
        skills = [s for s in skills if args.name.lower() in s["name"].lower()]

    if args.categories_only:
        categories = sorted(set(s["category"] for s in skills))
        print("\n".join(categories))
    else:
        print(json.dumps(skills, indent=2))


if __name__ == "__main__":
    main()
