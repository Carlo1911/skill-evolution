#!/usr/bin/env python3
"""Proposal schema and I/O for skill evolution proposals.

Usage:
    python proposal.py --example              # Print example proposal
    python proposal.py --list                 # List current proposals
    python proposal.py --show <id>            # Show specific proposal
"""

import base64
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import uuid4

import evaluate
import host


class ProposalType(str, Enum):
    IMPROVE_EXISTING = "improve_existing"
    CREATE_NEW = "create_new"
    MERGE_SKILLS = "merge_skills"
    DEPRECATE_SKILL = "deprecate_skill"


class ProposalStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"


def get_proposals_dir() -> str:
    """Return the proposals directory, defaulting to ./proposals/ at the repo root.

    The shared skill repo writes proposals here by default; deployments override via
    SKILL_EVOLUTION_PROPOSALS_DIR when they want a different location.
    """
    default = os.path.join(os.getcwd(), "proposals")
    return os.environ.get("SKILL_EVOLUTION_PROPOSALS_DIR", default)


@dataclass
class ProposedChange:
    field: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    description: str = ""


@dataclass
class SkillEvolutionProposal:
    proposal_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    type: ProposalType = ProposalType.IMPROVE_EXISTING
    target_skill: Optional[str] = None
    confidence: float = 0.0
    summary: str = ""
    rationale: str = ""
    proposed_changes: List[ProposedChange] = field(default_factory=list)
    session_ids: List[str] = field(default_factory=list)
    status: ProposalStatus = ProposalStatus.PROPOSED
    applied_at: Optional[str] = None
    # The file this proposal was loaded from, so save_proposal() writes it back there and
    # not to <proposal_id>.md -- which can be a *different* file. Never rendered.
    source_path: Optional[str] = field(default=None, repr=False, compare=False)

    def render(self) -> str:
        return f"{self._render_frontmatter()}\n{self._render_body()}"

    def _render_frontmatter(self) -> str:
        lines = ["---"]
        lines.append(f"proposal_id: {self.proposal_id}")
        lines.append(f"created_at: {self.created_at}")
        lines.append(f"type: {self.type.value}")
        lines.append(f"target_skill: {self.target_skill or ''}")
        lines.append(f"confidence: {self.confidence}")
        lines.append(f"summary: {self.summary}")
        lines.append(f"status: {self.status.value}")
        if self.applied_at:
            lines.append(f"applied_at: {self.applied_at}")
        if self.session_ids:
            lines.append("session_ids:")
            for sid in self.session_ids:
                lines.append(f"  - \"{sid}\"")
        if self.proposed_changes:
            lines.append("proposed_changes:")
            for c in self.proposed_changes:
                entry = f"  - field: {c.field}"
                if c.old_value is not None:
                    entry += f"\n    old_value: \"{_encode_frontmatter_value(c.old_value)}\""
                if c.new_value is not None:
                    entry += f"\n    new_value: \"{_encode_frontmatter_value(c.new_value)}\""
                if c.description:
                    entry += f"\n    description: \"{c.description}\""
                lines.append(entry)
        lines.append("---")
        return "\n".join(lines)

    def _render_body(self) -> str:
        lines = [
            f"# Skill Evolution Proposal: {self.summary}",
            "",
            f"**Proposal ID:** `{self.proposal_id}`",
            f"**Type:** {self.type.value}",
            f"**Confidence:** {self.confidence:.2f}",
            f"**Status:** {self.status.value}",
            f"**Created:** {self.created_at}",
            "",
        ]
        if self.target_skill:
            lines.append(f"**Target Skill:** `{self.target_skill}`")
            lines.append("")
        lines.extend(["## Rationale", "", self.rationale, ""])
        if self.session_ids:
            lines.extend(["## Evidence Sessions", ""])
            for sid in self.session_ids:
                lines.append(f"- `{sid}`")
            lines.append("")
        if self.proposed_changes:
            lines.extend(["## Proposed Changes", ""])
            for c in self.proposed_changes:
                lines.append(f"### `{c.field}`")
                if c.description:
                    lines.append(f"_{c.description}_")
                    lines.append("")
                if c.old_value is not None:
                    lines.append(f"- **Current:** {c.old_value}")
                if c.new_value is not None:
                    lines.append(f"- **Proposed:** {c.new_value}")
                lines.append("")
        return "\n".join(lines)


