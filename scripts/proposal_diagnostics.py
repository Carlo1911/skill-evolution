#!/usr/bin/env python3
"""Read-only diagnostic of the proposal queue: which pending proposals can be reviewed.

Usage:
    python3 scripts/proposal_diagnostics.py                 # pending proposals, text report
    python3 scripts/proposal_diagnostics.py --all           # every status
    python3 scripts/proposal_diagnostics.py --format json   # one object per proposal
    python3 scripts/proposal_diagnostics.py --dir PATH      # another proposals directory

Each proposal gets one primary state, by precedence -- unreadable, incomplete, ambiguous,
archived, unresolved, stale, else valid -- plus every reason that applies, so a reviewer
sees that an incomplete proposal also targets an archived skill and that completing it
would not make it applicable. Possible duplicates are flagged on top of the state, by
deterministic text rules only.

Every check is mechanical and reuses what the gate and apply path already use
(_extract_evaluated_content, _splice_candidate, _build_write_plan), reading skills
through the active host adapter -- one scan per run, each installed body read once. Nothing calls a provider, and nothing is
written: the persisted `status` stays the reviewer's decision.
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict
from typing import Any, Dict, List, NamedTuple, Optional

import evaluate
import host
import proposal as proposal_module
import skill_index
from proposal import ProposalStatus, ProposalType

STATE_PRECEDENCE = ["unreadable", "incomplete", "ambiguous", "archived", "unresolved", "stale"]

# Change fields each proposal type can turn into a skill mutation (see the host adapters'
# apply_skill_write()); anything else -- e.g. a repo script path -- cannot be applied.
_SKILL_FIELDS = {
    ProposalType.IMPROVE_EXISTING: {"body", "description"},
    ProposalType.CREATE_NEW: {"name", "description", "category", "body"},
    ProposalType.MERGE_SKILLS: {"body", "description"},
}


class _SkillView(NamedTuple):
    """What the active host has installed and archived, gathered once per run."""
    live_by_name: Dict[str, List[Dict[str, Any]]]
    ambiguous: Dict[str, List[str]]  # name -> distinct real paths (find_duplicate_names)
    archived_names: set
    bodies: Dict[str, Optional[str]]  # memoized installed bodies of unambiguous names

    def body(self, name: str) -> Optional[str]:
        """The installed body of an unambiguous live skill, read once and memoized --
        evaluate.installed_skill_body() would re-scan the whole tree per call."""
        if name not in self.bodies:
            try:
                with open(self.live_by_name[name][0]["path"], encoding="utf-8") as f:
                    self.bodies[name] = f.read()
            except OSError:
                self.bodies[name] = None
        return self.bodies[name]


def _normalize(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _new_skill_name(p) -> Optional[str]:
    """The skill name a create_new proposal would create, if it carries one."""
    return next((c.new_value for c in p.proposed_changes if c.field == "name" and c.new_value), None)


def _incomplete_reasons(p) -> List[str]:
    reasons = []
    _, kind, _ = evaluate._extract_evaluated_content(p)
    if kind == evaluate.MALFORMED_CONTENT_KIND:
        reasons.append("a proposed change has an empty new_value")
    allowed = _SKILL_FIELDS.get(p.type)
    if allowed is not None:
        bad = sorted({c.field for c in p.proposed_changes
                      if c.field not in allowed and not c.field.startswith("source_")})
        if bad:
            reasons.append(f"not a skill field: {', '.join(bad)}")
    if p.type == ProposalType.MERGE_SKILLS and not any(
            c.field.startswith("source_") for c in p.proposed_changes):
        reasons.append("merge_skills proposal has no source_* changes")
    _, plan_error = proposal_module._build_write_plan(p)
    if plan_error:
        reasons.append(plan_error)
    if p.type != ProposalType.CREATE_NEW and not p.target_skill:
        reasons.append("no target_skill")
    return reasons


def _diagnose_one(p, view: _SkillView) -> List[Dict[str, str]]:
    reasons = [{"code": "incomplete", "detail": r} for r in _incomplete_reasons(p)]
    if p.type == ProposalType.CREATE_NEW or not p.target_skill:
        return reasons

    target = p.target_skill
    if target in view.ambiguous:
        paths = ", ".join(view.ambiguous[target])
        reasons.append({"code": "ambiguous", "detail": f"'{target}' is declared by several files: {paths}"})
    elif target not in view.live_by_name:
        if target in view.archived_names:
            reasons.append({"code": "archived", "detail": f"'{target}' exists only in the host's archive"})
        else:
            reasons.append({"code": "unresolved",
                            "detail": f"'{target}' is neither installed nor archived on this host"})
    elif not any(r["code"] == "incomplete" for r in reasons):
        # The same anchor rule the gate applies (_splice_candidate), against the installed
        # body -- what evaluate._resolve_baseline() resolves for an unambiguous live skill.
        content, kind, claimed_old = evaluate._extract_evaluated_content(p)
        baseline = view.body(target)
        if kind == "body" and baseline is not None:
            _, error = evaluate._splice_candidate(content, claimed_old, baseline, kind)
            if error:
                reasons.append({"code": "stale", "detail": error})
    return reasons


def _duplicate_flags(pending, reference, view: _SkillView) -> Dict[str, List[Dict[str, str]]]:
    """Possible-duplicate flags per pending proposal id, from four text rules."""
    flags: Dict[str, List[Dict[str, str]]] = defaultdict(list)

    # Only content fields identify a change: two new skills sharing a `category` are not
    # duplicates. A create_new has no target_skill, so it is keyed by the name it creates.
    same_change = defaultdict(set)
    for p in pending:
        target = p.target_skill or _new_skill_name(p)
        for c in p.proposed_changes:
            if c.new_value and c.field in ("body", "description"):
                same_change[(target, c.field, _normalize(c.new_value))].add(p.proposal_id)
    for ids in same_change.values():
        for pid in ids:
            for other in sorted(ids - {pid}):
                if not any(f["other"] == other for f in flags[pid]):
                    flags[pid].append({"other": other, "detail": "identical change to the same target"})

    anchors = defaultdict(set)
    for p in pending + reference:
        for c in p.proposed_changes:
            if c.field == "body" and _normalize(c.old_value):
                anchors[(p.target_skill, _normalize(c.old_value))].add(p.proposal_id)
    for p in pending:
        others = set()
        for c in p.proposed_changes:
            if c.field == "body" and _normalize(c.old_value):
                others |= anchors[(p.target_skill, _normalize(c.old_value))] - {p.proposal_id}
        for other in sorted(others):
            if not any(f["other"] == other for f in flags[p.proposal_id]):
                flags[p.proposal_id].append({"other": other, "detail": "replaces the same old_value anchor"})

    for p in pending:
        if p.type == ProposalType.CREATE_NEW:
            name = _new_skill_name(p)
            if name and name in view.live_by_name:
                flags[p.proposal_id].append({"other": None, "detail": f"skill '{name}' already exists"})
            continue
        target = p.target_skill
        if target in view.live_by_name and target not in view.ambiguous:
            body = view.body(target) or ""
            for c in p.proposed_changes:
                if c.field == "body" and c.new_value and c.new_value.strip() in body:
                    flags[p.proposal_id].append(
                        {"other": None, "detail": "new_value is already present in the installed skill"})
                    break
    return flags


def diagnose_queue(directory: Optional[str] = None, include_all: bool = False) -> List[Dict[str, Any]]:
    """Classify the proposals in `directory` (default: proposal.get_proposals_dir()).

    Pending (`status: proposed`) proposals are classified unless `include_all`; the rest
    still serve as reference for the duplicate rules. Unreadable files are always
    reported, since their status cannot be known.
    """
    directory = directory or proposal_module.get_proposals_dir()
    adapter = host.get_adapter()
    live = adapter.iter_skills()
    live_by_name: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in live:
        live_by_name[record["name"]].append(record)
    view = _SkillView(live_by_name, skill_index.find_duplicate_names(live),
                      {r["name"] for r in adapter.iter_archived_skills()}, {})

    results: List[Dict[str, Any]] = []
    loaded = []
    for path in sorted(glob.glob(os.path.join(directory, "*.md"))):
        try:
            loaded.append((os.path.basename(path), proposal_module.load_proposal(path)))
        except (ValueError, OSError) as e:
            results.append({"file": os.path.basename(path), "proposal_id": None, "type": None,
                            "target": None, "status": None, "state": "unreadable",
                            "reasons": [{"code": "unreadable", "detail": str(e)}],
                            "possible_duplicates": []})

    def classified(p):
        return include_all or p.status == ProposalStatus.PROPOSED

    pending = [p for _, p in loaded if classified(p)]
    reference = [p for _, p in loaded if not classified(p)]
    flags = _duplicate_flags(pending, reference, view)

    for fname, p in loaded:
        if not classified(p):
            continue
        reasons = _diagnose_one(p, view)
        codes = [r["code"] for r in reasons]
        state = next((s for s in STATE_PRECEDENCE if s in codes), "valid")
        results.append({"file": fname, "proposal_id": p.proposal_id, "type": p.type.value,
                        "target": p.target_skill, "status": p.status.value, "state": state,
                        "reasons": reasons, "possible_duplicates": flags.get(p.proposal_id, [])})
    return results


def _render_text(diags: List[Dict[str, Any]]) -> str:
    states = defaultdict(int)
    reason_counts = defaultdict(int)
    for d in diags:
        states[d["state"]] += 1
        for code in {r["code"] for r in d["reasons"]}:
            reason_counts[code] += 1
    duplicates = sum(1 for d in diags if d["possible_duplicates"])

    lines = [f"{len(diags)} proposals"]
    lines.append("By state:  " + ", ".join(
        f"{s}: {states[s]}" for s in STATE_PRECEDENCE + ["valid"] if states[s]))
    if reason_counts:
        lines.append("By reason: " + ", ".join(
            f"{s}: {reason_counts[s]}" for s in STATE_PRECEDENCE if reason_counts[s]))
    lines.append(f"Possible duplicates: {duplicates}")
    lines.append("")
    order = {s: i for i, s in enumerate(STATE_PRECEDENCE + ["valid"])}
    for d in sorted(diags, key=lambda d: (order[d["state"]], d["file"])):
        ident = d["proposal_id"] or d["file"]
        head = f"[{d['state']}] {ident}"
        if d["type"]:
            head += f"  {d['type']}  {d['target'] or '-'}"
        lines.append(head)
        for r in d["reasons"]:
            lines.append(f"    {r['code']}: {r['detail']}")
        for f in d["possible_duplicates"]:
            other = f" ({f['other']})" if f["other"] else ""
            lines.append(f"    possible duplicate{other}: {f['detail']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Read-only diagnostic of the proposal queue")
    parser.add_argument("--dir", default=None, help="Proposals directory (default: proposal.get_proposals_dir())")
    parser.add_argument("--all", action="store_true", help="Classify every status, not only pending proposals")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    diags = diagnose_queue(args.dir, include_all=args.all)
    if args.format == "json":
        print(json.dumps(diags, indent=2))
    else:
        print(_render_text(diags))


if __name__ == "__main__":
    main()
