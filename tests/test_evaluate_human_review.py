"""Tests for scripts/evaluate.py's opt-in, TTY-gated human_review evaluator (P2-4).

Covers registration/defaults, fail-closed without a TTY, the binary approve/reject
prompt loop, EOF/interrupt handling, the three-phase run_evaluators() ordering (the
human runs last and never pollutes the regression aggregate), the gate integration,
and skill_quality.py's exclusion of the evaluator.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import skill_quality
from evaluate import EvalResult
from proposal import ProposalType, ProposedChange, SkillEvolutionProposal


@pytest.fixture(autouse=True)
def clean_evaluator_env(monkeypatch):
    monkeypatch.delenv("SKILL_EVOLUTION_EVALUATORS", raising=False)
    monkeypatch.delenv("SKILL_EVOLUTION_GATE_TARGETS", raising=False)
    monkeypatch.delenv("SKILL_EVOLUTION_GATE_STRICTNESS", raising=False)


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    history_path = str(tmp_path / "eval_history.jsonl")
    monkeypatch.setattr(evaluate, "get_history_path", lambda: history_path)
    return history_path


class _StubAutomatic(evaluate.Evaluator):
    """A deterministic pass/fail evaluator with a fixed score, for ordering tests."""

    name = "stub_automatic"

    def __init__(self, score=0.5):
        self._score = score

    def evaluate(self, content, context=None):
        return EvalResult(score=self._score, passed=True, feedback="stub", evaluator_name=self.name)


def _make_tty(monkeypatch, responses, tty=True):
    """Stub stdin/stdout isatty() and builtins.input so the evaluator sees a terminal."""
    calls = {"count": 0}

    def fake_input(prompt=""):
        calls["count"] += 1
        if calls["count"] > len(responses):
            raise EOFError
        return responses[calls["count"] - 1]

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)
    return calls


def _human_evaluate(monkeypatch, responses, content="content", context=None, tty=True):
    _make_tty(monkeypatch, responses, tty=tty)
    return evaluate.HumanReviewEvaluator().evaluate(content, context or {"target": "skill:foo", "new_score": 0.5})


def _proposal():
    return SkillEvolutionProposal(
        proposal_id="fixture-001",
        type=ProposalType.IMPROVE_EXISTING,
        target_skill="test-skill",
        confidence=0.9,
        summary="Improve test-skill",
        rationale="Fixture rationale.",
        proposed_changes=[ProposedChange(field="body", new_value="Well-formed body.")],
        session_ids=[],
    )


# ── Registration / defaults ──────────────────────────────────────────

def test_human_review_registered_in_registry():
    assert "human_review" in evaluate.REGISTRY
    assert evaluate.REGISTRY["human_review"] is evaluate.HumanReviewEvaluator


def test_human_review_not_in_default_evaluators():
    assert "human_review" not in evaluate.DEFAULT_EVALUATORS
    assert [e.name for e in evaluate.get_enabled_evaluators()] == ["deterministic", "llm_judge", "regression"]


def test_human_review_resolves_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic,human_review")
    names = [e.name for e in evaluate.get_enabled_evaluators()]
    assert "human_review" in names


def test_unknown_evaluator_name_still_raises(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "human_review,bogus")
    with pytest.raises(ValueError, match="Unknown evaluator 'bogus'"):
        evaluate.get_enabled_evaluators()


# ── Fail-closed without a TTY ────────────────────────────────────────

def test_fails_closed_without_tty(monkeypatch):
    calls = _make_tty(monkeypatch, responses=[], tty=False)
    result = _human_evaluate(monkeypatch, [], tty=False)
    assert result.passed is False
    assert result.score == 0.0
    assert "interactive terminal" in result.feedback
    assert calls["count"] == 0  # no input() was ever attempted


def test_fails_closed_when_stdin_not_tty_even_if_stdout_is(monkeypatch):
    _make_tty(monkeypatch, responses=[], tty=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    result = _human_evaluate(monkeypatch, [], tty=False)
    assert result.passed is False
    assert "interactive terminal" in result.feedback


def test_fails_closed_when_new_score_missing(monkeypatch):
    _make_tty(monkeypatch, responses=["y"], tty=True)
    result = evaluate.HumanReviewEvaluator().evaluate("content", {"target": "skill:foo"})
    assert result.passed is False
    assert "'new_score' in context" in result.feedback


# ── Binary approve / reject ──────────────────────────────────────────

def test_approve_on_y(monkeypatch):
    result = _human_evaluate(monkeypatch, ["y"])
    assert result.passed is True
    assert result.score == pytest.approx(0.5)  # new_score, not 1.0
    assert "human approved" in result.feedback


def test_approve_on_yes_case_insensitive(monkeypatch):
    result = _human_evaluate(monkeypatch, ["YES"])
    assert result.passed is True
    assert "human approved" in result.feedback


def test_reject_on_n_with_reason(monkeypatch):
    result = _human_evaluate(monkeypatch, ["n", "missing edge cases"])
    assert result.passed is False
    assert result.score == pytest.approx(0.5)
    assert "human rejected" in result.feedback
    assert "missing edge cases" in result.feedback


def test_reject_on_no_without_reason(monkeypatch):
    result = _human_evaluate(monkeypatch, ["no", ""])
    assert result.passed is False
    assert "no reason given" in result.feedback


# ── Prompt loop ──────────────────────────────────────────────────────

def test_empty_response_reprompts_then_approves(monkeypatch):
    result = _human_evaluate(monkeypatch, ["", "", "yes"])
    assert result.passed is True
    assert "human approved" in result.feedback


def test_unrecognized_response_reprompts_then_rejects(monkeypatch):
    result = _human_evaluate(monkeypatch, ["maybe", "n"])
    assert result.passed is False
    assert "human rejected" in result.feedback


def test_prompt_loop_exhausted_fails_closed(monkeypatch):
    result = _human_evaluate(monkeypatch, ["x", "x", "x"])
    assert result.passed is False
    assert result.score == 0.0
    assert "prompt loop exhausted" in result.feedback


def test_eof_during_prompt_fails_closed(monkeypatch):
    result = _human_evaluate(monkeypatch, [])
    assert result.passed is False
    assert result.score == 0.0
    assert "EOFError" in result.feedback


def test_eof_during_reason_falls_back_to_blank(monkeypatch):
    result = _human_evaluate(monkeypatch, ["n"])
    assert result.passed is False
    assert "no reason given" in result.feedback


# ── Three-phase ordering in run_evaluators ───────────────────────────

def test_human_review_runs_last_and_gets_prior_results(monkeypatch, isolated_history):
    monkeypatch.setitem(evaluate.REGISTRY, "stub_automatic", _StubAutomatic)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "stub_automatic,regression,human_review")
    _make_tty(monkeypatch, ["y"], tty=True)

    results = evaluate.run_evaluators("content", "skill:foo")

    assert [r.evaluator_name for r in results] == ["stub_automatic", "regression", "human_review"]
    # regression compared against the automatic mean (0.5), untouched by the human score
    assert results[1].score == pytest.approx(0.5)
    assert results[1].passed is True
    # human saw automatic + regression verdicts before deciding
    assert results[2].passed is True
    assert results[2].score == pytest.approx(0.5)  # new_score, not inflated to 1.0


def test_human_approval_does_not_inflate_recorded_aggregate(monkeypatch, isolated_history):
    """The regression new_score must equal the automatic mean even when the human approves."""
    monkeypatch.setitem(evaluate.REGISTRY, "stub_automatic", _StubAutomatic)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "stub_automatic,human_review")
    _make_tty(monkeypatch, ["y"], tty=True)

    results = evaluate.run_evaluators("content", "skill:foo")
    human = [r for r in results if r.evaluator_name == "human_review"][0]
    assert human.score == pytest.approx(0.5)


# ── Gate integration ─────────────────────────────────────────────────

def test_human_rejection_blocks_the_gate(monkeypatch, isolated_history):
    monkeypatch.setitem(evaluate.REGISTRY, "stub_automatic", _StubAutomatic)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "stub_automatic,human_review")
    monkeypatch.setattr(evaluate, "evaluate_skill_text",
                        lambda p: evaluate.run_evaluators("content", "skill:test-skill"))
    _make_tty(monkeypatch, ["n", "rejecting for review"], tty=True)

    proposal = _proposal()
    results, combined, gate_passed = evaluate.evaluate_and_record(proposal, gate_targets=["skill"])
    assert gate_passed is False
    assert combined.passed is False
    assert "human_review=fail" in combined.feedback


def test_human_approval_passes_the_gate(monkeypatch, isolated_history):
    monkeypatch.setitem(evaluate.REGISTRY, "stub_automatic", _StubAutomatic)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "stub_automatic,human_review")
    monkeypatch.setattr(evaluate, "evaluate_skill_text",
                        lambda p: evaluate.run_evaluators("content", "skill:test-skill"))
    _make_tty(monkeypatch, ["y"], tty=True)

    proposal = _proposal()
    _, combined, gate_passed = evaluate.evaluate_and_record(proposal, gate_targets=["skill"])
    assert gate_passed is True
    assert combined.passed is True


# ── skill_quality.py exclusion guard ─────────────────────────────────

def test_skill_quality_excludes_human_review(monkeypatch, capsys):
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic,llm_judge,regression,human_review")
    skill_quality._exclude_human_review()
    names = [e.name for e in evaluate.get_enabled_evaluators()]
    assert "human_review" not in names
    assert "deterministic" in names and "regression" in names
    err = capsys.readouterr().err
    assert "human_review" in err and "non-interactive" in err


def test_skill_quality_noop_without_human_review(monkeypatch, capsys):
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic,llm_judge,regression")
    skill_quality._exclude_human_review()
    assert capsys.readouterr().err == ""
