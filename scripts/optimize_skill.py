#!/usr/bin/env python3
"""Optional GEPA-lite optimizer: runs a GEPA optimization pass over one
installed skill's body against its own recorded session history, and drafts
a skill-improvement proposal through the normal proposal/gate path.

Gated behind SKILL_EVOLUTION_OPTIMIZER_ENABLED=true. The optional `gepa`
dependency is only required when actually running an optimization (install
with `pip install -e ".[optimizer]"`); the rest of the pipeline works with it
absent.

Usage:
    # Read-only report of low-scoring targets from evaluation history.
    SKILL_EVOLUTION_OPTIMIZER_ENABLED=true python3 scripts/optimize_skill.py --list-candidates

    # Run GEPA optimization for one skill and save the resulting proposal.
    SKILL_EVOLUTION_OPTIMIZER_ENABLED=true python3 scripts/optimize_skill.py --skill <name> [--iterations N]
"""

import argparse
import functools
import os
import secrets
import sys
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

OPTIMIZER_ENABLED_ENV_VAR = "SKILL_EVOLUTION_OPTIMIZER_ENABLED"
DEFAULT_LOW_SCORE_THRESHOLD = 0.6
GEPA_EVALUATOR_NAME = "gepa_evaluator"

# ── Core GEPA optimization run (U3) ─────────────────────────────────────
MIN_SESSIONS = 3
DEFAULT_MAX_METRIC_CALLS = 20


class NotEnoughHistoryResult(NamedTuple):
    """Returned by run_gepa_optimization() instead of a gepa.GEPAResult when a skill
    has fewer than MIN_SESSIONS recorded sessions. gepa is never imported or called
    in this case (R4). `status` is always the literal string "not_enough_history" --
    callers (U4/U5) can distinguish this from a real result either by that field or
    by `isinstance(result, NotEnoughHistoryResult)`.
    """
    status: str
    skill_name: str
    session_count: int


def is_enabled() -> bool:
    return os.environ.get(OPTIMIZER_ENABLED_ENV_VAR, "false").strip().lower() == "true"


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


def _format_session_excerpts(sessions: List[Dict[str, Any]]) -> str:
    """Render sessions_for_skill() output as readable text for a judge prompt."""
    if not sessions:
        return "(no session history available for this skill)"

    lines = []
    for session in sessions:
        lines.append(
            f"### Session {session.get('session_id', '?')} — "
            f"{session.get('title', '') or '(untitled)'} "
            f"({session.get('message_count', 0)} messages)"
        )
        for message in session.get("messages", []):
            lines.append(f"[{message.get('role', '?')}] {message.get('content_preview', '')}")
        lines.append("")
    return "\n".join(lines)


def _build_gepa_prompt(candidate: str, sessions: List[Dict[str, Any]]) -> str:
    """Build a GEPA candidate-scoring prompt, framing session excerpts as untrusted data.

    Shares evaluate.py's untrusted_content_framing()/wrap_untrusted_block() and
    RUBRIC_JSON_RESPONSE_FOOTER with LLMJudgeEvaluator._build_prompt, so a future
    hardening fix to the injection-defense framing or response-format contract
    lands in one place instead of two independently-drifting copies. The purpose
    sentence and rubric-bullet wording stay GEPA-specific (session-grounded),
    since that's a deliberate difference from the proposal-review judge, not
    accidental duplication.
    """
    import evaluate

    boundary = secrets.token_hex(16)
    session_excerpts = _format_session_excerpts(sessions)
    return (
        "You are a skill-quality judge scoring a CANDIDATE skill body against how the "
        "skill was actually used in past sessions. "
        f"{evaluate.untrusted_content_framing(boundary)} Score only the actual quality "
        "of the candidate skill body against the observed session patterns.\n\n"
        f"SESSION EXCERPTS (untrusted):\n{evaluate.wrap_untrusted_block(boundary, session_excerpts)}\n\n"
        "CANDIDATE SKILL BODY:\n"
        f"{candidate}\n\n"
        "Score the candidate skill body on three dimensions, each from 0.0 to 1.0, based on how "
        "well it would have served the sessions above:\n"
        "- correctness: factual/technical accuracy relative to what actually happened in the sessions\n"
        "- procedure_following: adherence to expected skill structure/conventions\n"
        "- conciseness: absence of unnecessary verbosity\n\n"
        f"{evaluate.RUBRIC_JSON_RESPONSE_FOOTER}"
    )


