"""Tests for scripts/optimize_skill.py's optional GEPA-lite optimizer (U11)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import optimize_skill
import proposal as proposal_module
from evaluate import EvalResult


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(optimize_skill.OPTIMIZER_ENABLED_ENV_VAR, raising=False)


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    history_path = str(tmp_path / "eval_history.jsonl")
    proposals_dir = str(tmp_path / "proposals")
    os.makedirs(proposals_dir, exist_ok=True)
    monkeypatch.setattr(evaluate, "get_history_path", lambda: history_path)
    return {"history_path": history_path, "proposals_dir": proposals_dir}


def test_disabled_by_default_exits_early(capsys):
    optimize_skill.main()
    captured = capsys.readouterr()
    assert "disabled" in captured.err.lower()


def test_explicitly_false_exits_early(monkeypatch, capsys):
    monkeypatch.setenv(optimize_skill.OPTIMIZER_ENABLED_ENV_VAR, "false")
    optimize_skill.main()
    captured = capsys.readouterr()
    assert "disabled" in captured.err.lower()


def test_disabled_optimizer_never_touches_dspy_or_history(monkeypatch, capsys):
    """The rest of the pipeline is unaffected by the optional dependency's absence when disabled."""
    def fail_if_called():
        raise AssertionError("_require_dspy should not be called while the optimizer is disabled")

    monkeypatch.setattr(optimize_skill, "_require_dspy", fail_if_called)
    optimize_skill.main()  # must not raise
    captured = capsys.readouterr()
    assert "disabled" in captured.err.lower()


def test_missing_optional_dependency_gives_clear_actionable_message(monkeypatch):
    def fake_require_dspy():
        raise RuntimeError(
            "The optional optimizer dependency 'dspy' is not installed. "
            "Install it with: pip install -e '.[optimizer]'"
        )
    monkeypatch.setattr(optimize_skill, "_require_dspy", fake_require_dspy)

    with pytest.raises(RuntimeError, match=r"pip install -e '\.\[optimizer\]'"):
        optimize_skill.run_optimizer()


def test_low_scoring_target_produces_saved_proposal(isolated_dirs, monkeypatch):
    monkeypatch.setattr(optimize_skill, "_require_dspy", lambda: None)
    evaluate.append_history(
        "skill:weak-skill",
        EvalResult(score=0.3, passed=False, feedback="Too verbose and unclear.", evaluator_name="gate"),
    )

    paths = optimize_skill.run_optimizer(proposals_dir=isolated_dirs["proposals_dir"])

    assert len(paths) == 1
    saved = proposal_module.load_proposal(paths[0])
    assert saved.target_skill == "weak-skill"
    assert saved.type == proposal_module.ProposalType.IMPROVE_EXISTING
    assert saved.status == proposal_module.ProposalStatus.PROPOSED


def test_high_scoring_target_produces_no_proposal(isolated_dirs, monkeypatch):
    monkeypatch.setattr(optimize_skill, "_require_dspy", lambda: None)
    evaluate.append_history(
        "skill:strong-skill",
        EvalResult(score=0.95, passed=True, feedback="Great.", evaluator_name="gate"),
    )

    paths = optimize_skill.run_optimizer(proposals_dir=isolated_dirs["proposals_dir"])
    assert paths == []


def test_optimizer_proposal_goes_through_same_gate_no_bypass(isolated_dirs, monkeypatch):
    """Covers AE4: no special-cased auto-apply path for optimizer-originated proposals."""
    monkeypatch.setattr(optimize_skill, "_require_dspy", lambda: None)
    evaluate.append_history(
        "skill:weak-skill",
        EvalResult(score=0.3, passed=False, feedback="Too verbose and unclear.", evaluator_name="gate"),
    )
    paths = optimize_skill.run_optimizer(proposals_dir=isolated_dirs["proposals_dir"])
    saved = proposal_module.load_proposal(paths[0])

    # Force every evaluator to fail, exactly as would happen for a normal analyzer proposal.
    monkeypatch.setattr(
        evaluate, "run_evaluators",
        lambda content, target, context=None: [
            EvalResult(score=0.1, passed=False, feedback="still bad", evaluator_name="stub"),
        ],
    )

    result = proposal_module.apply_proposal(saved, min_confidence=0.0, directory=isolated_dirs["proposals_dir"])

    assert result["can_apply"] is False
    assert saved.status == proposal_module.ProposalStatus.PROPOSED


def test_optimizer_proposal_can_apply_when_gate_passes_like_any_proposal(isolated_dirs, monkeypatch):
    """The flip side of AE4: no bypass in either direction -- it passes for the same reason any proposal would."""
    monkeypatch.setattr(optimize_skill, "_require_dspy", lambda: None)
    evaluate.append_history(
        "skill:weak-skill",
        EvalResult(score=0.3, passed=False, feedback="Too verbose and unclear.", evaluator_name="gate"),
    )
    paths = optimize_skill.run_optimizer(proposals_dir=isolated_dirs["proposals_dir"])
    saved = proposal_module.load_proposal(paths[0])

    monkeypatch.setattr(
        evaluate, "run_evaluators",
        lambda content, target, context=None: [
            EvalResult(score=1.0, passed=True, feedback="better now", evaluator_name="stub"),
        ],
    )

    result = proposal_module.apply_proposal(saved, min_confidence=0.0, directory=isolated_dirs["proposals_dir"])

    assert result["can_apply"] is True
    assert saved.status == proposal_module.ProposalStatus.APPLIED
