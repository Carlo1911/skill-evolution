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
        raise AssertionError("_require_gepa should not be called while the optimizer is disabled")

    monkeypatch.setattr(optimize_skill, "_require_gepa", fail_if_called)
    optimize_skill.main()  # must not raise
    captured = capsys.readouterr()
    assert "disabled" in captured.err.lower()


# ── U3: run_gepa_optimization() ─────────────────────────────────────────
#
# These tests exercise the core GEPA optimization run (U3). The real `gepa`
# package is not installed in this environment, so every test stubs `optimize_skill._require_gepa`
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
    assert captured_calls[0][1] == optimize_skill.GEPA_REFLECTION_EVALUATOR_NAME


def test_reflection_lm_adapter_flattens_chat_message_list_input(monkeypatch):
    """_reflection_lm_adapter's docstring documents accepting gepa's chat-message-list
    form (list[dict]), not just a plain string -- exercise that path directly."""
    captured = {}

    def fake_call_provider(prompt, provider=None, evaluator_name=None, timeout=60):
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(evaluate, "call_provider", fake_call_provider)

    result = optimize_skill._reflection_lm_adapter([
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Propose a better candidate."},
    ])

    assert result == "ok"
    assert captured["prompt"] == "You are a helpful assistant.\n\nPropose a better candidate."


def test_iterations_zero_is_respected_not_treated_as_unset(tmp_path, monkeypatch):
    """Regression: `iterations or DEFAULT` treats an explicit 0 as falsy and
    silently substitutes the default budget instead of honoring the caller's
    override."""
    skill_md_path = _make_fake_skill(tmp_path, name="zero-iter-skill")
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "zero-iter-skill", "category": "cat", "description": "", "path": skill_md_path, "size": 5},
    ])
    monkeypatch.setattr(fetch_sessions, "sessions_for_skill", lambda *a, **kw: _make_sessions(5))

    capture = {}
    fake_gepa = _make_fake_gepa(capture=capture)
    monkeypatch.setattr(optimize_skill, "_require_gepa", lambda: fake_gepa)

    optimize_skill.run_gepa_optimization("zero-iter-skill", iterations=0)

    assert capture["config"].engine.max_metric_calls == 0


def test_reflection_lm_fails_closed_on_provider_error(tmp_path, monkeypatch):
    """A transient provider failure during reflection must not raise into gepa's
    loop and abort the whole run -- it returns an empty string for that round."""
    skill_md_path = _make_fake_skill(tmp_path, name="reflection-fail-skill")
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "reflection-fail-skill", "category": "cat", "description": "", "path": skill_md_path, "size": 5},
    ])
    monkeypatch.setattr(fetch_sessions, "sessions_for_skill", lambda *a, **kw: _make_sessions(5))

    def failing_call_provider(prompt, provider=None, evaluator_name=None, timeout=60):
        raise evaluate.ProviderError("provider is down")

    monkeypatch.setattr(evaluate, "call_provider", failing_call_provider)

    capture = {}
    fake_gepa = _make_fake_gepa(capture=capture)
    monkeypatch.setattr(optimize_skill, "_require_gepa", lambda: fake_gepa)

    optimize_skill.run_gepa_optimization("reflection-fail-skill")

    reflection_lm = capture["config"].reflection.reflection_lm
    assert reflection_lm("propose a better version") == ""


def test_unknown_skill_raises_value_error(monkeypatch):
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [])
    with pytest.raises(ValueError, match="No installed skill"):
        optimize_skill.run_gepa_optimization("does-not-exist")


def test_ambiguous_skill_name_across_categories_raises_value_error(monkeypatch):
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "dup-skill", "category": "cat-a", "description": "", "path": "/a/SKILL.md", "size": 5},
        {"name": "dup-skill", "category": "cat-b", "description": "", "path": "/b/SKILL.md", "size": 5},
    ])
    with pytest.raises(ValueError, match="ambiguous"):
        optimize_skill.run_gepa_optimization("dup-skill")


