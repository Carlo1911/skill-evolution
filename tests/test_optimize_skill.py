"""Tests for scripts/optimize_skill.py's optional GEPA-lite optimizer (U11)."""

import functools
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import fetch_sessions
import optimize_skill
import proposal as proposal_module
import skill_index
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


# ── U3: run_gepa_optimization() ─────────────────────────────────────────
#
# These tests exercise the NEW core GEPA optimization run (U3), not the
# placeholder dspy-based optimizer above. The real `gepa` package is not
# installed in this environment, so every test stubs `optimize_skill._require_gepa`
# to return a small fake module whose GEPAConfig/EngineConfig/ReflectionConfig
# just record their kwargs and whose optimize_anything() returns a canned,
# GEPAResult-shaped object (best_candidate/val_aggregate_scores/total_metric_calls/best_idx).


def _make_fake_skill(tmp_path, name="test-skill", body="Seed body text describing the skill."):
    skill_dir = tmp_path / "skills" / "cat" / name
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"---\nname: {name}\ndescription: a test skill\n---\n\n{body}\n")
    return str(skill_md)


def _make_sessions(n):
    return [{"session_id": f"s{i}", "messages": []} for i in range(n)]


class _FakeGEPAConfig:
    def __init__(self, engine=None, reflection=None, **kwargs):
        self.engine = engine
        self.reflection = reflection


class _FakeEngineConfig:
    def __init__(self, max_metric_calls=None, **kwargs):
        self.max_metric_calls = max_metric_calls


class _FakeReflectionConfig:
    def __init__(self, reflection_lm=None, **kwargs):
        self.reflection_lm = reflection_lm


def _make_fake_gepa(result=None, capture=None):
    """A minimal stand-in for the `gepa` module surface run_gepa_optimization() uses."""
    fake = types.SimpleNamespace()
    fake.GEPAConfig = _FakeGEPAConfig
    fake.EngineConfig = _FakeEngineConfig
    fake.ReflectionConfig = _FakeReflectionConfig

    def fake_optimize_anything(seed_candidate=None, evaluator=None, objective=None, config=None, **kwargs):
        if capture is not None:
            capture["seed_candidate"] = seed_candidate
            capture["evaluator"] = evaluator
            capture["objective"] = objective
            capture["config"] = config
        if result is not None:
            return result
        return types.SimpleNamespace(
            best_candidate="improved body",
            val_aggregate_scores=[0.5, 0.8],
            total_metric_calls=12,
            best_idx=1,
        )

    fake.optimize_anything = fake_optimize_anything
    return fake


def test_run_gepa_optimization_happy_path(tmp_path, monkeypatch):
    skill_md_path = _make_fake_skill(tmp_path, name="my-skill")
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "my-skill", "category": "cat", "description": "test", "path": skill_md_path, "size": 10},
    ])
    sessions = _make_sessions(5)
    monkeypatch.setattr(fetch_sessions, "sessions_for_skill", lambda *a, **kw: sessions)

    capture = {}
    fake_gepa = _make_fake_gepa(capture=capture)
    monkeypatch.setattr(optimize_skill, "_require_gepa", lambda: fake_gepa)

    result = optimize_skill.run_gepa_optimization("my-skill")

    assert result.best_candidate == "improved body"
    assert result.val_aggregate_scores == [0.5, 0.8]
    assert result.total_metric_calls == 12

    assert "Seed body text describing the skill." in capture["seed_candidate"]
    assert isinstance(capture["evaluator"], functools.partial)
    assert capture["evaluator"].func is optimize_skill.score_candidate
    assert capture["evaluator"].keywords["sessions"] == sessions
    assert capture["config"].engine.max_metric_calls == optimize_skill.DEFAULT_MAX_METRIC_CALLS
    assert callable(capture["config"].reflection.reflection_lm)
    assert isinstance(capture["objective"], str) and capture["objective"]


