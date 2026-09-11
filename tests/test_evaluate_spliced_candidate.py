"""Tests for scripts/evaluate.py's fragment splice (20260910-004).

The analyzer writes old_value/new_value as section fragments (patch
semantics), but the gate used to score the fragment verbatim against the
full installed body -- every fragment-style body proposal failed the
shrink floor (~-80%), including already-applied ones. _splice_candidate()
scores what apply will actually write instead.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import EvalResult, _splice_candidate, evaluate_skill_text
from proposal import ProposalType


class _FakeChange:
    def __init__(self, field, new_value=None, old_value=None):
        self.field = field
        self.new_value = new_value
        self.old_value = old_value


class _FakeProposal:
    def __init__(self, proposal_id="p1", target_skill=None, summary="", rationale="",
                 proposed_changes=None, type=ProposalType.IMPROVE_EXISTING):
        self.proposal_id = proposal_id
        self.target_skill = target_skill
        self.summary = summary
        self.rationale = rationale
        self.proposed_changes = proposed_changes or []
        self.type = type


INSTALLED = (
    "---\nname: demo\n---\n\n# Demo\n\n| row | to replace |\n\nTrailing text.\n"
)
OLD = "| row | to replace |"
NEW = "| row | REPLACED new content here |"


@pytest.fixture()
def stub_gate(monkeypatch):
    captured = {}

    def fake_run_evaluators(content, target, context=None):
        captured["content"] = content
        captured["target"] = target
        captured["context"] = context or {}
        return [EvalResult(score=1.0, passed=True, feedback="ok", evaluator_name="stub")]

    monkeypatch.setattr(evaluate, "run_evaluators", fake_run_evaluators)
    monkeypatch.setattr(evaluate, "installed_skill_body", lambda name: INSTALLED)
    return captured


def test_fragment_is_spliced_before_scoring(stub_gate):
    proposal = _FakeProposal(
        target_skill="demo",
        proposed_changes=[_FakeChange(field="body", new_value=NEW, old_value=OLD)],
    )
    results = evaluate_skill_text(proposal)

    assert results[0].passed is True
    # The gate judged the full post-apply text, not the fragment.
    assert stub_gate["content"] == INSTALLED.replace(OLD, NEW)
    assert OLD not in stub_gate["content"]
    assert stub_gate["content"].startswith("---\n")
    # Baseline is still the installed file.
    assert stub_gate["context"]["baseline_size"] == len(INSTALLED.encode("utf-8"))


def test_stale_anchor_fails_closed_without_spending_evaluators(monkeypatch):
    called = []

    def fake_run_evaluators(content, target, context=None):
        called.append(content)
        return [EvalResult(score=1.0, passed=True, feedback="ok", evaluator_name="stub")]

    monkeypatch.setattr(evaluate, "run_evaluators", fake_run_evaluators)
    monkeypatch.setattr(evaluate, "installed_skill_body", lambda name: INSTALLED)

    proposal = _FakeProposal(
        target_skill="demo",
        proposed_changes=[_FakeChange(
            field="body", new_value=NEW, old_value="| no such row anywhere |")],
    )
    results = evaluate_skill_text(proposal)

    assert called == []
    assert len(results) == 1
    assert results[0].passed is False
    assert results[0].evaluator_name == "structure"
    assert "stale" in results[0].feedback


def test_ambiguous_anchor_fails_closed(monkeypatch):
    called = []
    doubled = INSTALLED + "\n" + OLD + "\n"

    def fake_run_evaluators(content, target, context=None):
        called.append(content)
        return [EvalResult(score=1.0, passed=True, feedback="ok", evaluator_name="stub")]

    monkeypatch.setattr(evaluate, "run_evaluators", fake_run_evaluators)
    monkeypatch.setattr(evaluate, "installed_skill_body", lambda name: doubled)

    proposal = _FakeProposal(
        target_skill="demo",
        proposed_changes=[_FakeChange(field="body", new_value=NEW, old_value=OLD)],
    )
    results = evaluate_skill_text(proposal)

    assert called == []
    assert results[0].passed is False
    assert "ambiguous" in results[0].feedback
    assert "2 times" in results[0].feedback


def test_body_without_old_value_scores_verbatim(stub_gate):
    proposal = _FakeProposal(
        target_skill="demo",
        proposed_changes=[_FakeChange(field="body", new_value="# Whole new body")],
    )
    evaluate_skill_text(proposal)

    assert stub_gate["content"] == "# Whole new body"


def test_description_change_never_splices(stub_gate):
    proposal = _FakeProposal(
        target_skill="demo",
        proposed_changes=[_FakeChange(
            field="description", new_value="new desc", old_value="row")],
    )
    evaluate_skill_text(proposal)

    assert stub_gate["content"] == "new desc"


def test_unresolvable_skill_falls_back_to_verbatim(monkeypatch):
    captured = {}

    def fake_run_evaluators(content, target, context=None):
        captured["content"] = content
        captured["context"] = context or {}
        return [EvalResult(score=1.0, passed=True, feedback="ok", evaluator_name="stub")]

    monkeypatch.setattr(evaluate, "run_evaluators", fake_run_evaluators)
    monkeypatch.setattr(evaluate, "installed_skill_body", lambda name: None)

    proposal = _FakeProposal(
        target_skill="ghost-skill",
        proposed_changes=[_FakeChange(field="body", new_value=NEW, old_value=OLD)],
    )
    results = evaluate_skill_text(proposal)

    assert results[0].passed is True
    assert captured["content"] == NEW


def test_splice_unit_boundaries():
    # Direct unit coverage of the helper's fallthroughs.
    assert _splice_candidate(NEW, None, INSTALLED, "body") == (NEW, None)
    assert _splice_candidate(NEW, OLD, None, "body") == (NEW, None)
    assert _splice_candidate("d", "o", "b", "description") == ("d", None)
    candidate, error = _splice_candidate(NEW, OLD, INSTALLED, "body")
    assert error is None and candidate == INSTALLED.replace(OLD, NEW)