def test_skill_file_deleted_between_scan_and_read_raises_clear_runtime_error(tmp_path, monkeypatch):
    """TOCTOU: the file can vanish between scan_skills() and open()."""
    missing_path = str(tmp_path / "does-not-exist" / "SKILL.md")
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "vanishing-skill", "category": "cat", "description": "", "path": missing_path, "size": 5},
    ])
    with pytest.raises(RuntimeError, match="Could not read skill file"):
        optimize_skill.run_gepa_optimization("vanishing-skill")


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


def test_run_gepa_optimization_missing_optional_dependency_gives_clear_actionable_message(tmp_path, monkeypatch):
    """run_gepa_optimization() calls _require_gepa() once enough session history is on
    record; confirm its RuntimeError propagates through unchanged."""
    skill_md_path = _make_fake_skill(tmp_path, name="my-skill")
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "my-skill", "category": "cat", "description": "test", "path": skill_md_path, "size": 10},
    ])
    monkeypatch.setattr(fetch_sessions, "sessions_for_skill", lambda *a, **kw: _make_sessions(5))

    def fake_require_gepa():
        raise RuntimeError(
            "The optional optimizer dependency 'gepa' is not installed. "
            "Install it with: pip install -e '.[optimizer]'"
        )
    monkeypatch.setattr(optimize_skill, "_require_gepa", fake_require_gepa)

    with pytest.raises(RuntimeError, match=r"pip install -e '\.\[optimizer\]'"):
        optimize_skill.run_gepa_optimization("my-skill")


# ── U4: draft_proposal_from_gepa_result() ────────────────────────────────
#
# Builds a real proposal from a completed run_gepa_optimization() result
# (a GEPAResult, or a GEPAResult-shaped fake here since `gepa` isn't
# installed), replacing the old placeholder-text drafting path for the
# new GEPA-backed flow. The old dspy-based placeholder-drafting entry points (and their
# tests) were fully removed by U5/U7 -- there is no longer any such code path in
# scripts/optimize_skill.py.


def _make_gepa_result(best_candidate="optimized skill body text",
                       val_aggregate_scores=(0.5, 0.8, 0.65),
                       best_idx=1,
                       total_metric_calls=15):
    return types.SimpleNamespace(
        best_candidate=best_candidate,
        val_aggregate_scores=list(val_aggregate_scores),
        best_idx=best_idx,
        total_metric_calls=total_metric_calls,
    )


def test_draft_proposal_from_gepa_result_uses_optimized_candidate_not_placeholder():
    result = _make_gepa_result(best_candidate="This is the real optimized candidate body.")

    drafted = optimize_skill.draft_proposal_from_gepa_result("my-skill", result)

    assert drafted.target_skill == "my-skill"
    assert drafted.type == proposal_module.ProposalType.IMPROVE_EXISTING
    assert len(drafted.proposed_changes) == 1
    assert drafted.proposed_changes[0].field == "body"
    assert drafted.proposed_changes[0].new_value == "This is the real optimized candidate body."
    # Must not be the old placeholder shape.
    assert "Address this feedback and revise" not in drafted.proposed_changes[0].new_value


def test_draft_proposal_from_gepa_result_omits_call_count_when_absent():
    """total_metric_calls is optional on a GEPAResult-shaped object; the rationale
    must still render correctly (no crash, no dangling sentence) when it's None."""
    result = _make_gepa_result(total_metric_calls=None)

    drafted = optimize_skill.draft_proposal_from_gepa_result("my-skill", result)

    assert "Total metric calls used" not in drafted.rationale


def test_draft_proposal_from_gepa_result_rationale_reports_winner_and_runner_up():
    """Covers R9: rationale names candidate count and distinguishes winner from runner-up."""
    result = _make_gepa_result(val_aggregate_scores=[0.5, 0.8, 0.65], best_idx=1)

    drafted = optimize_skill.draft_proposal_from_gepa_result("my-skill", result)

    assert "3" in drafted.rationale  # number of candidates explored
    assert "0.8" in drafted.rationale  # winning score
    assert "0.65" in drafted.rationale  # runner-up score (second-highest, excluding winner)