# ── I/O ─────────────────────────────────────────────────────────────

def save_proposal(proposal: SkillEvolutionProposal, directory: Optional[str] = None) -> str:
    """Write `proposal` to its source file when it was loaded from `directory`, else to
    `<directory>/<proposal_id>.md`.

    Always writing <proposal_id>.md was wrong once two files shared an id (live case:
    20260823-001.md and 20260823-001-new.md): applying the `-new` file wrote
    `status: applied` over the other one and left `-new` still `proposed`, i.e.
    applicable a second time.
    """
    proposals_dir = directory or get_proposals_dir()
    os.makedirs(proposals_dir, exist_ok=True)
    path = os.path.join(proposals_dir, f"{proposal.proposal_id}.md")
    source = proposal.source_path
    if source and os.path.realpath(os.path.dirname(source)) == os.path.realpath(proposals_dir):
        path = source
    content = evaluate.redact_secrets(proposal.render())  # redact secrets before persisting to disk
    with open(path, "w") as f:
        f.write(content)
    proposal.source_path = path
    return path


def _frontmatter_problem(content: str) -> str:
    """Name why `content` has no frontmatter block load_proposal() can parse.

    The bare "Invalid proposal file" this replaces sent the reviewer into the file to find
    out; the live case was an analyzer-written proposal missing only its opening `---`.
    """
    if not content.startswith("---\n"):
        return "missing opening '---' frontmatter delimiter on line 1"
    return "frontmatter opened on line 1 but no closing '---' line follows"


def load_proposal(path: str) -> SkillEvolutionProposal:
    with open(path) as f:
        content = f.read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
    if not m:
        raise ValueError(f"Invalid proposal file: {path}: {_frontmatter_problem(content)}")

    data = _parse_frontmatter(m.group(1))
    proposal = SkillEvolutionProposal(
        proposal_id=data.get("proposal_id", str(uuid4())),
        created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        type=ProposalType(data.get("type", "improve_existing")),
        target_skill=data.get("target_skill") or None,
        confidence=float(data.get("confidence", 0.0)),
        summary=data.get("summary", ""),
        status=ProposalStatus(data.get("status", "proposed")),
        applied_at=data.get("applied_at"),
        source_path=path,
    )
    raw_sessions = data.get("session_ids", [])
    if isinstance(raw_sessions, list):
        proposal.session_ids = [str(s) for s in raw_sessions if s]
    raw_changes = data.get("proposed_changes", [])
    if isinstance(raw_changes, list):
        for c in raw_changes:
            if isinstance(c, dict) and c.get("field"):
                proposal.proposed_changes.append(ProposedChange(
                    field=c["field"],
                    old_value=c.get("old_value"),
                    new_value=c.get("new_value"),
                    description=c.get("description", ""),
                ))
    return proposal


def list_proposals(directory: Optional[str] = None) -> List[SkillEvolutionProposal]:
    """Load every proposal in `directory`, reporting -- not silently dropping -- the ones
    that fail to parse.

    A proposal is a unit of work awaiting a human decision, so a file that disappears from
    the inventory is worse than one that errors: nobody goes looking for it. This swallowed
    ValueError/IOError entirely, and it fired for real -- a model wrote
    `status: already_covered`, which is not in ProposalStatus, and load_proposal() raised;
    `--list` and `--retroactive` then reported 7 of 8 files with no indication the eighth
    existed. Any model-authored field can do this, since the analyzer writes these files.

    Skips still happen (one malformed file must not take down the listing) but each is now
    named on stderr with its reason. stderr because stdout carries this module's CLI output.
    """
    proposals_dir = directory or get_proposals_dir()
    if not os.path.isdir(proposals_dir):
        return []
    proposals = []
    for fname in sorted(os.listdir(proposals_dir), reverse=True):
        if not fname.endswith(".md"):
            continue
        try:
            proposals.append(load_proposal(os.path.join(proposals_dir, fname)))
        except (ValueError, IOError) as e:
            print(f"warning: skipping unreadable proposal {fname}: {e}", file=sys.stderr)
    return proposals


