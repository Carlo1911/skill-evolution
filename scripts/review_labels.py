#!/usr/bin/env python3
"""Human labels for proposals: the reference a decision-model pilot is measured against.

Usage:
    python3 scripts/review_labels.py --next          # label unlabeled pending proposals (TTY)
    python3 scripts/review_labels.py --next 5        # at most 5 this session
    python3 scripts/review_labels.py --stats         # counts by label, split and state
    python3 scripts/review_labels.py --export        # current labels as JSONL on stdout

The gate's own pass/fail decisions cannot serve as that reference -- a pilot judged
against them would only measure agreement with the gate -- so a reviewer records, per
proposal, one of LABELS and why. Labels are appended to a JSONL file (latest per
proposal wins), never to the proposal: its `status` stays the reviewer's apply decision.

Each label records the proposal file's digest; a proposal edited after labeling shows as
outdated in --stats and is offered again by --next. Each proposal also has a fixed
`split`, derived from its id, so examples used to design a pilot's instructions are never
the ones used to check them, whatever order labeling happens in.
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import proposal as proposal_module
import proposal_diagnostics

LABELS_PATH_ENV_VAR = "SKILL_EVOLUTION_LABELS_PATH"
LABELS = ("useful", "harmful", "duplicate", "incomplete", "obsolete", "unsure")
SHORTCUTS = {"u": "useful", "h": "harmful", "d": "duplicate", "i": "incomplete",
             "o": "obsolete", "n": "unsure"}
# A label whose meaning is evident from the diagnostic needs no written reason.
REASON_OPTIONAL = {"incomplete"}
HOLDOUT_SHARE = 0.3
MAX_SHOWN_LINES = 40


def get_labels_path() -> str:
    """Labels file: SKILL_EVOLUTION_LABELS_PATH, else ./labels/review_labels.jsonl --
    cwd-relative like the history and proposals defaults. Local-only (gitignored): labels
    and reasons describe proposals derived from real sessions."""
    default = os.path.join(os.getcwd(), "labels", "review_labels.jsonl")
    return os.environ.get(LABELS_PATH_ENV_VAR, "").strip() or default


def split_for(proposal_id: str) -> str:
    """`holdout` for ~HOLDOUT_SHARE of ids, else `design`; fixed per id."""
    bucket = int(hashlib.sha256(proposal_id.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "holdout" if bucket < HOLDOUT_SHARE else "design"


def file_digest(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def read_labels(path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Latest label per proposal id. Unparseable lines are skipped with a warning."""
    path = path or get_labels_path()
    labels: Dict[str, Dict[str, Any]] = {}
    if not os.path.exists(path):
        return labels
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                labels[row["proposal_id"]] = row
            except (ValueError, KeyError, TypeError):
                print(f"warning: {path}:{n} is not a label row; skipped", file=sys.stderr)
    return labels


def make_row(diag: Dict[str, Any], proposal_path: str, label: str, reason: str) -> Dict[str, Any]:
    """Build one label row; ValueError for an unknown label or a missing reason."""
    if label not in LABELS:
        raise ValueError(f"unknown label '{label}' (one of: {', '.join(LABELS)})")
    reason = (reason or "").strip()
    if not reason and label not in REASON_OPTIONAL:
        raise ValueError(f"label '{label}' needs a reason")
    return {
        "proposal_id": diag["proposal_id"],
        "file": diag["file"],
        "content_sha256": file_digest(proposal_path),
        "label": label,
        "reason": reason,
        "labeled_at": datetime.now(timezone.utc).isoformat(),
        "diagnostic_state": diag["state"],
        "split": split_for(diag["proposal_id"]),
    }


