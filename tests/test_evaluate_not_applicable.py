"""Tests for the "not applicable" evaluator outcome.

An evaluator that has nothing to compare (no context for its mode) abstains visibly:
the result is recorded and shown as `n/a`, but it counts neither in the gate decision
nor in any mean score. An evaluator that tries and fails still fails closed.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import proposal as proposal_module
import skill_quality
from evaluate import EvalResult
from proposal import ProposalType, ProposedChange, SkillEvolutionProposal


PASS = EvalResult(score=0.9, feedback="", passed=True, evaluator_name="a")
FAIL = EvalResult(score=0.1, feedback="", passed=False, evaluator_name="b")
NA = EvalResult(score=1.0, feedback="no context", passed=True, evaluator_name="c",
                not_applicable=True)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("SKILL_EVOLUTION_GATE_") or key == "SKILL_EVOLUTION_EVALUATORS":
            monkeypatch.delenv(key, raising=False)
    history_path = str(tmp_path / "eval_history.jsonl")
    monkeypatch.setattr(evaluate, "get_history_path", lambda: history_path)
    monkeypatch.setattr(proposal_module, "save_proposal", lambda p, directory=None: "noop")
    return history_path


def _fixed(name, result=None, raises=None, seen=None):
    """An ad hoc evaluator class returning `result` (or raising) and recording its context."""
    def evaluate_fn(self, content, context=None):
        if seen is not None:
            seen.append(dict(context or {}))
        if raises:
            raise raises
        return EvalResult(**{**result.__dict__, "evaluator_name": name})
    return type(name, (evaluate.Evaluator,), {"name": name, "evaluate": evaluate_fn})


def _use(monkeypatch, registry):
    monkeypatch.setattr(evaluate, "REGISTRY", registry)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", ",".join(registry))


def test_evaluator_helper_builds_a_not_applicable_result():
    class Abstainer(evaluate.Evaluator):
        name = "abstainer"

        def evaluate(self, content, context=None):
            return self._not_applicable("nothing to compare")

    result = Abstainer().evaluate("x")

    assert result.not_applicable is True
    assert result.evaluator_name == "abstainer"
    assert "nothing to compare" in result.feedback


def test_strict_gate_passes_with_one_pass_and_one_not_applicable():
    assert evaluate.combine_gate([PASS, NA], "strict") is True


def test_majority_decides_over_applicable_results_only():
    # Counted as a pass, the abstention would turn 1-of-2 into 2-of-3 and pass.
    assert evaluate.combine_gate([PASS, FAIL, NA], "majority") is False


def test_all_not_applicable_behaves_like_no_evaluators():
    assert evaluate.combine_gate([NA, NA], "strict") is True


def test_regression_new_score_excludes_not_applicable(monkeypatch):
    seen = []
    _use(monkeypatch, {
        "scorer": _fixed("scorer", EvalResult(score=0.4, feedback="", passed=True, evaluator_name="")),
        "abstainer": _fixed("abstainer", NA),
        "regression": _fixed("regression", PASS, seen=seen),
    })

    evaluate.run_evaluators("content", "skill:x", {})

    assert seen[0]["new_score"] == pytest.approx(0.4)


def test_raising_evaluator_still_fails_closed(monkeypatch):
    _use(monkeypatch, {"boom": _fixed("boom", raises=RuntimeError("backend missing"))})

    (result,) = evaluate.run_evaluators("content", "skill:x", {})

    assert result.passed is False
    assert result.not_applicable is False


def test_gate_entry_shows_n_a_and_excludes_it_from_the_score(monkeypatch, clean_env):
    monkeypatch.setattr(evaluate, "evaluate_skill_text", lambda p: [PASS, NA])
    proposal = SkillEvolutionProposal(
        proposal_id="p-1", type=ProposalType.IMPROVE_EXISTING, target_skill="s",
        confidence=0.9, summary="s", rationale="r",
        proposed_changes=[ProposedChange(field="body", new_value="body")],
    )

    evaluate.evaluate_and_record(proposal, gate_targets=["skill"])

    (entry,) = [json.loads(line) for line in open(clean_env).read().splitlines()]
    assert "skill/c=n/a" in entry["feedback"]
    assert entry["score"] == pytest.approx(0.9)
    assert "not_applicable" not in entry


def test_cli_gate_entry_excludes_not_applicable():
    combined = evaluate._cli_gate_result([PASS, NA])

    assert combined.score == pytest.approx(0.9)
    assert "c=n/a" in combined.feedback


def test_append_history_omits_the_key_for_ordinary_results(clean_env):
    evaluate.append_history("skill:x", PASS)

    (entry,) = [json.loads(line) for line in open(clean_env).read().splitlines()]
    assert "not_applicable" not in entry


def test_skill_quality_mean_and_verdict_ignore_not_applicable(monkeypatch):
    judge = EvalResult(score=0.6, feedback="ok", passed=True, evaluator_name="llm_judge")
    failing_na = EvalResult(score=0.0, feedback="n/a", passed=False,
                            evaluator_name="embedding_similarity", not_applicable=True)
    monkeypatch.setattr(evaluate, "installed_skill_body", lambda name: "# Skill\n\nBody.")
    monkeypatch.setattr(evaluate, "evaluate_skill_text", lambda p: [judge, failing_na])

    result = skill_quality.evaluate_skill_quality("s")

    assert result.current_score == pytest.approx(0.6)
    assert result.passed is True