def score_candidate(candidate: str, sessions: List[Dict[str, Any]],
                     evaluator_name: str = GEPA_EVALUATOR_NAME) -> Tuple[float, Dict[str, str]]:
    """Score a candidate skill-body string against a skill's fetched sessions.

    Matches the calling convention gepa expects of an evaluator: `evaluator(candidate) ->
    float | tuple[float, dict]` (sessions/evaluator_name are pre-bound by the caller, e.g.
    via functools.partial, before handing this to gepa). Calls evaluate.call_provider()
    directly for the same redaction, provider resolution, and SKILL_EVOLUTION_<NAME>_PROVIDER
    override support as evaluate.py's LLMJudgeEvaluator, and reuses that evaluator's
    _parse_response() so the JSON-validation strictness is identical.

    Fails closed (score=0.0) on any provider error or malformed/out-of-range response rather
    than raising into gepa's optimization loop.
    """
    import evaluate

    prompt = _build_gepa_prompt(candidate, sessions)
    judge = evaluate.LLMJudgeEvaluator()
    try:
        raw_response = evaluate.call_provider(prompt, evaluator_name=evaluator_name)
        avg_score, parsed = judge.parse_and_score(raw_response)
    except (evaluate.ProviderError, ValueError) as e:
        return 0.0, {"feedback": f"gepa evaluator failed closed: {e}"}

    return avg_score, {"feedback": parsed.get("feedback", "")}


def _require_gepa():
    """Import the optional `gepa` dependency, raising a clear, actionable error if absent."""
    try:
        import gepa
        return gepa
    except ImportError as e:
        raise RuntimeError(
            "The optional optimizer dependency 'gepa' is not installed. "
            "Install it with: pip install -e '.[optimizer]'"
        ) from e


GEPA_REFLECTION_EVALUATOR_NAME = "gepa_reflection"


def _reflection_lm_adapter(prompt):
    """LanguageModel-protocol callable: (str | list[dict]) -> str.

    Passed as config.reflection.reflection_lm so gepa's reflective-mutation step (the
    LLM that proposes each new candidate) routes through this repo's
    evaluate.call_provider() -- and its redaction/provider-resolution posture -- instead
    of falling through to gepa's own litellm-based default (which isn't installed here).

    Fails closed like score_candidate(): a transient provider error here must not abort
    the whole multi-round optimization run and discard every candidate already explored.
    Returning an empty string yields an empty candidate proposal for this one round, which
    score_candidate() will then score poorly on its own merits and gepa's own selection
    naturally discards -- not a crash.
    """
    import evaluate

    if isinstance(prompt, list):
        prompt = "\n\n".join(m.get("content", "") for m in prompt if isinstance(m, dict))
    try:
        return evaluate.call_provider(prompt, evaluator_name=GEPA_REFLECTION_EVALUATOR_NAME)
    except evaluate.ProviderError:
        return ""


