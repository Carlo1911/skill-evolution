"""A quality pass measures the installed skill, and the optimizer lists what the judge failed.

The first full pass with a working judge (2026-10-18, 175 skills) failed 17: 8 on the
judge's score, 6 on cumulative drift from edits made outside the pipeline, 3 on regression
against a proposal's candidate score. The optimizer listed exactly the 6 drift cases and
none of the 8 judge failures, whose combined score (0.78-0.84) sat above its threshold.
"""

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import optimize_skill
import skill_quality
from evaluate import EvalResult, RegressionEvaluator
from host_sandbox import HOSTS, build_host
from proposal import ProposedChange, SkillEvolutionProposal

BODY = "---\nname: demo\ndescription: d\n---\n\n## Steps\n\nStep one.\n" + "Kept line.\n" * 60


@pytest.fixture(params=HOSTS)
def box(request, tmp_path, monkeypatch):
    sandbox = build_host(request.param, tmp_path, monkeypatch)
    sandbox.write_skill("demo", BODY)
    monkeypatch.setenv("SKILL_EVOLUTION_HISTORY_PATH", str(tmp_path / "h.jsonl"))
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic,regression")
    return sandbox


def _record_original(size):
    evaluate.append_history("skill:demo", EvalResult(0.9, "ok", True, "gate"),
                            session_ids=["s0"], content_size=size, baseline_size=size,
                            kind="skill_text", subject="candidate")


# ── cumulative drift bounds proposals, not the installed text ────────────

def test_a_quality_pass_ignores_drift_made_outside_the_pipeline(box):
    _record_original(100)  # the skill has since been edited by hand to ~700B

    results = evaluate.evaluate_skill_text(skill_quality.create_synthetic_proposal("demo", BODY))

    deterministic = [r for r in results if r.evaluator_name == "deterministic"][0]
    assert deterministic.passed, deterministic.feedback


def test_a_proposal_is_still_bounded_by_cumulative_drift(box):
    _record_original(100)
    p = SkillEvolutionProposal(proposal_id="p1", target_skill="demo", confidence=0.9,
                               summary="s", rationale="r",
                               proposed_changes=[ProposedChange("body", "Step one.", "Step one, clarified.")])

    results = evaluate.evaluate_skill_text(p)

    deterministic = [r for r in results if r.evaluator_name == "deterministic"][0]
    assert not deterministic.passed
    assert "cumulative growth" in deterministic.feedback


# ── regression compares like with like ────────────────────────────

def _regress(subject, monkeypatch, tmp_path, new_score):
    path = tmp_path / "r.jsonl"
    evaluate.append_history("skill:x", EvalResult(0.6, "q", True, "gate"), path=str(path),
                            subject="installed")
    evaluate.append_history("skill:x", EvalResult(1.0, "c", True, "gate"), path=str(path),
                            subject="candidate")
    monkeypatch.setattr(evaluate, "get_history_path", lambda: str(path))
    return RegressionEvaluator().evaluate("c", {"target": "skill:x", "new_score": new_score,
                                                "subject": subject})


def test_an_installed_measurement_regresses_only_against_installed_ones(monkeypatch, tmp_path):
    result = _regress("installed", monkeypatch, tmp_path, 0.8)
    assert result.passed and "previous 0.6" in result.feedback


def test_a_candidate_regresses_only_against_candidates(monkeypatch, tmp_path):
    result = _regress("candidate", monkeypatch, tmp_path, 0.8)
    assert not result.passed and "previous 1.0" in result.feedback


def test_the_gate_hands_regression_the_subject(box):
    seen = {}
    real = evaluate.run_evaluators

    def capture(content, target, context=None):
        seen.update(context or {})
        return real(content, target, context=context)
    with patch("evaluate.run_evaluators", side_effect=capture):
        evaluate.evaluate_skill_text(skill_quality.create_synthetic_proposal("demo", BODY))
    assert seen["subject"] == "installed"


def test_previous_score_reads_installed_measurements_only(box):
    evaluate.append_history("skill:demo", EvalResult(0.7, "q", True, "gate"), subject="installed")
    evaluate.append_history("skill:demo", EvalResult(1.0, "c", True, "gate"), subject="candidate")
    assert skill_quality.get_previous_score("demo") == 0.7


# ── the optimizer reads the judge's own score ─────────────────────────

def _entry(score, judge_score=None, target="skill:demo"):
    e = {"target": target, "timestamp": "2026-10-18T00:00:00+00:00", "score": score,
         "passed": True, "feedback": "judge feedback", "evaluator_name": "gate",
         "kind": "skill_text", "subject": "installed",
         "content_sha256": evaluate.content_digest(BODY)}
    if judge_score is not None:
        e["judge"] = {"provider": "p", "model": "m", "rubric_version": "v", "score": judge_score}
    return e


def _listed(tmp_path, entries, **kw):
    path = tmp_path / "o.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return [e["target"] for e in optimize_skill.find_low_scoring_targets(history_path=str(path), **kw)]


def test_a_judge_failure_hidden_by_the_combined_score_is_listed(box, tmp_path):
    assert _listed(tmp_path, [_entry(0.83, judge_score=0.65)]) == ["skill:demo"]


def test_a_low_combined_score_with_a_passing_judge_is_not_listed(box, tmp_path):
    assert _listed(tmp_path, [_entry(0.45, judge_score=0.9)]) == []


def test_entries_without_a_judge_score_keep_the_combined_rule(box, tmp_path):
    assert _listed(tmp_path, [_entry(0.5)]) == ["skill:demo"]
    assert _listed(tmp_path, [_entry(0.65)]) == []


def test_an_explicit_threshold_applies_to_the_judge_score(box, tmp_path):
    assert _listed(tmp_path, [_entry(0.83, judge_score=0.65)], threshold=0.6) == []


def test_the_judge_threshold_env_var_is_honoured(box, tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_LLM_JUDGE_THRESHOLD", "0.6")
    assert _listed(tmp_path, [_entry(0.83, judge_score=0.65)]) == []
