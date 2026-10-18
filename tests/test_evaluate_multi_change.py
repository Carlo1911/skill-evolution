"""The gate evaluates what apply writes: every change, composed onto the installed skill.

`_extract_evaluated_content()` kept one change per field -- the last -- so the gate spliced
and scored only that one while apply wrote all of them. Reproduced on both hosts: a
70-byte skill grew to 52 KB past the size cap with "all deterministic checks passed", an
earlier change with an empty new_value deleted its anchor text on Claude Code (and became
a `patch` with nothing to patch on Hermes), and an ambiguous earlier anchor went through
unexamined. 8 of the 39 real proposals carry several body changes.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import proposal as proposal_module
import proposal_diagnostics
from host_sandbox import HOSTS, build_host
from proposal import ProposedChange, SkillEvolutionProposal

BODY = ("---\nname: demo\ndescription: d\n---\n\n# Demo\n\nStep one.\n\nStep two.\n\n"
        "Shared line.\n\nShared line.\n")


@pytest.fixture(params=HOSTS)
def sandbox(request, tmp_path, monkeypatch):
    box = build_host(request.param, tmp_path, monkeypatch)
    box.write_skill("demo", BODY)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_TARGETS", "skill")
    return box


def _proposal(*changes, pid="p1"):
    return SkillEvolutionProposal(proposal_id=pid, target_skill="demo", confidence=0.9,
                                  summary="s", rationale="r", proposed_changes=list(changes))


def _gate_feedback(p):
    results = evaluate.evaluate_skill_text(p)
    return all(r.passed for r in results), " | ".join(r.feedback for r in results)


def _apply(p, sandbox, tmp_path):
    return proposal_module.apply_proposal(p, min_confidence=0.5, directory=str(tmp_path / "proposals"))


def test_every_body_change_counts_toward_the_size_cap(sandbox, tmp_path):
    big = "Step one.\n" + "padding line\n" * 4000
    p = _proposal(ProposedChange("body", "Step one.", big),
                  ProposedChange("body", "Step two.", "Step two, clarified."))

    passed, feedback = _gate_feedback(p)
    result = _apply(p, sandbox, tmp_path)

    assert passed is False
    assert "exceeds" in feedback
    assert result["can_apply"] is False
    assert sandbox.read_skill("demo") == BODY


def test_an_empty_new_value_in_any_change_is_refused(sandbox, tmp_path):
    p = _proposal(ProposedChange("body", "Step one.", ""),
                  ProposedChange("body", "Step two.", "Step two, clarified."))

    passed, feedback = _gate_feedback(p)
    result = _apply(p, sandbox, tmp_path)

    assert passed is False
    assert "change 1 of 2" in feedback and "empty" in feedback
    assert result["can_apply"] is False
    assert result.get("instructions") == []
    assert "Step one." in sandbox.read_skill("demo")


def test_an_empty_new_value_is_refused_before_the_gate(sandbox, tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "evaluate_and_record",
                        lambda *a, **k: pytest.fail("an unusable proposal must not reach the gate"))
    p = _proposal(ProposedChange("body", "Step one.", ""),
                  ProposedChange("body", "Step two.", "Step two, clarified."))

    result = _apply(p, sandbox, tmp_path)

    assert result["can_apply"] is False
    assert "empty" in result["reason"]


def test_an_ambiguous_anchor_in_any_change_is_refused(sandbox, tmp_path):
    p = _proposal(ProposedChange("body", "Shared line.", "First only."),
                  ProposedChange("body", "Step two.", "Step two, clarified."))

    passed, feedback = _gate_feedback(p)
    result = _apply(p, sandbox, tmp_path)

    assert passed is False
    assert "change 1 of 2" in feedback and "ambiguous" in feedback
    assert result["can_apply"] is False
    assert sandbox.read_skill("demo") == BODY


def test_a_stale_anchor_in_an_earlier_change_is_named(sandbox):
    p = _proposal(ProposedChange("body", "No such line.", "x"),
                  ProposedChange("body", "Step two.", "Step two, clarified."))

    passed, feedback = _gate_feedback(p)

    assert passed is False
    assert "change 1 of 2" in feedback and "stale" in feedback


def test_changes_compose_in_order(sandbox, tmp_path):
    """Change 2's anchor only exists once change 1 has been applied."""
    p = _proposal(ProposedChange("body", "Step one.", "Step one, then check."),
                  ProposedChange("body", "then check.", "then recheck."))

    passed, _ = _gate_feedback(p)
    candidate = evaluate._build_candidate(p)
    result = _apply(p, sandbox, tmp_path)

    assert passed is True
    assert "Step one, then recheck." in candidate.content
    assert result["can_apply"] is True
    if sandbox.name == "claude_code":
        assert sandbox.read_skill("demo") == candidate.content
    else:
        assert [i["action"] for i in result["instructions"]] == ["patch", "patch"]


