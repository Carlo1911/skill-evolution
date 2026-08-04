"""A malformed proposal must record no sizes in history (P0-3, second-order).

Found while fixing P0-3, not reported by the review. `evaluate_and_record()` records
`content_size` so `original_size_for_target()` can read the earliest one back as the
cumulative-drift baseline. For a malformed proposal the "content" is an error message, so
persisting its length would make ~100 bytes of explanatory prose become "where this skill
started" -- and every later cumulative check against a real body would read as
several-thousand-percent growth.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from proposal import ProposalType, ProposedChange, SkillEvolutionProposal


@pytest.fixture(autouse=True)
def isolated_history(monkeypatch, tmp_path):
    monkeypatch.setenv("SKILL_EVOLUTION_HISTORY_PATH", str(tmp_path / "h.jsonl"))
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    return tmp_path / "h.jsonl"


def _entries(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _skill_entries(path):
    return [e for e in _entries(path) if e.get("target") == "skill:some-skill"]


def test_malformed_proposal_records_no_sizes(isolated_history):
    p = SkillEvolutionProposal(
        type=ProposalType.IMPROVE_EXISTING, target_skill="some-skill",
        summary="s", rationale="r",
        proposed_changes=[ProposedChange(field="body", description="described in prose only")],
    )

    _, combined, gate_passed = evaluate.evaluate_and_record(p)

    assert not gate_passed
    # The skill-text target records no sizes (P0-3 second-order). The proposal-document
    # target is a separate lineage and legitimately records its own entry.
    skill_entry, = _skill_entries(isolated_history)
    assert skill_entry["passed"] is False
    assert "content_size" not in skill_entry
    assert "baseline_size" not in skill_entry
    proposal_entry, = [e for e in _entries(isolated_history) if e.get("target") == f"proposal:{getattr(p, 'proposal_id', 'unknown')}"]
    assert proposal_entry["kind"] == "proposal"


def test_a_malformed_entry_does_not_become_the_cumulative_baseline(isolated_history):
    """The consequence: a later well-formed proposal must still see no baseline from it."""
    malformed = SkillEvolutionProposal(
        type=ProposalType.IMPROVE_EXISTING, target_skill="some-skill",
        summary="s", rationale="r",
        proposed_changes=[ProposedChange(field="body", new_value="")],
    )
    evaluate.evaluate_and_record(malformed)

    assert evaluate.original_size_for_target("skill:some-skill") is None


def test_a_well_formed_proposal_still_records_sizes(isolated_history):
    """Guard against the fix over-reaching: the normal path must keep recording."""
    body = "---\nname: some-skill\ndescription: d\n---\n\nGuidance text.\n"
    p = SkillEvolutionProposal(
        type=ProposalType.IMPROVE_EXISTING, target_skill="some-skill",
        summary="s", rationale="r",
        proposed_changes=[ProposedChange(field="body", old_value=body, new_value=body)],
    )

    evaluate.evaluate_and_record(p)

    skill_entry, = _skill_entries(isolated_history)
    assert skill_entry["content_size"] == len(body.encode("utf-8"))
