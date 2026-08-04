"""Tests that SKILL_EVOLUTION_HISTORY_RETENTION now applies automatically, not just via a
manually-run `evaluate.py --prune`.

Before this, prune_history()'s only caller was the --prune CLI flag, so an unattended
deployment with a retention policy configured still grew the file forever unless someone
remembered to run --prune. evaluate_and_record() now prunes after every append by default
(a no-op unless retention is configured, so nothing changes for anyone who hasn't opted
in) -- except inside retroactive_reevaluate()'s batch loop, which prunes once after the
whole batch instead of once per proposal, since prune_history() rewrites the *shared*
history file (every target), not just the one being re-evaluated.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import proposal as proposal_module
from evaluate import EvalResult, evaluate_and_record, retroactive_reevaluate
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


@pytest.fixture(autouse=True)
def clean_retention_env(monkeypatch):
    monkeypatch.delenv(evaluate.HISTORY_RETENTION_ENV_VAR, raising=False)


def _proposal(target_skill, proposal_id):
    return SkillEvolutionProposal(
        proposal_id=proposal_id,
        type=ProposalType.IMPROVE_EXISTING,
        target_skill=target_skill,
        confidence=0.9,
        summary=f"Improve {target_skill}",
        rationale="Fixture rationale.",
        status=ProposalStatus.PROPOSED,
        proposed_changes=[ProposedChange(field="body", new_value="Body content.")],
    )


def test_evaluate_and_record_prunes_automatically_when_retention_configured(isolated_dirs, monkeypatch):
    monkeypatch.setenv(evaluate.HISTORY_RETENTION_ENV_VAR, "1")
    target = "skill:demo"
    proposal = _proposal("demo", "p1")

    for _ in range(4):
        evaluate.append_history(target, EvalResult(score=0.5, passed=False, feedback="old", evaluator_name="gate"))
    evaluate_and_record(proposal)

    entries = evaluate.read_history(target)
    # count=1 window (the just-written entry) + earliest-entry anchor (the first old one) --
    # not all 5, proving auto-prune actually ran without a manual --prune.
    assert len(entries) == 2


def test_evaluate_and_record_is_noop_prune_when_retention_unset(isolated_dirs):
    """Explicit regression lock: the default must not touch anything for callers that
    haven't opted into a retention policy."""
    target = "skill:demo"
    proposal = _proposal("demo", "p1")

    for _ in range(10):
        evaluate_and_record(proposal)

    assert len(evaluate.read_history(target)) == 10
    assert not os.path.exists(evaluate.get_history_archive_path(isolated_dirs["history_path"]))


def test_retroactive_batch_prunes_once_not_per_proposal(isolated_dirs, monkeypatch):
    monkeypatch.setenv(evaluate.HISTORY_RETENTION_ENV_VAR, "1")
    for i in range(4):
        proposal = _proposal(f"skill-{i}", f"p{i}")
        save_proposal(proposal, directory=isolated_dirs["proposals_dir"])

    calls = []
    original = evaluate.prune_history

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(evaluate, "prune_history", spy)

    retroactive_reevaluate(proposals_dir=isolated_dirs["proposals_dir"])

    assert len(calls) == 1


def test_apply_proposal_prunes_after_each_call(isolated_dirs, monkeypatch):
    """apply_proposal() calls evaluate_and_record(proposal) with no extra kwargs, so it
    must get auto_prune=True for free via the new default -- no changes needed in
    proposal.py itself."""
    monkeypatch.setenv(evaluate.HISTORY_RETENTION_ENV_VAR, "1")
    target = "skill:demo"

    for i in range(3):
        proposal = _proposal("demo", f"p{i}")
        proposal_module.apply_proposal(proposal, min_confidence=0.0, directory=isolated_dirs["proposals_dir"])

    entries = evaluate.read_history(target)
    # count=1 window + earliest-entry anchor: at most 2 survive despite 3 calls, proving
    # each apply_proposal() call pruned on its own rather than only at the very end.
    assert len(entries) <= 2


def test_evaluate_and_record_propagates_transport_failure_flag(isolated_dirs, monkeypatch):
    """A transport failure in any evaluator must surface on the combined history entry,
    so a reviewer (or find_low_scoring_targets) can tell an outage-driven 0.0 from a real
    regression."""
    monkeypatch.setattr(
        evaluate, "run_evaluators",
        lambda content, target, context=None: [
            EvalResult(score=0.0, passed=False, feedback="503 from provider", evaluator_name="llm_judge",
                       transport_failure=True),
        ],
    )

    proposal = _proposal("demo", "p1")
    evaluate_and_record(proposal)

    entries = evaluate.read_history("skill:demo")
    assert len(entries) == 1
    assert entries[0]["transport_failure"] is True


def test_evaluate_and_record_leaves_flag_unset_when_no_transport_failure(isolated_dirs):
    proposal = _proposal("demo", "p1")
    evaluate_and_record(proposal)

    entries = evaluate.read_history("skill:demo")
    assert len(entries) == 1
    assert "transport_failure" not in entries[0]
