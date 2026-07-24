#!/usr/bin/env python3
"""Optional GEPA-lite optimizer: reads the evaluation history for low-scoring
targets and drafts skill-improvement proposals through the normal
proposal/gate path.

Gated behind SKILL_EVOLUTION_OPTIMIZER_ENABLED=true. The optional `dspy`
dependency is only required when enabled (install with
`pip install -e ".[optimizer]"`); the rest of the pipeline works with it
absent.

Usage:
    SKILL_EVOLUTION_OPTIMIZER_ENABLED=true python3 scripts/optimize_skill.py
"""

import os
import sys
from typing import Any, Dict, List, Optional

OPTIMIZER_ENABLED_ENV_VAR = "SKILL_EVOLUTION_OPTIMIZER_ENABLED"
DEFAULT_LOW_SCORE_THRESHOLD = 0.6


def is_enabled() -> bool:
    return os.environ.get(OPTIMIZER_ENABLED_ENV_VAR, "false").strip().lower() == "true"


def _require_dspy():
    """Import the optional `dspy` dependency, raising a clear, actionable error if absent."""
    try:
        import dspy
        return dspy
    except ImportError as e:
        raise RuntimeError(
            "The optional optimizer dependency 'dspy' is not installed. "
            "Install it with: pip install -e '.[optimizer]'"
        ) from e


def find_low_scoring_targets(threshold: float = DEFAULT_LOW_SCORE_THRESHOLD,
                              history_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return each target's most recent history entry when it scores below `threshold`."""
    import evaluate

    entries = evaluate._read_all_entries(history_path or evaluate.get_history_path())
    latest_by_target: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        latest_by_target[entry.get("target", "")] = entry  # entries are chronological; last wins

    return [
        entry for entry in latest_by_target.values()
        if entry.get("score", 1.0) < threshold and entry.get("feedback")
    ]


def draft_proposal_for_target(entry: Dict[str, Any]):
    """Construct a normal improve_existing SkillEvolutionProposal from a low-scoring entry."""
    import proposal as proposal_module

    target = entry.get("target", "")
    skill_name = target.split(":", 1)[1] if target.startswith("skill:") else target
    feedback = entry.get("feedback", "")

    return proposal_module.SkillEvolutionProposal(
        type=proposal_module.ProposalType.IMPROVE_EXISTING,
        target_skill=skill_name,
        confidence=0.5,  # the evaluation gate (R15), not this confidence, is the real check
        summary=f"Optimizer-proposed improvement for {skill_name}",
        rationale=f"Low score ({entry.get('score')}) with feedback: {feedback}",
        proposed_changes=[proposal_module.ProposedChange(
            field="body",
            description="Address the evaluator feedback below.",
            new_value=f"Address this feedback and revise: {feedback}",
        )],
    )


def run_optimizer(history_path: Optional[str] = None, proposals_dir: Optional[str] = None) -> List[str]:
    """Draft and save proposals for every low-scoring target.

    Only saves proposals (status: proposed) via the normal save_proposal() path
    (U10) -- it never calls apply_proposal() itself, so optimizer-originated
    proposals go through the exact same evaluation gate as any other
    proposal, with no auto-apply bypass (R15).
    """
    _require_dspy()
    import proposal as proposal_module

    saved_paths = []
    for entry in find_low_scoring_targets(history_path=history_path):
        drafted = draft_proposal_for_target(entry)
        saved_paths.append(proposal_module.save_proposal(drafted, directory=proposals_dir))
    return saved_paths


def main():
    if not is_enabled():
        print(
            f"Optimizer disabled ({OPTIMIZER_ENABLED_ENV_VAR} is not 'true'); exiting.",
            file=sys.stderr,
        )
        return

    try:
        saved_paths = run_optimizer()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    for path in saved_paths:
        print(f"Optimizer proposal saved: {path}")


if __name__ == "__main__":
    main()
