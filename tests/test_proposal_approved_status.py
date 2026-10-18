"""`status: approved` is a pending status: it can be applied, re-scored and diagnosed.

README and SKILL.md tell a reviewer to set `status: approved` and then call
apply_proposal(), which accepted only `proposed` and raised on every host. The retroactive
selection and the queue diagnostic filtered on `proposed` too, so an approved proposal
vanished from both.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import proposal as proposal_module
import proposal_diagnostics
from host_sandbox import HOSTS, build_host
from proposal import ProposalStatus, ProposedChange, SkillEvolutionProposal

BODY = "---\nname: demo\ndescription: d\n---\n\n# Demo\n\nStep one.\n\nStep two.\n"


@pytest.fixture(params=HOSTS)
def sandbox(request, tmp_path, monkeypatch):
    box = build_host(request.param, tmp_path, monkeypatch)
    box.write_skill("demo", BODY)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_TARGETS", "skill")
    return box


def _proposal(status, pid="p1"):
    return SkillEvolutionProposal(
        proposal_id=pid, target_skill="demo", confidence=0.9, summary="s", rationale="r",
        status=status, proposed_changes=[ProposedChange("body", "Step two.", "Step two, again.")])


def test_an_approved_proposal_is_applied(sandbox, tmp_path):
    p = _proposal(ProposalStatus.APPROVED)

    result = proposal_module.apply_proposal(p, min_confidence=0.5, directory=str(tmp_path / "q"))

    assert result["can_apply"] is True
    assert p.status == ProposalStatus.APPLIED
    if sandbox.name == "claude_code":
        assert "Step two, again." in sandbox.read_skill("demo")
    else:
        assert result["instructions"][0]["action"] == "patch"


@pytest.mark.parametrize("status", [ProposalStatus.REJECTED, ProposalStatus.APPLIED])
def test_decided_proposals_are_still_refused(sandbox, tmp_path, status):
    with pytest.raises(ValueError, match="only 'proposed' or 'approved'"):
        proposal_module.apply_proposal(_proposal(status), min_confidence=0.5,
                                       directory=str(tmp_path / "q"))


def test_retroactive_selection_includes_approved(sandbox, tmp_path):
    directory = str(tmp_path / "q")
    for pid, status in [("a", ProposalStatus.PROPOSED), ("b", ProposalStatus.APPROVED),
                        ("c", ProposalStatus.REJECTED), ("d", ProposalStatus.APPLIED)]:
        proposal_module.save_proposal(_proposal(status, pid=pid), directory=directory)

    selected = evaluate._select_retroactive_proposals(proposals_dir=directory)

    assert sorted(p.proposal_id for p in selected) == ["a", "b"]


def test_diagnostics_classify_approved_by_default(sandbox, tmp_path):
    directory = str(tmp_path / "q")
    proposal_module.save_proposal(_proposal(ProposalStatus.APPROVED, pid="b"), directory=directory)
    proposal_module.save_proposal(_proposal(ProposalStatus.REJECTED, pid="c"), directory=directory)

    diags = proposal_diagnostics.diagnose_queue(directory)

    assert [d["proposal_id"] for d in diags] == ["b"]
    assert diags[0]["status"] == "approved"
