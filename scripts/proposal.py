#!/usr/bin/env python3
"""Proposal schema and I/O for skill evolution proposals.

Usage:
    python proposal.py --example              # Print example proposal
    python proposal.py --list                 # List current proposals
    python proposal.py --show <id>            # Show specific proposal
"""

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
    """Return the proposals directory, defaulting to docs/skill-evolution/proposals/."""
    default = os.path.join(os.getcwd(), "docs", "skill-evolution", "proposals")
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
                    entry += f"\n    old_value: \"{c.old_value}\""
                if c.new_value is not None:
                    entry += f"\n    new_value: \"{c.new_value}\""
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
    proposals_dir = directory or get_proposals_dir()
    os.makedirs(proposals_dir, exist_ok=True)
    path = os.path.join(proposals_dir, f"{proposal.proposal_id}.md")
    with open(path, "w") as f:
        f.write(proposal.render())
    return path


def load_proposal(path: str) -> SkillEvolutionProposal:
    with open(path) as f:
        content = f.read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
    if not m:
        raise ValueError(f"Invalid proposal file: {path}")
    
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
    proposals_dir = directory or get_proposals_dir()
    if not os.path.isdir(proposals_dir):
        return []
    proposals = []
    for fname in sorted(os.listdir(proposals_dir), reverse=True):
        if not fname.endswith(".md"):
            continue
        try:
            proposals.append(load_proposal(os.path.join(proposals_dir, fname)))
        except (ValueError, IOError):
            continue
    return proposals


def _parse_frontmatter(text: str) -> dict:
    """Simple YAML-like frontmatter parser."""
    data = {}
    list_key = None
    list_items = []
    in_list = False
    change_block = None
    in_change = False
    
    for line in text.split("\n"):
        line_stripped = line.strip()
        
        # Handle list continuation
        if in_list and line.startswith("  -"):
            list_items.append(line_stripped[3:].strip().strip('"'))
            continue
        elif in_list and not line.startswith("  -"):
            if list_key:
                data[list_key] = list_items
            in_list = False
            list_key = None
            list_items = []
        
        # Handle change block
        if in_change and line.startswith("    "):
            kv = line_stripped.split(":", 1)
            if len(kv) == 2:
                change_block[kv[0].strip()] = kv[1].strip().strip('"')
            continue
        elif in_change and not line.startswith("    "):
            if change_block:
                data.setdefault("proposed_changes", []).append(change_block)
            in_change = False
            change_block = None
        
        if ": " not in line_stripped:
            continue
        
        key, value = line_stripped.split(": ", 1)
        key = key.strip()
        value = value.strip().strip('"')
        
        if line.startswith("  - field:"):
            in_change = True
            change_block = {"field": value}
            continue
        
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


def apply_proposal(proposal: SkillEvolutionProposal, min_confidence: float = 0.85,
                   directory: Optional[str] = None) -> dict:
    """Validate a proposal, run the evaluation gate, and return the action descriptor for skill_manage.

    Args:
        proposal: The proposal to validate
        min_confidence: Minimum confidence threshold for auto-apply
        directory: Override proposals directory

    Returns:
        dict with:
            can_apply: bool
            action: str (skill_manage action type)
            target_skill: str
            instructions: list of dicts for the agent
            evaluation_results: list of evaluator result dicts (present once the gate has run)
    """
    if proposal.status != ProposalStatus.PROPOSED:
        raise ValueError(f"Cannot apply: proposal status is {proposal.status.value}, not 'proposed'")

    if proposal.confidence < min_confidence:
        raise ValueError(f"Confidence {proposal.confidence:.2f} below threshold {min_confidence:.2f}")

    # Evaluation gate (R4, R21): run after status/confidence, before mutating the proposal.
    target = evaluate.target_key_for_proposal(proposal)
    eval_results = evaluate.evaluate_skill_text(proposal)

    strictness = evaluate.resolve_gate_strictness(proposal.type.value)
    gate_passed = evaluate.combine_gate(eval_results, strictness)

    combined_score = (
        sum(r.score for r in eval_results) / len(eval_results) if eval_results else 1.0
    )
    combined_feedback = (
        "; ".join(f"{r.evaluator_name}={'pass' if r.passed else 'fail'}" for r in eval_results)
        or "no evaluators configured"
    )
    combined_result = evaluate.EvalResult(
        score=combined_score, passed=gate_passed, evaluator_name="gate", feedback=combined_feedback,
    )
    evaluate.append_history(target, combined_result, session_ids=proposal.session_ids)

    evaluation_results = [asdict(r) for r in eval_results]

    if not gate_passed:
        return {
            "can_apply": False,
            "action": proposal.type.value,
            "target_skill": proposal.target_skill or "",
            "instructions": [],
            "evaluation_results": evaluation_results,
        }

    result = {
        "can_apply": True,
        "action": proposal.type.value,
        "target_skill": proposal.target_skill or "",
        "instructions": [],
        "evaluation_results": evaluation_results,
    }

    if proposal.type == ProposalType.IMPROVE_EXISTING:
        for change in proposal.proposed_changes:
            instruction = {
                "action": "patch",
                "target_skill": proposal.target_skill,
                "field": change.field,
                "description": change.description,
            }
            if change.old_value:
                instruction["old_value"] = change.old_value
            if change.new_value:
                instruction["new_value"] = change.new_value
            result["instructions"].append(instruction)
    
    elif proposal.type == ProposalType.CREATE_NEW:
        result["instructions"].append({
            "action": "create",
            "target_skill": proposal.target_skill or "",
            "description": next((c.new_value for c in proposal.proposed_changes if c.field == "description"), ""),
            "category": next((c.new_value for c in proposal.proposed_changes if c.field == "category"), ""),
        })
    
    elif proposal.type == ProposalType.DEPRECATE_SKILL:
        result["instructions"].append({
            "action": "delete",
            "name": proposal.target_skill,
        })
    
    elif proposal.type == ProposalType.MERGE_SKILLS:
        for change in proposal.proposed_changes:
            if change.field.startswith("source_"):
                result["instructions"].append({
                    "action": "delete",
                    "name": change.new_value or change.field.replace("source_", ""),
                    "absorbed_into": proposal.target_skill,
                })
    
    else:
        result["can_apply"] = False
        return result
    
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
        path = os.path.join(proposals_dir, f"{args.show}.md")
        if os.path.exists(path):
            print(load_proposal(path).render())
        else:
            print(f"Proposal '{args.show}' not found in {proposals_dir}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