def test_draft_proposal_from_gepa_result_reports_seed_to_winner_delta():
    """Covers R6: rationale reports the score delta from the seed candidate (gepa always
    places it at val_aggregate_scores[0]) to the winner, e.g. "improved from 0.72 to 0.81"."""
    result = _make_gepa_result(val_aggregate_scores=[0.72, 0.81, 0.65], best_idx=1)

    drafted = optimize_skill.draft_proposal_from_gepa_result("my-skill", result)

    assert "0.72" in drafted.rationale  # seed score
    assert "0.81" in drafted.rationale  # winner score
    assert "improved" in drafted.rationale.lower()


def test_draft_proposal_from_gepa_result_single_candidate_has_no_runner_up():
    """Edge case: only one candidate explored -- must not crash, and must note no runner-up."""
    result = _make_gepa_result(val_aggregate_scores=[0.7], best_idx=0)

    drafted = optimize_skill.draft_proposal_from_gepa_result("solo-skill", result)

    assert "0.7" in drafted.rationale
    assert "runner-up" in drafted.rationale.lower()
    assert "no runner-up" in drafted.rationale.lower() or "no-runner-up" in drafted.rationale.lower()


def test_draft_proposal_from_gepa_result_goes_through_same_gate_no_bypass(isolated_dirs, monkeypatch):
    """Regression, covers AE3: a GEPA-drafted proposal is gated identically to any other --
    no special-cased auto-apply path just because it came from the optimizer."""
    result = _make_gepa_result(best_candidate="candidate body", val_aggregate_scores=[0.4, 0.9], best_idx=1)
    drafted = optimize_skill.draft_proposal_from_gepa_result("weak-skill", result)
    path = proposal_module.save_proposal(drafted, directory=isolated_dirs["proposals_dir"])
    saved = proposal_module.load_proposal(path)

    monkeypatch.setattr(
        evaluate, "run_evaluators",
        lambda content, target, context=None: [
            EvalResult(score=0.1, passed=False, feedback="still bad", evaluator_name="stub"),
        ],
    )

    result_dict = proposal_module.apply_proposal(saved, min_confidence=0.0, directory=isolated_dirs["proposals_dir"])

    assert result_dict["can_apply"] is False
    assert saved.status == proposal_module.ProposalStatus.PROPOSED


def test_draft_proposal_from_gepa_result_can_apply_when_gate_passes(isolated_dirs, monkeypatch):
    """Flip side: no bypass in either direction -- passes for the same reason any proposal would."""
    result = _make_gepa_result(best_candidate="candidate body", val_aggregate_scores=[0.4, 0.9], best_idx=1)
    drafted = optimize_skill.draft_proposal_from_gepa_result("weak-skill", result)
    path = proposal_module.save_proposal(drafted, directory=isolated_dirs["proposals_dir"])
    saved = proposal_module.load_proposal(path)

    monkeypatch.setattr(
        evaluate, "run_evaluators",
        lambda content, target, context=None: [
            EvalResult(score=1.0, passed=True, feedback="better now", evaluator_name="stub"),
        ],
    )

    result_dict = proposal_module.apply_proposal(saved, min_confidence=0.0, directory=isolated_dirs["proposals_dir"])

    assert result_dict["can_apply"] is True
    assert saved.status == proposal_module.ProposalStatus.APPLIED