def test_not_enough_history_skips_gepa_entirely(tmp_path, monkeypatch):
    skill_md_path = _make_fake_skill(tmp_path, name="new-skill")
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "new-skill", "category": "cat", "description": "", "path": skill_md_path, "size": 5},
    ])
    monkeypatch.setattr(fetch_sessions, "sessions_for_skill", lambda *a, **kw: _make_sessions(1))

    def fail_if_called():
        raise AssertionError("_require_gepa must not be called when there is not enough session history")
    monkeypatch.setattr(optimize_skill, "_require_gepa", fail_if_called)

    result = optimize_skill.run_gepa_optimization("new-skill")

    assert result.status == "not_enough_history"
    assert result.skill_name == "new-skill"
    assert result.session_count == 1


def test_zero_sessions_also_skips_gepa(tmp_path, monkeypatch):
    skill_md_path = _make_fake_skill(tmp_path, name="brand-new-skill")
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "brand-new-skill", "category": "cat", "description": "", "path": skill_md_path, "size": 5},
    ])
    monkeypatch.setattr(fetch_sessions, "sessions_for_skill", lambda *a, **kw: [])

    def fail_if_called():
        raise AssertionError("_require_gepa must not be called with zero sessions")
    monkeypatch.setattr(optimize_skill, "_require_gepa", fail_if_called)

    result = optimize_skill.run_gepa_optimization("brand-new-skill")

    assert result.status == "not_enough_history"
    assert result.session_count == 0


def test_run_gepa_optimization_respects_iteration_budget(tmp_path, monkeypatch):
    skill_md_path = _make_fake_skill(tmp_path, name="budget-skill")
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "budget-skill", "category": "cat", "description": "", "path": skill_md_path, "size": 5},
    ])
    monkeypatch.setattr(fetch_sessions, "sessions_for_skill", lambda *a, **kw: _make_sessions(5))

    capture = {}
    fake_result = types.SimpleNamespace(
        best_candidate="budget-capped candidate",
        val_aggregate_scores=[0.4, 0.6, 0.6],
        total_metric_calls=5,
        best_idx=2,
    )
    fake_gepa = _make_fake_gepa(result=fake_result, capture=capture)
    monkeypatch.setattr(optimize_skill, "_require_gepa", lambda: fake_gepa)

    result = optimize_skill.run_gepa_optimization("budget-skill", iterations=5)

    assert result.total_metric_calls == 5
    assert result.best_candidate == "budget-capped candidate"
    assert capture["config"].engine.max_metric_calls == 5


def test_reflection_lm_routes_through_call_provider(tmp_path, monkeypatch):
    skill_md_path = _make_fake_skill(tmp_path, name="wired-skill")
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "wired-skill", "category": "cat", "description": "", "path": skill_md_path, "size": 5},
    ])
    monkeypatch.setattr(fetch_sessions, "sessions_for_skill", lambda *a, **kw: _make_sessions(5))

    captured_calls = []

    def fake_call_provider(prompt, provider=None, evaluator_name=None, timeout=60):
        captured_calls.append((prompt, evaluator_name))
        return "reflected text"

    monkeypatch.setattr(evaluate, "call_provider", fake_call_provider)

    capture = {}
    fake_gepa = _make_fake_gepa(capture=capture)
    monkeypatch.setattr(optimize_skill, "_require_gepa", lambda: fake_gepa)

    optimize_skill.run_gepa_optimization("wired-skill")

    reflection_lm = capture["config"].reflection.reflection_lm
    output = reflection_lm("propose a better version")

    assert output == "reflected text"
    assert captured_calls[0][0] == "propose a better version"
    assert captured_calls[0][1] == "gepa_reflection"


def test_unknown_skill_raises_value_error(monkeypatch):
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [])
    with pytest.raises(ValueError, match="No installed skill"):
        optimize_skill.run_gepa_optimization("does-not-exist")


def test_require_gepa_missing_dependency_gives_clear_actionable_message(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gepa":
            raise ImportError("No module named 'gepa'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match=r"pip install -e '\.\[optimizer\]'"):
        optimize_skill._require_gepa()


def test_require_dspy_has_been_removed():
    """_require_dspy() is dead code now that U6 dropped the dspy extra -- U3 deletes it."""
    assert not hasattr(optimize_skill, "_require_dspy")
