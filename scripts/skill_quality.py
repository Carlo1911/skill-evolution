#!/usr/bin/env python3
"""Skill quality tracking: evaluate all installed skills periodically and report trends.

Usage:
    # Evaluate all skills, print report to stdout
    python3 scripts/skill_quality.py

    # Evaluate all skills, write report to file
    python3 scripts/skill_quality.py --output report.md

    # Evaluate a single skill
    python3 scripts/skill_quality.py --skill money-admin-messaging

    # Only evaluate skills not judged in the last 30 days (cost control:
    # each skill costs one LLM judge call)
    python3 scripts/skill_quality.py --since 30d

    # Evaluate skills below a threshold
    python3 scripts/skill_quality.py --below 0.7
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, NamedTuple, Optional

import evaluate
import host


class SkillQualityResult(NamedTuple):
    """Result of evaluating one skill's quality."""
    skill_name: str
    current_score: float
    previous_score: Optional[float]  # None if no prior evaluation
    delta: Optional[float]  # current - previous
    feedback: str  # LLM judge feedback
    evaluated_at: str  # ISO timestamp
    passed: bool  # Whether the evaluation passed


def create_synthetic_proposal(skill_name: str, skill_body: str) -> Any:
    """Create a synthetic proposal for quality evaluation.
    
    Creates a proposal with field="body", new_value=<skill body>, old_value=<skill body>
    (same text, so size guards pass). This allows reusing evaluate_skill_text() for
    quality tracking without creating a real change proposal.
    """
    from proposal import ProposedChange, SkillEvolutionProposal, ProposalType
    
    return SkillEvolutionProposal(
        proposal_id=f"quality_check_{skill_name}",
        type=ProposalType.IMPROVE_EXISTING,
        target_skill=skill_name,
        confidence=1.0,
        summary=f"Quality check for {skill_name}",
        rationale="Periodic quality evaluation",
        proposed_changes=[
            ProposedChange(
                field="body",
                old_value=skill_body,
                new_value=skill_body,
                description="Quality evaluation (no actual change)"
            )
        ],
        session_ids=[],
    )


def get_previous_score(skill_name: str) -> Optional[float]:
    """Get the most recent passing score for a skill from eval_history.jsonl.
    
    Returns None if no prior evaluation exists or if no passing entry was found.
    """
    target = f"skill:{skill_name}"
    history = evaluate.read_history(target)
    
    # Find the most recent passing entry
    for entry in reversed(history):
        if entry.get("passed") and entry.get("score") is not None:
            return entry["score"]
    
    return None


def get_last_evaluation_timestamp(skill_name: str) -> Optional[float]:
    """Get the timestamp (epoch seconds) of a skill's most recent evaluation.
    
    Returns None if the skill has never been evaluated. Used by `--since` to
    skip skills that were evaluated recently, so a full pass doesn't re-spend
    an LLM judge call on a skill judged a few days ago.
    """
    target = f"skill:{skill_name}"
    history = evaluate.read_history(target)
    if not history:
        return None
    
    last = history[-1]
    ts = last.get("timestamp")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def evaluate_skill_quality(skill_name: str) -> Optional[SkillQualityResult]:
    """Evaluate one skill's quality and return the result.
    
    Returns None if the skill cannot be resolved or evaluation fails.
    """
    # Get the installed skill body
    skill_body = evaluate.installed_skill_body(skill_name)
    if skill_body is None:
        paths = evaluate.ambiguous_skill_paths(skill_name)
        if paths:
            print(f"warning: skill '{skill_name}' is ambiguous ({len(paths)} installed files: "
                  f"{', '.join(paths)}); skipped", file=sys.stderr)
        else:
            print(f"warning: skill '{skill_name}' not found or unreadable", file=sys.stderr)
        return None
    
    # Get previous score before evaluation
    previous_score = get_previous_score(skill_name)
    
    # Create synthetic proposal
    proposal = create_synthetic_proposal(skill_name, skill_body)
    
    # Evaluate using the standard skill text evaluator
    try:
        results = evaluate.evaluate_skill_text(proposal)
        
        # Combine results (same as combine_gate)
        if not results:
            return None
        
        # Abstentions ("not applicable") count neither in the mean nor in the verdict.
        scored = evaluate.applicable(results)
        if not scored:
            return None
        current_score = evaluate.mean_score(results, default=0.0)
        
        # Get feedback from LLM judge if available
        feedback = ""
        for r in results:
            if r.evaluator_name == "llm_judge" and r.feedback:
                feedback = r.feedback
                break
        
        # Check if passed (all evaluators must pass in strict mode)
        passed = all(r.passed for r in scored)
        
        # Record the evaluation in history
        target = f"skill:{skill_name}"
        evaluated_at = datetime.now(timezone.utc).isoformat()
        
        # Combine into a single gate result. Carry the provider-fault flag forward the way
        # evaluate_and_record() does: without it an outage's 0.0 reads as a quality
        # signal and find_low_scoring_targets() queues the skill for optimization.
        combined_result = evaluate.EvalResult(
            score=current_score,
            feedback=feedback,
            passed=passed,
            evaluator_name="gate",
            transport_failure=evaluate.any_transport_failure(results),
        )
        
        # Append to history
        evaluate.append_history(
            target=target,
            result=combined_result,
            content_size=len(skill_body.encode("utf-8")),
            baseline_size=len(skill_body.encode("utf-8")),  # Same as content for quality checks
            kind="skill_text",
        )
        
        delta = (current_score - previous_score) if previous_score is not None else None
        
        return SkillQualityResult(
            skill_name=skill_name,
            current_score=current_score,
            previous_score=previous_score,
            delta=delta,
            feedback=feedback,
            evaluated_at=evaluated_at,
            passed=passed,
        )
        
    except Exception as e:
        print(f"warning: evaluation failed for '{skill_name}': {e}", file=sys.stderr)
        return None


