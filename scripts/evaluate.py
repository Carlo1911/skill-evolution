#!/usr/bin/env python3
"""Evaluation framework for skill-evolution proposals and targets.

Scores skill-evolution work (skill text, and fast-follow targets) via one or
more configurable evaluators and AI providers, gates auto-apply, and persists
a versioned quality history.

Usage:
    python3 scripts/evaluate.py --list-evaluators
"""

import json
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type


# ── Core result shape ───────────────────────────────────────────────

@dataclass
class EvalResult:
    score: float
    feedback: str
    passed: bool
    evaluator_name: str


# ── Evaluator interface ─────────────────────────────────────────────

class Evaluator(ABC):
    """Base interface every evaluator implements."""

    name: str = ""

    @abstractmethod
    def evaluate(self, content: str, context: Optional[Dict[str, Any]] = None) -> EvalResult:
        """Score `content` (optionally using `context`) and return an EvalResult."""
        raise NotImplementedError


# ── Registry (KTD4: plain name -> class dict, no dynamic discovery) ─

REGISTRY: Dict[str, Type[Evaluator]] = {}

DEFAULT_EVALUATORS = "deterministic,llm_judge,regression"
EVALUATORS_ENV_VAR = "SKILL_EVOLUTION_EVALUATORS"


def register_evaluator(name: str, cls: Type[Evaluator]) -> None:
    """Register an evaluator class under `name` in the module registry."""
    REGISTRY[name] = cls


def get_enabled_evaluators(env_value: Optional[str] = None) -> List[Evaluator]:
    """Resolve the enabled evaluator instances from SKILL_EVOLUTION_EVALUATORS.

    Falls back to DEFAULT_EVALUATORS when the env var is unset, empty, or
    whitespace-only. Raises ValueError for any name not present in REGISTRY.
    """
    raw = env_value if env_value is not None else os.environ.get(EVALUATORS_ENV_VAR)
    if raw is None or not raw.strip():
        raw = DEFAULT_EVALUATORS

    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        names = [n.strip() for n in DEFAULT_EVALUATORS.split(",")]

    evaluators = []
    for name in names:
        if name not in REGISTRY:
            raise ValueError(
                f"Unknown evaluator '{name}' in {EVALUATORS_ENV_VAR}. "
                f"Available: {', '.join(sorted(REGISTRY)) or '(none registered)'}"
            )
        evaluators.append(REGISTRY[name]())

    return evaluators


# ── Versioned evaluation history (KTD2: one shared append-only JSONL) ─

HISTORY_RETENTION_ENV_VAR = "SKILL_EVOLUTION_HISTORY_RETENTION"


def get_history_path() -> str:
    """Return the shared eval history JSONL path."""
    return os.path.join(os.getcwd(), "docs", "skill-evolution", "eval_history.jsonl")


def append_history(target: str, result: EvalResult, session_ids: Optional[List[str]] = None,
                    path: Optional[str] = None) -> None:
    """Append one JSON line for `target`'s evaluation result to the history file."""
    history_path = path or get_history_path()
    os.makedirs(os.path.dirname(history_path), exist_ok=True)

    entry = {
        "target": target,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_ids": session_ids or [],
        **asdict(result),
    }

    with open(history_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_history(target: str, path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return `target`'s history entries in chronological (insertion) order."""
    history_path = path or get_history_path()
    if not os.path.exists(history_path):
        return []

    entries = []
    with open(history_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("target") == target:
                entries.append(entry)
    return entries


def _read_all_entries(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _parse_retention(raw: Optional[str]):
    """Parse SKILL_EVOLUTION_HISTORY_RETENTION into ('count', int) or ('age_days', float).

    Returns None for unset/unbounded. Accepts a bare integer (max versions),
    or a suffixed value: '90d' (days) or '6mo' (months, ~30 days each).
    """
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if value.endswith("mo"):
        return ("age_days", float(value[:-2]) * 30)
    if value.endswith("d"):
        return ("age_days", float(value[:-1]))
    return ("count", int(value))


def prune_history(path: Optional[str] = None, retention: Optional[str] = None) -> None:
    """Prune the shared history file per SKILL_EVOLUTION_HISTORY_RETENTION.

    Always retains the most recent entry per target regardless of the limit.
    """
    history_path = path or get_history_path()
    raw_retention = retention if retention is not None else os.environ.get(HISTORY_RETENTION_ENV_VAR)
    rule = _parse_retention(raw_retention)
    if rule is None:
        return  # unbounded

    entries = _read_all_entries(history_path)
    if not entries:
        return

    by_target: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        by_target.setdefault(entry.get("target", ""), []).append(entry)

    kept: List[Dict[str, Any]] = []
    kind, limit = rule
    for target_entries in by_target.values():
        most_recent = target_entries[-1]
        if kind == "count":
            survivors = target_entries[-int(limit):] if limit > 0 else [most_recent]
        else:
            cutoff = datetime.now(timezone.utc).timestamp() - (limit * 86400)
            survivors = []
            for entry in target_entries:
                try:
                    ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
                except (KeyError, ValueError):
                    ts = 0
                if ts >= cutoff:
                    survivors.append(entry)
        if most_recent not in survivors:
            survivors.append(most_recent)
        kept.extend(survivors)

    # Preserve original relative ordering
    kept_ids = {id(e) for e in kept}
    ordered_kept = [e for e in entries if id(e) in kept_ids]

    with open(history_path, "w") as f:
        for entry in ordered_kept:
            f.write(json.dumps(entry) + "\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Skill evolution evaluation tools")
    parser.add_argument("--list-evaluators", action="store_true", help="Print the resolved evaluator list")
    args = parser.parse_args()

    if args.list_evaluators:
        try:
            evaluators = get_enabled_evaluators()
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        for ev in evaluators:
            print(ev.name)


if __name__ == "__main__":
    main()