def test_gepa_flow_produces_saved_proposal_for_target_skill(tmp_path, isolated_dirs, monkeypatch):
    """Exercises the full pipeline end to end -- run_gepa_optimization() (stubbed `gepa`
    module, fixture session history) feeding draft_proposal_from_gepa_result() feeding
    proposal_module.save_proposal() -- and confirms the proposal written to disk has
    target_skill matching and status PROPOSED. Unlike test_skill_flag_runs_full_flow_and_prints_saved_path
    (which only checks a file was written), this loads and inspects it."""
    skill_md_path = _make_fake_skill(tmp_path, name="weak-skill")
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "weak-skill", "category": "cat", "description": "test", "path": skill_md_path, "size": 10},
    ])
    monkeypatch.setattr(fetch_sessions, "sessions_for_skill", lambda *a, **kw: _make_sessions(5))
    fake_gepa = _make_fake_gepa()
    monkeypatch.setattr(optimize_skill, "_require_gepa", lambda: fake_gepa)

    result = optimize_skill.run_gepa_optimization("weak-skill")
    drafted = optimize_skill.draft_proposal_from_gepa_result("weak-skill", result)
    path = proposal_module.save_proposal(drafted, directory=isolated_dirs["proposals_dir"])

    saved = proposal_module.load_proposal(path)
    assert saved.target_skill == "weak-skill"
    assert saved.type == proposal_module.ProposalType.IMPROVE_EXISTING
    assert saved.status == proposal_module.ProposalStatus.PROPOSED


# ── U5: CLI surface -- explicit invocation + candidates report ───────────
#
# Replaces the old auto-scan-and-run main() with --skill/--iterations
# explicit invocation and a --list-candidates read-only report. These tests
# exercise main() directly via monkeypatched sys.argv, following the same
# pattern as test_disabled_by_default_exits_early above.


