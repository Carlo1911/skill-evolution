"""Tests for scripts/evaluate.py's retroactive/batch re-evaluation mode (U9)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import proposal as proposal_module
from evaluate import EvalResult, retroactive_reevaluate
from proposal import ProposalStatus, ProposalType, ProposedChange, SkillEvolutionProposal, save_proposal


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    history_path = str(tmp_path / "eval_history.jsonl")
    proposals_dir = str(tmp_path / "proposals")
    os.makedirs(proposals_dir, exist_ok=True)
    monkeypatch.setattr(evaluate, "get_history_path", lambda: history_path)
    return {"history_path": history_path, "proposals_dir": proposals_dir}


@pytest.fixture(autouse=True)
def stub_evaluator(monkeypatch):
    monkeypatch.setattr(
        evaluate, "run_evaluators",
        lambda content, target, context=None: [
            EvalResult(score=0.9, passed=True, feedback="stub", evaluator_name="stub"),
        ],
    )


def _seed_proposal(proposals_dir, target_skill, proposal_id, status=ProposalStatus.PROPOSED):
    proposal = SkillEvolutionProposal(
        proposal_id=proposal_id,
        type=ProposalType.IMPROVE_EXISTING,
        target_skill=target_skill,
        confidence=0.9,
        summary=f"Improve {target_skill}",
        rationale="Fixture rationale.",
        status=status,
        proposed_changes=[ProposedChange(field="body", new_value="Body content.")],
    )
    save_proposal(proposal, directory=proposals_dir)
    return proposal


def test_retroactive_reevaluation_appends_new_entry_preserving_original(isolated_dirs):
    """Covers AE3: a target evaluated a week ago is re-evaluated; original entry preserved."""
    proposal = _seed_proposal(isolated_dirs["proposals_dir"], "test-skill", "p1")
    target = evaluate.target_key_for_proposal(proposal)

    # Seed an "original" entry with a known score, as if evaluated a week ago.
    evaluate.append_history(target, EvalResult(score=0.6, passed=True, feedback="old", evaluator_name="gate"))

    retroactive_reevaluate(proposals_dir=isolated_dirs["proposals_dir"])

    entries = evaluate.read_history(target)
    assert len(entries) == 2
    assert entries[0]["score"] == 0.6
    assert entries[0]["feedback"] == "old"
    assert entries[1]["score"] != 0.6


def test_no_matching_proposals_returns_empty_summaries(isolated_dirs):
    summaries = retroactive_reevaluate(proposals_dir=isolated_dirs["proposals_dir"])
    assert summaries == []


def test_target_filters_to_single_named_target(isolated_dirs):
    p1 = _seed_proposal(isolated_dirs["proposals_dir"], "skill-one", "p1")
    _seed_proposal(isolated_dirs["proposals_dir"], "skill-two", "p2")

    target = evaluate.target_key_for_proposal(p1)
    summaries = retroactive_reevaluate(target=target, proposals_dir=isolated_dirs["proposals_dir"])

    assert len(summaries) == 1
    assert summaries[0]["target"] == "skill:skill-one"


def test_running_retroactive_twice_produces_two_distinct_entries(isolated_dirs):
    proposal = _seed_proposal(isolated_dirs["proposals_dir"], "test-skill", "p1")
    target = evaluate.target_key_for_proposal(proposal)

    retroactive_reevaluate(proposals_dir=isolated_dirs["proposals_dir"])
    retroactive_reevaluate(proposals_dir=isolated_dirs["proposals_dir"])

    entries = evaluate.read_history(target)
    assert len(entries) == 2


def test_rejected_and_applied_proposals_are_excluded_by_default(isolated_dirs):
    """A rejected/applied proposal is not a live decision and must not inject a
    score into the same history stream the live apply_proposal() gate regresses against."""
    _seed_proposal(isolated_dirs["proposals_dir"], "proposed-skill", "p1", status=ProposalStatus.PROPOSED)
    _seed_proposal(isolated_dirs["proposals_dir"], "rejected-skill", "p2", status=ProposalStatus.REJECTED)
    _seed_proposal(isolated_dirs["proposals_dir"], "applied-skill", "p3", status=ProposalStatus.APPLIED)

    summaries = retroactive_reevaluate(proposals_dir=isolated_dirs["proposals_dir"])

    assert {s["target"] for s in summaries} == {"skill:proposed-skill"}


def test_include_all_statuses_opts_back_in(isolated_dirs):
    _seed_proposal(isolated_dirs["proposals_dir"], "proposed-skill", "p1", status=ProposalStatus.PROPOSED)
    _seed_proposal(isolated_dirs["proposals_dir"], "rejected-skill", "p2", status=ProposalStatus.REJECTED)

    proposals = evaluate._select_retroactive_proposals(
        proposals_dir=isolated_dirs["proposals_dir"], include_all_statuses=True,
    )

    assert {p.proposal_id for p in proposals} == {"p1", "p2"}


def test_include_all_statuses_reevaluates_rejected_and_applied(isolated_dirs):
    """The opt-in must reach the full retroactive path, not just the selector."""
    _seed_proposal(isolated_dirs["proposals_dir"], "proposed-skill", "p1", status=ProposalStatus.PROPOSED)
    _seed_proposal(isolated_dirs["proposals_dir"], "rejected-skill", "p2", status=ProposalStatus.REJECTED)
    _seed_proposal(isolated_dirs["proposals_dir"], "applied-skill", "p3", status=ProposalStatus.APPLIED)

    summaries = retroactive_reevaluate(proposals_dir=isolated_dirs["proposals_dir"],
                                       include_all_statuses=True)

    assert {s["target"] for s in summaries} == {"skill:proposed-skill",
                                                "skill:rejected-skill",
                                                "skill:applied-skill"}


def test_since_with_timezone_naive_date_does_not_crash(isolated_dirs):
    proposal = _seed_proposal(isolated_dirs["proposals_dir"], "test-skill", "p1")
    proposals = evaluate._select_retroactive_proposals(
        since="2020-01-01", proposals_dir=isolated_dirs["proposals_dir"],
    )
    assert proposal.proposal_id in [p.proposal_id for p in proposals]


def test_since_with_malformed_date_raises_clear_value_error(isolated_dirs):
    with pytest.raises(ValueError, match="invalid --since date"):
        evaluate._select_retroactive_proposals(since="not-a-date", proposals_dir=isolated_dirs["proposals_dir"])
