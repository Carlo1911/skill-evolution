"""The size guards must not silently no-op on a body change with no new_value (P0-3).

Found by reviewing the first two proposals the live cron produced on 2026-07-29. Both hit
`_extract_evaluated_content()`'s summary_rationale fallback, and the gate passed both:

- `20260729-001` wanted to add ~500B to a 19,018-byte installed skill -- already 3.6KB over
  the 15KB cap -- but its body change carried no `new_value` at all, so the gate scored 63
  bytes of its own summary instead. content_kind was not "body", so the frontmatter check was
  skipped; _resolve_baseline() only resolves for body changes, so baseline_size was None and
  *every* growth, shrink, byte-floor and cumulative check was inert. The ratchet -- the guard
  whose entire purpose is stopping an oversized skill from growing -- never ran on a proposal
  that grows an oversized skill.
- `20260729-002` listed `description` before `body`, and the scan returned the first match,
  so its description was scored and its placeholder body never looked at.

The fallback itself is legitimate for merge_skills/deprecate_skill, which genuinely have no
body field. What was wrong is that it also absorbed a *broken* improve_existing.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from proposal import ProposalType, ProposedChange, SkillEvolutionProposal


def _proposal(ptype, changes, target_skill="some-skill"):
    return SkillEvolutionProposal(
        type=ptype,
        target_skill=target_skill,
        summary="A summary",
        rationale="A rationale",
        proposed_changes=changes,
    )


# ── The extraction contract ──────────────────────────────────────────


def test_body_change_without_new_value_is_malformed_not_summary_fallback():
    """The 20260729-001 shape. Previously returned ("<summary>\\n\\n<rationale>",
    "summary_rationale", None), which disabled every size check."""
    p = _proposal(ProposalType.IMPROVE_EXISTING, [
        ProposedChange(field="body", description="Add a Provider Idle Timeout section"),
    ])
    content, kind, _ = evaluate._extract_evaluated_content(p)
    assert kind == "malformed"
    assert "body" in content and "new_value" in content


def test_empty_string_new_value_is_also_malformed():
    p = _proposal(ProposalType.IMPROVE_EXISTING, [ProposedChange(field="body", new_value="")])
    _, kind, _ = evaluate._extract_evaluated_content(p)
    assert kind == "malformed"


def test_body_wins_over_description_when_both_are_present():
    """The 20260729-002 shape: extraction must not depend on list order."""
    changes = [
        ProposedChange(field="description", new_value="a new description"),
        ProposedChange(field="body", new_value="---\nname: x\ndescription: y\n---\nbody text"),
    ]
    content, kind, _ = evaluate._extract_evaluated_content(_proposal(ProposalType.CREATE_NEW, changes))
    assert kind == "body"
    assert "body text" in content

    # ...and the same when the order is reversed.
    content, kind, _ = evaluate._extract_evaluated_content(
        _proposal(ProposalType.CREATE_NEW, list(reversed(changes)))
    )
    assert kind == "body"


def test_description_only_change_still_scores_as_a_description():
    p = _proposal(ProposalType.IMPROVE_EXISTING,
                  [ProposedChange(field="description", new_value="a new description")])
    content, kind, _ = evaluate._extract_evaluated_content(p)
    assert kind == "description"
    assert content == "a new description"


def test_merge_skills_keeps_the_summary_rationale_fallback():
    """The fallback exists for shapes with genuinely no body field -- unchanged."""
    p = _proposal(ProposalType.MERGE_SKILLS,
                  [ProposedChange(field="source_a", new_value="skill-a")])
    content, kind, _ = evaluate._extract_evaluated_content(p)
    assert kind == "summary_rationale"
    assert "A summary" in content


def test_deprecate_skill_keeps_the_summary_rationale_fallback():
    p = _proposal(ProposalType.DEPRECATE_SKILL, [])
    _, kind, _ = evaluate._extract_evaluated_content(p)
    assert kind == "summary_rationale"


# ── The gate consequence ─────────────────────────────────────────────


def test_gate_fails_a_malformed_proposal_without_calling_a_provider(monkeypatch):
    """Fail closed (R21), and don't spend a provider call on something unapplicable."""
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic,llm_judge")
    monkeypatch.setattr(
        evaluate, "call_provider",
        lambda *a, **k: pytest.fail("no provider call for a malformed proposal"),
    )
    p = _proposal(ProposalType.IMPROVE_EXISTING, [ProposedChange(field="body")])

    results = evaluate.evaluate_skill_text(p)

    assert results and not any(r.passed for r in results)
    assert not evaluate.combine_gate(results, "strict")
    assert "new_value" in results[0].feedback


def test_a_well_formed_body_change_is_unaffected(monkeypatch):
    """Guard against over-rejecting: the normal path must still reach the evaluators."""
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    body = "---\nname: some-skill\ndescription: does a thing\n---\n\n# Some Skill\n\nGuidance.\n"
    p = _proposal(ProposalType.IMPROVE_EXISTING,
                  [ProposedChange(field="body", old_value=body, new_value=body)])

    results = evaluate.evaluate_skill_text(p)

    assert all(r.passed for r in results)


def test_the_ratchet_now_actually_runs_on_an_oversized_skill(monkeypatch):
    """The defect's real-world consequence, end to end.

    A body change that grows a skill already over the absolute cap must be rejected. Before
    P0-3 this proposal shape reached the gate as `summary_rationale` with baseline_size
    absent, so the ratchet never evaluated it.
    """
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    header = "---\nname: big-skill\ndescription: d\n---\n"
    oversized = header + ("x" * 19_000)                 # ~19KB, over the 15KB cap
    grown = header + ("x" * 19_500)                     # larger still
    monkeypatch.setattr(evaluate, "installed_skill_body", lambda name: oversized)

    p = _proposal(ProposalType.IMPROVE_EXISTING,
                  [ProposedChange(field="body", old_value=oversized, new_value=grown)],
                  target_skill="big-skill")

    results = evaluate.evaluate_skill_text(p)

    assert not any(r.passed for r in results), "the ratchet must reject growth on an oversized skill"