def test_a_description_change_is_folded_into_a_body_candidate(sandbox):
    p = _proposal(ProposedChange("body", "Step two.", "Step two, clarified."),
                  ProposedChange("description", "d", "a sharper description"))

    candidate = evaluate._build_candidate(p)

    assert candidate.error is None
    assert "description: a sharper description" in candidate.content
    assert "Step two, clarified." in candidate.content


def test_several_body_changes_without_the_installed_skill_fail_closed(sandbox):
    p = _proposal(ProposedChange("body", "Step one.", "a"), ProposedChange("body", "Step two.", "b"))
    p.target_skill = "not-installed"

    passed, feedback = _gate_feedback(p)

    assert passed is False
    assert "cannot be verified without the installed skill" in feedback


def test_a_single_change_on_a_missing_skill_keeps_the_old_fallback(sandbox):
    p = _proposal(ProposedChange("body", "Step two.", "Step two, clarified."))
    p.target_skill = "not-installed"

    candidate = evaluate._build_candidate(p)

    assert candidate.error is None
    assert candidate.baseline_on_disk is False


def test_recorded_sizes_match_the_composed_candidate(sandbox):
    p = _proposal(ProposedChange("body", "Step one.", "Step one, longer."),
                  ProposedChange("body", "Step two.", "Step two, longer."))

    evaluate.evaluate_and_record(p, auto_prune=False)
    (entry,) = evaluate.read_history("skill:demo")
    candidate = evaluate._build_candidate(p)

    assert entry["content_size"] == len(candidate.content.encode("utf-8"))
    assert entry["baseline_size"] == len(BODY.encode("utf-8"))


@pytest.mark.parametrize("changes, message", [
    ([ProposedChange("description", None, "a"), ProposedChange("description", None, "b")],
     "2 'description' changes"),
    ([ProposedChange("body", None, "whole body"), ProposedChange("body", "Step two.", "b")],
     "must be the proposal's only body change"),
])
def test_structurally_unusable_change_sets_are_malformed(sandbox, changes, message):
    passed, feedback = _gate_feedback(_proposal(*changes))

    assert passed is False
    assert message in feedback


def test_diagnostics_catch_a_stale_anchor_in_an_earlier_change(sandbox, tmp_path):
    directory = tmp_path / "queue"
    proposal_module.save_proposal(
        _proposal(ProposedChange("body", "No such line.", "x"),
                  ProposedChange("body", "Step two.", "Step two, clarified.")),
        directory=str(directory))

    (diag,) = proposal_diagnostics.diagnose_queue(str(directory))

    assert diag["state"] == "stale"
    assert "change 1 of 2" in diag["reasons"][0]["detail"]


def test_claude_code_apply_refuses_an_ambiguous_anchor_on_its_own(tmp_path, monkeypatch):
    """Defence in depth: the adapter no longer replaces "the first of several"."""
    box = build_host("claude_code", tmp_path, monkeypatch)
    box.write_skill("demo", BODY)

    result = box.adapter.apply_skill_write({
        "type": "improve_existing", "target_skill": "demo", "proposal_id": "x",
        "changes": [{"field": "body", "old_value": "Shared line.", "new_value": "Once."}],
    })

    assert result["can_apply"] is False
    assert box.read_skill("demo") == BODY