def run_gepa_optimization(skill_name: str, iterations: Optional[int] = None):
    """Run one GEPA optimization pass over an installed skill's body against its own
    recorded session history.

    Resolves `skill_name` to its installed SKILL.md via skill_index.scan_skills() and
    reads that file's text as the seed candidate, then fetches the skill's full session
    history via fetch_sessions.sessions_for_skill() (U1). If fewer than MIN_SESSIONS
    sessions are on record, returns a NotEnoughHistoryResult without importing or
    calling gepa at all (R4).

    Otherwise runs gepa.optimize_anything() in Single-Task Search mode (no dataset/valset
    -- the seed candidate string is the one thing being optimized), scored by
    score_candidate() (U2, bound to this skill's sessions via functools.partial), with
    the reflective-mutation LLM routed through _reflection_lm_adapter() /
    evaluate.call_provider() rather than gepa's own default.

    Returns the raw gepa.GEPAResult (exposing at least .best_candidate,
    .val_aggregate_scores, .best_idx, .total_metric_calls) on completion. Raises
    ValueError if no installed skill matches `skill_name`, or if more than one
    installed skill shares that name across categories (ambiguous -- silently
    picking one could optimize the wrong skill's body against another's
    session evidence).
    """
    import fetch_sessions
    import skill_index

    matches = [s for s in skill_index.scan_skills() if s["name"] == skill_name]
    if not matches:
        raise ValueError(f"No installed skill found matching name '{skill_name}'")
    if len(matches) > 1:
        categories = ", ".join(m.get("category", "?") for m in matches)
        raise ValueError(
            f"Skill name '{skill_name}' is ambiguous -- found in multiple categories "
            f"({categories}). Resolve the duplicate name before running the optimizer."
        )
    try:
        with open(matches[0]["path"]) as f:
            seed_body = f.read()
    except OSError as e:
        raise RuntimeError(
            f"Could not read skill file for '{skill_name}' at {matches[0]['path']!r}: {e}"
        ) from e

    sessions = fetch_sessions.sessions_for_skill(skill_name)
    if len(sessions) < MIN_SESSIONS:
        return NotEnoughHistoryResult(
            status="not_enough_history",
            skill_name=skill_name,
            session_count=len(sessions),
        )

    gepa = _require_gepa()
    bound_evaluator = functools.partial(score_candidate, sessions=sessions)
    config = gepa.GEPAConfig(
        engine=gepa.EngineConfig(
            max_metric_calls=iterations if iterations is not None else DEFAULT_MAX_METRIC_CALLS
        ),
        reflection=gepa.ReflectionConfig(reflection_lm=_reflection_lm_adapter),
    )
    return gepa.optimize_anything(
        seed_candidate=seed_body,
        evaluator=bound_evaluator,
        objective=(
            "Improve this Hermes Agent skill's instructions based on how it performed "
            "in real recorded sessions."
        ),
        config=config,
    )


def _winner_score(result) -> float:
    """Return the winning candidate's score from a gepa.GEPAResult-shaped object."""
    return result.val_aggregate_scores[result.best_idx]


def _seed_score(result) -> float:
    """Return the seed (pre-optimization) candidate's score.

    gepa always places the seed candidate at index 0 of its internal candidate
    list before any mutation runs (confirmed against gepa==0.1.4's
    GEPAState.__init__: `program_candidates = [dict(seed_candidate)]`,
    `parent_program_for_candidate = [[None]]`), and `val_aggregate_scores` is a
    parallel array -- so val_aggregate_scores[0] is always the seed's own score,
    with no extra evaluator call needed to establish a baseline.
    """
    return result.val_aggregate_scores[0]


