#!/usr/bin/env python3
"""Evaluation framework for skill-evolution proposals and targets.

Scores skill-evolution work (skill text, and fast-follow targets) via one or
more configurable evaluators and AI providers, gates auto-apply, and persists
a versioned quality history.

Usage:
    python3 scripts/evaluate.py --list-evaluators
"""

import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
