"""Tests for scripts/evaluate.py's retroactive/batch re-evaluation mode (U9)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import proposal as proposal_module
from evaluate import EvalResult, retroactive_reevaluate
from proposal import ProposalType, ProposedChange, SkillEvolutionProposal, save_proposal


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


def _seed_proposal(proposals_dir, target_skill, proposal_id):
    proposal = SkillEvolutionProposal(
        proposal_id=proposal_id,
        type=ProposalType.IMPROVE_EXISTING,
        target_skill=target_skill,
        confidence=0.9,
        summary=f"Improve {target_skill}",
        rationale="Fixture rationale.",
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