def append_label(row: Dict[str, Any], path: Optional[str] = None) -> None:
    path = path or get_labels_path()
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _queue(directory: str, labels: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pending, readable proposals not yet labeled (or edited since), valid ones first."""
    order = {s: i for i, s in enumerate(["valid"] + proposal_diagnostics.STATE_PRECEDENCE)}
    todo = []
    for d in proposal_diagnostics.diagnose_queue(directory):
        if not d["proposal_id"]:
            continue
        existing = labels.get(d["proposal_id"])
        if existing and existing.get("content_sha256") == file_digest(os.path.join(directory, d["file"])):
            continue
        todo.append(d)
    return sorted(todo, key=lambda d: (order.get(d["state"], len(order)), d["file"]))


def stats(directory: Optional[str] = None, path: Optional[str] = None) -> Dict[str, Any]:
    directory = directory or proposal_module.get_proposals_dir()
    labels = read_labels(path)
    pending = {d["proposal_id"]: d for d in proposal_diagnostics.diagnose_queue(directory)
               if d["proposal_id"]}
    outdated = 0
    for pid, row in labels.items():
        file_path = os.path.join(directory, row.get("file", ""))
        if os.path.exists(file_path) and file_digest(file_path) != row.get("content_sha256"):
            outdated += 1
    by_split: Dict[str, Counter] = {"design": Counter(), "holdout": Counter()}
    for row in labels.values():
        by_split.setdefault(row.get("split", "?"), Counter())[row["label"]] += 1
    return {
        "labeled": len(labels),
        "pending_unlabeled": sum(1 for pid in pending if pid not in labels),
        "outdated": outdated,
        "by_label": dict(Counter(r["label"] for r in labels.values())),
        "by_state": dict(Counter(r.get("diagnostic_state") for r in labels.values())),
        "by_split": {k: dict(v) for k, v in by_split.items()},
    }


def _clip(text: str) -> str:
    lines = (text or "").splitlines()
    if len(lines) <= MAX_SHOWN_LINES:
        return "\n".join(lines)
    return "\n".join(lines[:MAX_SHOWN_LINES]) + f"\n[... {len(lines) - MAX_SHOWN_LINES} more lines in the file]"


def render(diag: Dict[str, Any], p) -> str:
    out = [f"== {diag['proposal_id']}  [{diag['state']}]  {diag['type']}  {diag['target'] or '-'}",
           f"file: {diag['file']}   confidence: {p.confidence}   split: {split_for(diag['proposal_id'])}"]
    if p.summary:
        out.append(f"summary: {p.summary}")
    for r in diag["reasons"]:
        out.append(f"  {r['code']}: {r['detail']}")
    for f in diag["possible_duplicates"]:
        out.append(f"  possible duplicate ({f['other'] or '-'}): {f['detail']}")
    for i, c in enumerate(p.proposed_changes, 1):
        out.append(f"-- change {i}: {c.field}" + (f" ({c.description})" if c.description else ""))
        if c.old_value:
            out.append("   old:\n" + _clip(c.old_value))
        out.append("   new:\n" + (_clip(c.new_value) if c.new_value else "(empty)"))
    if p.session_ids:
        out.append("sessions: " + ", ".join(p.session_ids))
    return "\n".join(out)


def _is_tty() -> bool:
    return (getattr(sys.stdin, "isatty", lambda: False)()
            and getattr(sys.stdout, "isatty", lambda: False)())


def label_interactively(limit: Optional[int] = None, directory: Optional[str] = None,
                        path: Optional[str] = None,
                        ask: Callable[[str], str] = input) -> int:
    """Prompt for a label per queued proposal; returns how many were recorded."""
    directory = directory or proposal_module.get_proposals_dir()
    queue = _queue(directory, read_labels(path))
    if limit is not None:
        queue = queue[:limit]
    prompt = ("label [u]seful [h]armful [d]uplicate [i]ncomplete [o]bsolete u[n]sure, "
              "[s]kip, [q]uit: ")
    recorded = 0
    for i, diag in enumerate(queue, 1):
        file_path = os.path.join(directory, diag["file"])
        print(f"\n({i}/{len(queue)})\n" + render(diag, proposal_module.load_proposal(file_path)))
        while True:
            answer = ask(prompt).strip().lower()
            if answer in ("q", "quit"):
                return recorded
            if answer in ("s", "skip"):
                break
            label = SHORTCUTS.get(answer, answer)
            if label not in LABELS:
                continue
            reason = ""
            while True:
                reason = ask("reason: " if label not in REASON_OPTIONAL else "reason (optional): ")
                if reason.strip() or label in REASON_OPTIONAL:
                    break
            append_label(make_row(diag, file_path, label, reason), path)
            recorded += 1
            break
    return recorded


def main():
    parser = argparse.ArgumentParser(description="Human labels for proposals (pilot reference data)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--next", nargs="?", type=int, const=-1, metavar="N",
                       help="Label unlabeled pending proposals interactively (at most N)")
    group.add_argument("--stats", action="store_true", help="Print label counts as JSON")
    group.add_argument("--export", action="store_true", help="Print current labels as JSONL")
    parser.add_argument("--dir", default=None, help="Proposals directory (default: proposal.get_proposals_dir())")
    args = parser.parse_args()

    if args.stats:
        print(json.dumps(stats(args.dir), indent=2))
    elif args.export:
        for row in read_labels().values():
            print(json.dumps(row, ensure_ascii=False))
    else:
        if not _is_tty():
            print("--next needs an interactive terminal", file=sys.stderr)
            sys.exit(1)
        limit = None if args.next == -1 else args.next
        n = label_interactively(limit, args.dir)
        print(f"\n{n} label(s) recorded in {get_labels_path()}")


if __name__ == "__main__":
    main()