def test_list_candidates_prints_targets_and_writes_no_proposal(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv(optimize_skill.OPTIMIZER_ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(sys, "argv", ["optimize_skill.py", "--list-candidates"])

    fake_targets = [
        {"target": "skill:weak-skill", "score": 0.3, "feedback": "Too verbose and unclear."},
    ]
    monkeypatch.setattr(optimize_skill, "find_low_scoring_targets", lambda: fake_targets)

    save_called = []
    monkeypatch.setattr(
        "proposal.save_proposal",
        lambda *a, **kw: save_called.append(True) or "should-not-be-called",
        raising=False,
    )

    optimize_skill.main()

    captured = capsys.readouterr()
    assert "weak-skill" in captured.out
    assert "0.3" in captured.out
    assert "Too verbose and unclear." in captured.out
    assert save_called == []


def test_list_candidates_with_no_targets_prints_clear_message(monkeypatch, capsys):
    monkeypatch.setenv(optimize_skill.OPTIMIZER_ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(sys, "argv", ["optimize_skill.py", "--list-candidates"])
    monkeypatch.setattr(optimize_skill, "find_low_scoring_targets", lambda: [])

    optimize_skill.main()

    captured = capsys.readouterr()
    assert captured.out.strip() != ""


def test_skill_flag_runs_full_flow_and_prints_saved_path(monkeypatch, capsys, isolated_dirs):
    monkeypatch.setenv(optimize_skill.OPTIMIZER_ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(sys, "argv", ["optimize_skill.py", "--skill", "my-skill", "--iterations", "7"])

    captured_args = {}

    def fake_run_gepa_optimization(skill_name, iterations=None):
        captured_args["skill_name"] = skill_name
        captured_args["iterations"] = iterations
        return types.SimpleNamespace(
            best_candidate="improved body",
            val_aggregate_scores=[0.5, 0.9],
            best_idx=1,
            total_metric_calls=12,
        )

    monkeypatch.setattr(optimize_skill, "run_gepa_optimization", fake_run_gepa_optimization)
    monkeypatch.setattr(
        proposal_module, "get_proposals_dir", lambda: isolated_dirs["proposals_dir"]
    )

    optimize_skill.main()

    captured = capsys.readouterr()
    assert captured_args == {"skill_name": "my-skill", "iterations": 7}
    assert "saved" in captured.out.lower()
    assert "12" in captured.out  # metric calls used
    assert "0.9" in captured.out  # winning score

    # Confirm a real proposal file was written to the isolated proposals dir.
    saved_files = list(os.listdir(isolated_dirs["proposals_dir"]))
    assert len(saved_files) == 1


def test_skill_flag_without_iterations_defaults_to_none(monkeypatch, capsys, isolated_dirs):
    monkeypatch.setenv(optimize_skill.OPTIMIZER_ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(sys, "argv", ["optimize_skill.py", "--skill", "my-skill"])

    captured_args = {}

    def fake_run_gepa_optimization(skill_name, iterations=None):
        captured_args["skill_name"] = skill_name
        captured_args["iterations"] = iterations
        return types.SimpleNamespace(
            best_candidate="improved body",
            val_aggregate_scores=[0.9],
            best_idx=0,
            total_metric_calls=3,
        )

    monkeypatch.setattr(optimize_skill, "run_gepa_optimization", fake_run_gepa_optimization)
    monkeypatch.setattr(
        proposal_module, "get_proposals_dir", lambda: isolated_dirs["proposals_dir"]
    )

    optimize_skill.main()

    assert captured_args == {"skill_name": "my-skill", "iterations": None}


def test_no_flags_prints_usage_message_not_auto_scan(monkeypatch, capsys):
    monkeypatch.setenv(optimize_skill.OPTIMIZER_ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(sys, "argv", ["optimize_skill.py"])

    def fail_if_called(*a, **kw):
        raise AssertionError("no auto-scan-all run should happen when neither flag is given")
    monkeypatch.setattr(optimize_skill, "find_low_scoring_targets", fail_if_called)
    monkeypatch.setattr(optimize_skill, "run_gepa_optimization", fail_if_called)

    with pytest.raises(SystemExit) as exc_info:
        optimize_skill.main()

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "--skill" in (captured.out + captured.err)
    assert "--list-candidates" in (captured.out + captured.err)


def test_skill_flag_runtime_error_prints_message_and_exits_nonzero(monkeypatch, capsys):
    """main()'s --skill path catches RuntimeError (e.g. from a missing gepa install)
    and exits 1 with the message on stderr, instead of an uncaught traceback."""
    monkeypatch.setenv(optimize_skill.OPTIMIZER_ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(sys, "argv", ["optimize_skill.py", "--skill", "some-skill"])

    def fake_run_skill(skill_name, iterations):
        raise RuntimeError("The optional optimizer dependency 'gepa' is not installed.")
    monkeypatch.setattr(optimize_skill, "_run_skill", fake_run_skill)

    with pytest.raises(SystemExit) as exc_info:
        optimize_skill.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "not installed" in captured.err


def test_skill_flag_not_enough_history_prints_message_and_no_proposal(monkeypatch, capsys, isolated_dirs):
    monkeypatch.setenv(optimize_skill.OPTIMIZER_ENABLED_ENV_VAR, "true")
    monkeypatch.setattr(sys, "argv", ["optimize_skill.py", "--skill", "new-skill"])

    monkeypatch.setattr(
        optimize_skill, "run_gepa_optimization",
        lambda skill_name, iterations=None: optimize_skill.NotEnoughHistoryResult(
            status="not_enough_history", skill_name=skill_name, session_count=1,
        ),
    )

    def fail_if_called(*a, **kw):
        raise AssertionError("draft_proposal_from_gepa_result must not be called for NotEnoughHistoryResult")
    monkeypatch.setattr(optimize_skill, "draft_proposal_from_gepa_result", fail_if_called)

    optimize_skill.main()

    captured = capsys.readouterr()
    combined = (captured.out + captured.err).lower()
    assert "not enough" in combined or "history" in combined
    assert list(os.listdir(isolated_dirs["proposals_dir"])) == []


def test_disabled_optimizer_ignores_skill_and_list_candidates_flags(monkeypatch, capsys):
    """Regression: SKILL_EVOLUTION_OPTIMIZER_ENABLED unset/false exits before touching either mode."""
    monkeypatch.setattr(sys, "argv", ["optimize_skill.py", "--skill", "anything", "--list-candidates"])

    def fail_if_called(*a, **kw):
        raise AssertionError("neither mode should run while the optimizer is disabled")
    monkeypatch.setattr(optimize_skill, "find_low_scoring_targets", fail_if_called)
    monkeypatch.setattr(optimize_skill, "run_gepa_optimization", fail_if_called)

    optimize_skill.main()

    captured = capsys.readouterr()
    assert "disabled" in captured.err.lower()