def _exclude_human_review() -> None:
    """Drop `human_review` from SKILL_EVOLUTION_EVALUATORS, warning on stderr when it did.

    The periodic tracker is non-interactive by design (cron wrapper, report generation),
    so a globally-exported `human_review` must not reach the gate: interactively it would
    prompt for every skill, and under the cron wrapper (no TTY) it would record a
    fail-closed `passed=False` entry for every skill into eval_history.jsonl. Stripping it
    here keeps the tracker's gate entries purely automatic. No-op when absent.
    """
    enabled = evaluate.get_enabled_evaluators()
    if not any(e.name == "human_review" for e in enabled):
        return
    names = [n.strip() for n in os.environ.get(evaluate.EVALUATORS_ENV_VAR, evaluate.DEFAULT_EVALUATORS).split(",") if n.strip()]
    names = [n for n in names if n != "human_review"]
    os.environ[evaluate.EVALUATORS_ENV_VAR] = ",".join(names) if names else evaluate.DEFAULT_EVALUATORS
    print(
        "warning: SKILL_EVOLUTION_EVALUATORS includes human_review, but the periodic "
        "quality tracker is non-interactive; dropped it for this run",
        file=sys.stderr,
    )


def evaluate_all_skills(
    skill_names: Optional[List[str]] = None,
    since_days: Optional[int] = None,
    below_threshold: Optional[float] = None,
) -> List[SkillQualityResult]:
    """Evaluate all installed skills (or a subset) and return results.
    
    Args:
        skill_names: Optional list of specific skills to evaluate. If None, evaluates all.
        since_days: Skip skills evaluated within the last N days (each is one LLM call,
            so this is the cost-control knob — see the plan's "skip recently evaluated"
            rationale). Explicitly requested skills via `skill_names` are never skipped.
        below_threshold: Only include skills with current score below this threshold.
    
    Returns:
        List of SkillQualityResult, sorted by score (ascending).
    """
    # The tracker is non-interactive: never let a globally-exported human_review prompt
    # per skill or fail-close every skill's gate entry under the cron wrapper.
    _exclude_human_review()

    # Get all installed skills
    adapter = host.get_adapter()
    all_skills = adapter.iter_skills()
    
    if skill_names:
        # Filter to requested skills
        skills_to_eval = [s for s in all_skills if s["name"] in skill_names]
    else:
        skills_to_eval = all_skills
    
    # Skip recently-evaluated skills (unless explicitly requested)
    if since_days is not None:
        cutoff = datetime.now(timezone.utc).timestamp() - (since_days * 86400)
        filtered = []
        for s in skills_to_eval:
            last_ts = get_last_evaluation_timestamp(s["name"])
            skip = (last_ts is not None) and (last_ts >= cutoff)
            if not skip or (skill_names and s["name"] in skill_names):
                filtered.append(s)
        skills_to_eval = filtered
    
    results = []
    total = len(skills_to_eval)
    
    for i, skill in enumerate(skills_to_eval, 1):
        skill_name = skill["name"]
        print(f"[{i}/{total}] Evaluating {skill_name}...", file=sys.stderr)
        
        result = evaluate_skill_quality(skill_name)
        if result is not None:
            results.append(result)
    
    # Filter by below_threshold
    if below_threshold is not None:
        results = [r for r in results if r.current_score < below_threshold]
    
    # Sort by score (ascending)
    results.sort(key=lambda r: r.current_score)
    
    return results