def find_proposal_file(proposal_id: str, directory: Optional[str] = None) -> str:
    """Return the one file in `directory` that holds `proposal_id`.

    Candidates are every readable file declaring that id plus `<proposal_id>.md` when it
    cannot be read -- so a `-new` copy is found by its id, and an unreadable namesake
    still counts as a claim rather than leaving the copy to look unique. Raises
    FileNotFoundError when nothing matches and ValueError naming every file when several
    do: picking one would show the reviewer a proposal other than the one they meant.
    """
    proposals_dir = directory or get_proposals_dir()
    canonical = f"{proposal_id}.md"
    candidates = set()
    if os.path.isdir(proposals_dir):
        for fname in os.listdir(proposals_dir):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(proposals_dir, fname)
            try:
                if load_proposal(path).proposal_id == proposal_id:
                    candidates.add(path)
            except (ValueError, IOError):
                if fname == canonical:
                    candidates.add(path)
    if not candidates:
        raise FileNotFoundError(f"no proposal with id '{proposal_id}' in {proposals_dir}")
    if len(candidates) > 1:
        names = ", ".join(sorted(os.path.basename(c) for c in candidates))
        raise ValueError(f"several files claim proposal id '{proposal_id}': {names}")
    return candidates.pop()


def _encode_frontmatter_value(value: str) -> str:
    """Escape a proposed-change value for the hand-rolled frontmatter parser.

    load_proposal() finds the frontmatter block by matching up to the first
    "\\n---\\n", and _parse_frontmatter() reads change fields line-by-line. A
    multi-line value containing its own "---" (e.g. a GEPA-optimized skill
    body, which starts with its own YAML frontmatter) corrupts both: the outer
    match truncates early, and the per-line reader misreads embedded colons
    and quotes. Base64-encode only when the value contains a newline or
    "---" -- the base64 alphabet has no "-", quote, colon, or newline, so the
    encoded form is always safe here. Short single-line values (the common
    case) are returned untouched so existing proposal files keep rendering
    exactly as before.

    Redacts secrets from the value *before* encoding it: save_proposal()'s
    whole-document redaction pass is line-based and would not recognize a
    secret pattern once it's hidden inside a base64 blob, so redaction must
    happen on the plaintext first or it would silently stop applying to this
    field the moment a value needs encoding.
    """
    value = evaluate.redact_secrets(value)
    if "\n" in value or "---" in value:
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        return f"b64:{encoded}"
    return value


def _decode_frontmatter_value(value: str) -> str:
    """Reverse _encode_frontmatter_value(); a value with no "b64:" prefix is returned as-is."""
    if value.startswith("b64:"):
        return base64.b64decode(value[4:]).decode("utf-8")
    return value