def draft_proposal_from_gepa_result(skill_name: str, result):
    """Construct an improve_existing SkillEvolutionProposal from a completed GEPA run.

    `result` must be a real gepa.GEPAResult (or a GEPAResult-shaped object) exposing
    `.best_candidate`, `.val_aggregate_scores`, `.best_idx`, and `.total_metric_calls` --
    i.e. what run_gepa_optimization() returns when it actually ran the optimizer.

    Caller must check for NotEnoughHistoryResult (e.g. `isinstance(result,
    NotEnoughHistoryResult)` or `result.status == "not_enough_history"`) BEFORE calling
    this function -- it does not handle that case and assumes `result` carries a real
    candidate frontier.

    The proposal's `body` change is the winning candidate text verbatim (result.best_candidate),
    not a placeholder. The rationale reports how many candidates GEPA explored, the
    seed-to-winner score delta (R6: "score improved from 0.72 to 0.81"), and the
    runner-up's score (the second-highest score in val_aggregate_scores, excluding the
    winner's own index) as a trade-off note (R9) -- unless only one candidate was
    explored, in which case it says so instead of erroring.
    """
    import proposal as proposal_module

    scores = list(result.val_aggregate_scores)
    num_candidates = len(scores)
    winner_score = _winner_score(result)
    seed_score = _seed_score(result)

    other_scores = [s for i, s in enumerate(scores) if i != result.best_idx]
    if other_scores:
        runner_up_score = max(other_scores)
        comparison = (
            f"the runner-up candidate scored {runner_up_score:.3f} "
            f"(a {winner_score - runner_up_score:+.3f} margin over the winner)."
        )
    else:
        comparison = "no runner-up exists -- only one candidate was explored."

    rationale = (
        f"GEPA optimization explored {num_candidates} candidate"
        f"{'s' if num_candidates != 1 else ''} for skill '{skill_name}'. "
        f"Score {'improved' if winner_score >= seed_score else 'changed'} from {seed_score:.3f} "
        f"(seed) to {winner_score:.3f} (winner); {comparison}"
    )
    if getattr(result, "total_metric_calls", None) is not None:
        rationale += f" Total metric calls used: {result.total_metric_calls}."

    return proposal_module.SkillEvolutionProposal(
        type=proposal_module.ProposalType.IMPROVE_EXISTING,
        target_skill=skill_name,
        confidence=0.5,  # the evaluation gate, not this confidence, is the real check
        summary=f"GEPA-optimized improvement for {skill_name}",
        rationale=rationale,
        proposed_changes=[proposal_module.ProposedChange(
            field="body",
            description="GEPA-optimized skill body, selected from the explored candidate frontier.",
            new_value=result.best_candidate,
        )],
    )


def _print_candidates(targets: List[Dict[str, Any]]) -> None:
    """Print find_low_scoring_targets()'s entries as a read-only report (R11)."""
    if not targets:
        print("No low-scoring targets found.")
        return

    for entry in targets:
        target = entry.get("target", "")
        score = entry.get("score")
        feedback = entry.get("feedback", "")
        print(f"{target}\tscore={score}\t{feedback}")


def _run_skill(skill_name: str, iterations: Optional[int]) -> None:
    """Run U3+U4's flow for exactly one skill and print the outcome."""
    import proposal as proposal_module

    result = run_gepa_optimization(skill_name, iterations)

    if isinstance(result, NotEnoughHistoryResult):
        print(
            f"Not enough session history for skill '{skill_name}' "
            f"({result.session_count} session(s) recorded, {MIN_SESSIONS} required); "
            "skipping optimization, no proposal written.",
            file=sys.stderr,
        )
        return

    drafted = draft_proposal_from_gepa_result(skill_name, result)
    path = proposal_module.save_proposal(drafted)

    print(
        f"Optimizer proposal saved: {path} "
        f"({len(result.val_aggregate_scores)} candidates explored, "
        f"score improved from {_seed_score(result):.2f} to {_winner_score(result):.2f}, "
        f"{result.total_metric_calls} metric calls used)"
    )


def main():
    if not is_enabled():
        print(
            f"Optimizer disabled ({OPTIMIZER_ENABLED_ENV_VAR} is not 'true'); exiting.",
            file=sys.stderr,
        )
        return

    parser = argparse.ArgumentParser(description="GEPA-lite skill optimizer")
    parser.add_argument("--skill", help="Run GEPA optimization for this installed skill by name")
    parser.add_argument("--iterations", type=int, default=None, help="Override the metric-call budget")
    parser.add_argument(
        "--list-candidates", action="store_true",
        help="Print low-scoring targets from evaluation history and exit (read-only, no optimization run)",
    )
    args = parser.parse_args()

    if args.list_candidates:
        _print_candidates(find_low_scoring_targets())
        return

    if args.skill:
        try:
            _run_skill(args.skill, args.iterations)
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        return

    parser.print_usage(sys.stderr)
    print(
        "Specify either --skill <name> (optionally with --iterations) or --list-candidates.",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