def generate_quality_report(
    results: List[SkillQualityResult],
    output_format: str = "markdown",
) -> str:
    """Generate a quality report from evaluation results.
    
    Args:
        results: List of SkillQualityResult
        output_format: "markdown" (default) or "json"
    
    Returns:
        Formatted report string
    """
    if output_format == "json":
        return json.dumps(
            [r._asdict() for r in results],
            indent=2,
            default=str,
        )
    
    # Markdown format
    lines = []
    lines.append("# Skill Quality Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"Total skills evaluated: {len(results)}")
    
    if not results:
        lines.append("")
        lines.append("No skills evaluated.")
        return "\n".join(lines)
    
    # Calculate statistics
    scores = [r.current_score for r in results]
    avg_score = sum(scores) / len(scores)
    
    lines.append(f"Average score: {avg_score:.2f}")
    lines.append("")
    
    # Summary
    improved = [r for r in results if r.delta is not None and r.delta > 0.05]
    regressed = [r for r in results if r.delta is not None and r.delta < -0.05]
    stable = [r for r in results if r.delta is None or abs(r.delta) <= 0.05]
    
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- {len(improved)} skills improved (score increased by >0.05)")
    lines.append(f"- {len(regressed)} skills regressed (score decreased by >0.05)")
    lines.append(f"- {len(stable)} skills stable (change within ±0.05)")
    lines.append("")
    
    # Skills by score
    lines.append("## Skills by Score (ascending)")
    lines.append("")
    lines.append("| Skill | Score | Previous | Delta | Trend | Passed |")
    lines.append("|-------|-------|----------|-------|-------|--------|")
    
    for r in results:
        prev_str = f"{r.previous_score:.2f}" if r.previous_score is not None else "N/A"
        delta_str = f"{r.delta:+.2f}" if r.delta is not None else "N/A"
        
        if r.delta is None:
            trend = "→"
        elif r.delta > 0.05:
            trend = "↑"
        elif r.delta < -0.05:
            trend = "↓"
        else:
            trend = "→"
        
        passed_str = "✓" if r.passed else "✗"
        
        lines.append(f"| {r.skill_name} | {r.current_score:.2f} | {prev_str} | {delta_str} | {trend} | {passed_str} |")
    
    lines.append("")
    
    # Improvements
    if improved:
        lines.append("## Improvements")
        lines.append("")
        for r in improved:
            lines.append(f"- **{r.skill_name}**: {r.delta:+.2f} ({r.previous_score:.2f} → {r.current_score:.2f})")
            if r.feedback:
                lines.append(f"  - Feedback: {r.feedback[:200]}...")
        lines.append("")
    
    # Regressions
    if regressed:
        lines.append("## Regressions")
        lines.append("")
        for r in regressed:
            lines.append(f"- **{r.skill_name}**: {r.delta:+.2f} ({r.previous_score:.2f} → {r.current_score:.2f})")
            if r.feedback:
                lines.append(f"  - Feedback: {r.feedback[:200]}...")
        lines.append("")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate skill quality and generate reports"
    )
    parser.add_argument(
        "--skill",
        help="Evaluate a specific skill (can be specified multiple times)",
        action="append",
    )
    parser.add_argument(
        "--output",
        help="Write report to file instead of stdout",
    )
    parser.add_argument(
        "--since",
        help="Skip skills evaluated within the last N days (cost control; e.g., 30d)",
    )
    parser.add_argument(
        "--below",
        type=float,
        help="Only include skills with score below this threshold",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    
    args = parser.parse_args()
    
    # Parse --since
    since_days = None
    if args.since:
        since_str = args.since.strip()
        if since_str.endswith("d"):
            since_days = int(since_str[:-1])
        else:
            since_days = int(since_str)
    
    # Evaluate skills
    results = evaluate_all_skills(
        skill_names=args.skill,
        since_days=since_days,
        below_threshold=args.below,
    )
    
    # Generate report
    report = generate_quality_report(results, output_format=args.format)
    
    # Output
    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