def _parse_frontmatter(text: str) -> dict:
    """Simple YAML-like frontmatter parser."""

    def flush_list():
        """Persist an open block list (e.g. session_ids) into data, if any."""
        nonlocal in_list, list_key, list_items
        if in_list and list_key:
            data[list_key] = list_items
        in_list = False
        list_key = None
        list_items = []

    data = {}
    list_key = None
    list_items = []
    in_list = False
    change_block = None
    in_change = False

    for line in text.split("\n"):
        line_stripped = line.strip()

        # A new change-block entry ("  - field: ...") closes any open block list
        # before starting its own block. It must be handled before list
        # continuation, otherwise a `proposed_changes:` key that opened a list
        # would swallow the entry as a bare list item.
        if line.startswith("  - field:"):
            flush_list()
            if in_change and change_block:
                data.setdefault("proposed_changes", []).append(change_block)
            in_change = True
            change_block = {
                "field": line_stripped.split(": ", 1)[1].strip().strip('"')
                if ": " in line_stripped else ""
            }
            continue

        # Handle change block continuation (4-space indented keys)
        if in_change and line.startswith("    "):
            kv = line_stripped.split(":", 1)
            if len(kv) == 2:
                change_key = kv[0].strip()
                change_value = kv[1].strip().strip('"')
                if change_key in ("old_value", "new_value"):
                    change_value = _decode_frontmatter_value(change_value)
                change_block[change_key] = change_value
            continue
        elif in_change:
            if change_block:
                data.setdefault("proposed_changes", []).append(change_block)
            in_change = False
            change_block = None

        # Handle list continuation
        if in_list and line.startswith("  -"):
            # strip the leading "- " (the line was already .strip()'d, so the
            # indent is gone); this keeps unquoted items intact instead of
            # slicing off their first character like the old [3:] did.
            list_items.append(line_stripped[2:].strip().strip('"'))
            continue
        elif in_list:
            flush_list()

        # Handle bare `key:` lines (block lists like session_ids:) BEFORE the
        # ": " check -- a line ending in ":" has no ": " and would otherwise be
        # skipped entirely, leaving list items orphaned and the key absent.
        if line_stripped.endswith(":") and not line_stripped.startswith(("  -", "    ")):
            flush_list()
            in_list = True
            list_key = line_stripped[:-1].strip()
            list_items = []
            continue

        if ": " not in line_stripped:
            continue

        key, value = line_stripped.split(": ", 1)
        key = key.strip()
        value = value.strip().strip('"')

        if value == "":
            in_list = True
            list_key = key
            list_items = []
            continue

        data[key] = value

    # Close open structures
    if in_list and list_key:
        data[list_key] = list_items
    if in_change and change_block:
        data.setdefault("proposed_changes", []).append(change_block)

    return data


def _resolve_create_meta(proposal: SkillEvolutionProposal):
    """Resolve a create_new proposal's name/description/category/body, or an error string.

    The analyzer's create_new proposals carry a `body` change whose content is the full
    SKILL.md -- but a real proposal has shipped a literal placeholder ("See proposal body
    for full draft content") instead, and Hermes's skill_manage 'create' requires
    full content, so even the Hermes path could not apply one. Fail closed here, before
    the evaluation gate spends a provider call: a create proposal without a real body
    is a drafting failure, not a valid mutation.

    Returns (meta_dict, None) on success or (None, error_reason) on refusal.
    """
    changes = proposal.proposed_changes
    name = next((c.new_value for c in changes if c.field == "name" and c.new_value), None)
    description = next((c.new_value for c in changes if c.field == "description" and c.new_value), "")
    category = next((c.new_value for c in changes if c.field == "category" and c.new_value), "")
    body = next((c.new_value for c in changes if c.field == "body" and c.new_value), None)

    if not name:
        return None, "create_new proposal has no 'name' change with a value"
    if not body:
        return None, "create_new proposal has no 'body' change with a value"
    if not body.lstrip().startswith("---"):
        return None, "create_new body must be a full SKILL.md starting with frontmatter ('---')"
    # Placeholder markers: the head of the body is where a stub says "see the proposal",
    # "draft below", "todo", etc. rather than shipping a real skill. Refuse loudly --
    # a proposal that would create an empty/placeholder skill must never be marked
    # applied. The scan is intentionally scoped to the first 500 chars so a legitimate
    # later "## Todo" section can't trip it.
    head = body[:500]
    if re.search(
        r"(?i)see\s+proposal|full\s+draft\s+content|draft\s+below|placeholder|\bto\s+be\s+written\b"
        r"|\bnot\s+yet\s+written\b|coming\s+soon|\btodo\b|\btbd\b",
        head,
    ):
        return None, "create_new body looks like a placeholder draft rather than a real SKILL.md"

    return (
        {"name": name, "description": description, "category": category, "body": body},
        None,
    )


def _build_write_plan(proposal: SkillEvolutionProposal):
    """Build the normalized mutation plan apply_proposal() hands to the host adapter.

    Returns (plan, None) on success or (None, error_reason) when the proposal cannot be
    expressed as a mutation at all (currently only a create_new with a missing/
    placeholder body or name) -- callers refuse before the evaluation gate, so a
    structurally invalid proposal never costs a provider call.
    """
    plan = {
        "type": proposal.type.value,
        "target_skill": proposal.target_skill,
        "proposal_id": proposal.proposal_id,
        "changes": [
            {
                "field": c.field,
                "old_value": c.old_value,
                "new_value": c.new_value,
                "description": c.description,
            }
            for c in proposal.proposed_changes
        ],
    }
    if proposal.type == ProposalType.CREATE_NEW:
        meta, error = _resolve_create_meta(proposal)
        if error:
            return None, error
        plan["body_name"] = meta["name"]
        plan["body"] = meta["body"]
        plan["description"] = meta["description"]
        plan["category"] = meta["category"]
    return plan, None


def apply_proposal(proposal: SkillEvolutionProposal, min_confidence: float = 0.85,
                   directory: Optional[str] = None) -> dict:
    """Validate a proposal, run the evaluation gate, and delegate the mutation to the
    active host's adapter.

    Args:
        proposal: The proposal to validate
        min_confidence: Minimum confidence threshold for auto-apply
        directory: Override proposals directory

    Returns:
        dict with:
            can_apply: bool
            action: str (proposal type)
            target_skill: str
            instructions: list of dicts for the agent (Hermes) or writes (Claude Code)
            evaluation_results: list of evaluator result dicts (present once the gate has run)
    """
    if proposal.status != ProposalStatus.PROPOSED:
        raise ValueError(f"Cannot apply: proposal status is {proposal.status.value}, not 'proposed'")

    if proposal.confidence < min_confidence:
        raise ValueError(f"Confidence {proposal.confidence:.2f} below threshold {min_confidence:.2f}")

    # Resolve the active host adapter once. Everything below -- the write-side guard AND
    # the create-body validation -- happens before the evaluation gate: there is no point
    # spending a provider call scoring a proposal that can never be applied, and checking
    # here means a non-applicable host never appends an entry to eval_history.jsonl
    # either.
    try:
        adapter = host.get_adapter()
    except ValueError as e:
        return {
            "can_apply": False,
            "action": proposal.type.value,
            "target_skill": proposal.target_skill or "",
            "instructions": [],
            "evaluation_results": [],
            "reason": str(e),
        }

    # Write-side guard: a host that can't mutate skills refuses before the gate. Same
    # provider-call economy as the unknown-host case above.
    if not adapter.supports_write:
        return {
            "can_apply": False,
            "action": proposal.type.value,
            "target_skill": proposal.target_skill or "",
            "instructions": [],
            "evaluation_results": [],
            "reason": f"host '{adapter.name}' does not support skill writes; no proposal can be applied on it",
        }

    plan, plan_error = _build_write_plan(proposal)
    if plan_error:
        return {
            "can_apply": False,
            "action": proposal.type.value,
            "target_skill": proposal.target_skill or "",
            "instructions": [],
            "evaluation_results": [],
            "reason": plan_error,
        }

    # Evaluation gate: run after status/confidence, before mutating the proposal.
    # Fail closed (R21) on any error here too -- not just inside individual
    # evaluators -- so a misconfigured SKILL_EVOLUTION_EVALUATORS/GATE_STRICTNESS
    # value blocks auto-apply instead of crashing apply_proposal() uncaught.
    try:
        eval_results, _, gate_passed = evaluate.evaluate_and_record(proposal)
        evaluation_results = [asdict(r) for r in eval_results]
    except Exception as e:
        return {
            "can_apply": False,
            "action": proposal.type.value,
            "target_skill": proposal.target_skill or "",
            "instructions": [],
            "evaluation_results": [],
            "evaluation_error": f"evaluation gate raised and was treated as failed (fail-closed): {e}",
        }

    if not gate_passed:
        return {
            "can_apply": False,
            "action": proposal.type.value,
            "target_skill": proposal.target_skill or "",
            "instructions": [],
            "evaluation_results": evaluation_results,
        }

    # Delegate the mutation to the resolved host's adapter -- the single write seam.
    # Hermes returns instruction dicts for the agent to run later; Claude Code writes
    # skill files now. Only a can_apply: True here leads to status: applied.
    write_result = adapter.apply_skill_write(plan)
    if not write_result.get("can_apply"):
        return {
            "can_apply": False,
            "action": proposal.type.value,
            "target_skill": proposal.target_skill or "",
            "instructions": [],
            "evaluation_results": evaluation_results,
            "reason": write_result.get("reason", "host adapter refused the write"),
        }

    # For create_new proposals, migrate history from proposal:<id> to skill:<name>
    # so RegressionEvaluator can detect regressions across the skill's full lineage.
    # Synchronous, idempotent, archive-first (same safety posture as prune_history).
    # Runs only after a successful apply: a refused write must never leave an orphaned
    # migration behind.
    if proposal.type == ProposalType.CREATE_NEW and proposal.proposal_id:
        skill_name = plan.get("body_name")
        if skill_name:
            new_target = f"skill:{skill_name}"
            migrated = evaluate.migrate_proposal_history(proposal.proposal_id, new_target)
            if migrated:
                print(f"Migrated {migrated} history entries from "
                      f"proposal:{proposal.proposal_id} to {new_target}")

    result = {
        "can_apply": True,
        "action": proposal.type.value,
        "target_skill": proposal.target_skill or "",
        "instructions": write_result.get("instructions", []),
        "evaluation_results": evaluation_results,
    }
    if write_result.get("applied_by"):
        result["applied_by"] = write_result["applied_by"]
    if write_result.get("writes"):
        result["writes"] = write_result["writes"]

    # Mark as applied
    proposal.status = ProposalStatus.APPLIED
    proposal.applied_at = datetime.now(timezone.utc).isoformat()
    save_proposal(proposal, directory)

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Skill evolution proposal tools")
    parser.add_argument("--example", action="store_true", help="Print example proposal")
    parser.add_argument("--list", action="store_true", help="List current proposals")
    parser.add_argument("--show", type=str, default=None, help="Show specific proposal by ID")
    args = parser.parse_args()
    
    if args.example:
        proposal = SkillEvolutionProposal(
            proposal_id="example-001",
            type=ProposalType.IMPROVE_EXISTING,
            target_skill="debugging-and-error-recovery",
            confidence=0.85,
            summary="Improve debugging skill with FastAPI patterns",
            rationale="Analysis of 5 recent sessions shows FastAPI 500 errors are common.",
            proposed_changes=[ProposedChange(
                field="description",
                old_value="Guides systematic root-cause debugging.",
                new_value="Guides systematic root-cause debugging with FastAPI-specific patterns.",
            )],
            session_ids=["20260718_183435_d91723"],
        )
        print(proposal.render())
    
    elif args.list:
        proposals = list_proposals()
        for p in proposals:
            print(f"{p.proposal_id} | {p.type.value:20s} | conf={p.confidence:.2f} | {p.summary[:50]}")
    
    elif args.show:
        proposals_dir = get_proposals_dir()
        try:
            path = find_proposal_file(args.show, proposals_dir)
        except FileNotFoundError:
            print(f"Proposal '{args.show}' not found in {proposals_dir}", file=sys.stderr)
            sys.exit(1)
        except ValueError as e:
            print(f"Proposal '{args.show}' is ambiguous: {e}", file=sys.stderr)
            sys.exit(1)
        try:
            print(load_proposal(path).render())
        except (ValueError, IOError) as e:
            print(f"Proposal '{args.show}' is unreadable: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
