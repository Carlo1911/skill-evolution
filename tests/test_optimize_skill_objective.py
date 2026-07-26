"""The GEPA objective string must carry the size budget the gate will enforce.

Without it the reflection LM optimizes purely for the judge score and only discovers the
size limits after the run ends -- a real run on `money-admin-messaging` spent its whole
metric-call budget converging on a +121.8% candidate that was inadmissible from the first
byte. This is a soft defence (a prompt the model may ignore); the deterministic gate
remains the hard one. Its value is not wasting budget on candidates born dead.
"""

import os
import re
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import fetch_sessions
import optimize_skill
import skill_index


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (optimize_skill.OPTIMIZER_ENABLED_ENV_VAR,
                optimize_skill.MIN_SESSIONS_ENV_VAR,
                optimize_skill.MAX_METRIC_CALLS_ENV_VAR,
                "SKILL_EVOLUTION_MAX_GROWTH_PCT",
                "SKILL_EVOLUTION_MAX_SHRINK_PCT"):
        monkeypatch.delenv(var, raising=False)


SEED = "---\nname: demo\ndescription: a demo skill\n---\n\n# Demo\n\n" + ("guidance line.\n" * 40)


@pytest.fixture
def captured_objective(tmp_path, monkeypatch):
    """Run run_gepa_optimization() against a stubbed gepa and capture the objective."""
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(SEED)
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "demo", "category": "cat", "description": "d",
         "path": str(skill_md), "size": len(SEED)},
    ])
    monkeypatch.setattr(fetch_sessions, "sessions_for_skill",
                        lambda *a, **kw: [{"session_id": f"s{i}", "messages": []} for i in range(5)])

    captured = {}

    def fake_optimize_anything(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(best_candidate=SEED, val_aggregate_scores=[0.5],
                                     total_metric_calls=1, best_idx=0)

    fake = types.SimpleNamespace(
        optimize_anything=fake_optimize_anything,
        GEPAConfig=lambda **kw: types.SimpleNamespace(**kw),
        EngineConfig=lambda **kw: types.SimpleNamespace(**kw),
        ReflectionConfig=lambda **kw: types.SimpleNamespace(**kw),
    )
    monkeypatch.setattr(optimize_skill, "_require_gepa", lambda: fake)

    optimize_skill.run_gepa_optimization("demo")
    return captured["objective"]


def test_objective_states_the_current_body_size(captured_objective):
    assert str(len(SEED.encode("utf-8"))) in captured_objective


def test_objective_states_the_allowed_byte_range(captured_objective):
    """Derived from the same env-configurable limits the gate enforces.

    Reads the defaults from evaluate rather than hardcoding them, so retuning a limit
    doesn't silently turn this into a test of a stale number.
    """
    import evaluate

    base = len(SEED.encode("utf-8"))
    lower = int(base * (1 - evaluate.DEFAULT_MAX_SHRINK_PCT / 100))
    upper = int(base * (1 + evaluate.DEFAULT_MAX_GROWTH_PCT / 100))
    assert str(lower) in captured_objective
    assert str(upper) in captured_objective


def test_objective_tracks_configured_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_MAX_GROWTH_PCT", "50")
    monkeypatch.setenv("SKILL_EVOLUTION_MAX_SHRINK_PCT", "5")

    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(SEED)
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "demo", "category": "cat", "description": "d",
         "path": str(skill_md), "size": len(SEED)}])
    monkeypatch.setattr(fetch_sessions, "sessions_for_skill",
                        lambda *a, **kw: [{"session_id": f"s{i}", "messages": []} for i in range(5)])
    captured = {}
    fake = types.SimpleNamespace(
        optimize_anything=lambda **kw: (captured.update(kw), types.SimpleNamespace(
            best_candidate=SEED, val_aggregate_scores=[0.5], total_metric_calls=1, best_idx=0))[1],
        GEPAConfig=lambda **kw: types.SimpleNamespace(**kw),
        EngineConfig=lambda **kw: types.SimpleNamespace(**kw),
        ReflectionConfig=lambda **kw: types.SimpleNamespace(**kw))
    monkeypatch.setattr(optimize_skill, "_require_gepa", lambda: fake)

    optimize_skill.run_gepa_optimization("demo")

    base = len(SEED.encode("utf-8"))
    assert str(int(base * 1.5)) in captured["objective"]
    assert str(int(base * 0.95)) in captured["objective"]


def test_objective_asks_to_preserve_section_headings(captured_objective):
    """The observed failure mode was losing 17 of 29 headings, incl. the guardrail ones."""
    assert re.search(r"heading|section", captured_objective, re.IGNORECASE)


def test_objective_still_states_the_actual_task(captured_objective):
    assert "session" in captured_objective.lower()


# ── cumulative limits must narrow the stated budget ──────────────────────
#
# The gate enforces two reference points: per-pass against the body being replaced, and
# cumulative against where the target started (original_size_for_target). The objective
# stated only the per-pass window, so for a skill already partway toward its cumulative
# ceiling it advertised a range wider than the gate would accept -- reintroducing, in
# narrower form, the objective-vs-constraint mismatch the budget exists to remove.

def _seeded(tmp_path, monkeypatch, original_size, seed):
    """Point history at a temp file, record `original_size` for skill:demo, stub gepa."""
    import evaluate
    from evaluate import EvalResult

    history = str(tmp_path / "eval_history.jsonl")
    monkeypatch.setattr(evaluate, "get_history_path", lambda: history)
    evaluate.append_history(
        "skill:demo",
        EvalResult(score=0.9, passed=True, feedback="seed", evaluator_name="gate"),
        content_size=len(seed.encode("utf-8")), baseline_size=original_size,
    )

    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(seed)
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [
        {"name": "demo", "category": "cat", "description": "d",
         "path": str(skill_md), "size": len(seed)}])
    monkeypatch.setattr(fetch_sessions, "sessions_for_skill",
                        lambda *a, **kw: [{"session_id": f"s{i}", "messages": []} for i in range(5)])

    captured = {}
    fake = types.SimpleNamespace(
        optimize_anything=lambda **kw: (captured.update(kw), types.SimpleNamespace(
            best_candidate=seed, val_aggregate_scores=[0.5], total_metric_calls=1, best_idx=0))[1],
        GEPAConfig=lambda **kw: types.SimpleNamespace(**kw),
        EngineConfig=lambda **kw: types.SimpleNamespace(**kw),
        ReflectionConfig=lambda **kw: types.SimpleNamespace(**kw))
    monkeypatch.setattr(optimize_skill, "_require_gepa", lambda: fake)

    optimize_skill.run_gepa_optimization("demo")
    return captured["objective"]


def _stated_range(objective):
    """Pull the two byte figures out of the 'between X and Y bytes' clause."""
    m = re.search(r"between\s+(\d+)\s+and\s+(\d+)\s+bytes", objective)
    assert m, f"no byte range found in objective:\n{objective}"
    return int(m.group(1)), int(m.group(2))


def test_cumulative_ceiling_narrows_the_upper_bound(tmp_path, monkeypatch):
    """A skill that already grew has less headroom left than one pass allows."""
    import evaluate

    seed = SEED
    base = len(seed.encode("utf-8"))
    original = int(base / 1.4)          # already +40% over where it started
    lower, upper = _stated_range(_seeded(tmp_path, monkeypatch, original, seed))

    cum_ceiling = original * (1 + evaluate.DEFAULT_MAX_CUMULATIVE_GROWTH_PCT / 100)
    per_pass_ceiling = base * (1 + evaluate.DEFAULT_MAX_GROWTH_PCT / 100)

    assert cum_ceiling < per_pass_ceiling, "fixture must make the cumulative limit the binding one"
    assert upper <= int(cum_ceiling) + 1, "objective advertised more headroom than the gate allows"


def test_cumulative_floor_narrows_the_lower_bound(tmp_path, monkeypatch):
    """A skill that already shrank has less room left to cut."""
    import evaluate

    seed = SEED
    base = len(seed.encode("utf-8"))
    original = int(base / 0.75)          # already -25% below where it started
    lower, upper = _stated_range(_seeded(tmp_path, monkeypatch, original, seed))

    cum_floor = original * (1 - evaluate.DEFAULT_MAX_CUMULATIVE_SHRINK_PCT / 100)
    per_pass_floor = base * (1 - evaluate.DEFAULT_MAX_SHRINK_PCT / 100)

    assert cum_floor > per_pass_floor, "fixture must make the cumulative limit the binding one"
    assert lower >= int(cum_floor) - 1, "objective advertised more room to cut than the gate allows"


def test_no_history_leaves_the_per_pass_window_intact(captured_objective):
    """Without a recorded original there is no cumulative constraint to apply."""
    import evaluate

    base = len(SEED.encode("utf-8"))
    lower, upper = _stated_range(captured_objective)

    assert lower == int(base * (1 - evaluate.DEFAULT_MAX_SHRINK_PCT / 100))
    assert upper == int(base * (1 + evaluate.DEFAULT_MAX_GROWTH_PCT / 100))


def test_objective_says_so_when_no_room_is_left(tmp_path, monkeypatch):
    """A skill already past its cumulative ceiling has an empty admissible window.

    Telling the reflection LM to produce something 'between X and Y' where X > Y is worse
    than useless, so the objective must name the situation instead.
    """
    seed = SEED
    base = len(seed.encode("utf-8"))
    original = int(base / 3.0)           # far beyond any cumulative growth allowance

    objective = _seeded(tmp_path, monkeypatch, original, seed)

    assert re.search(r"no admissible|already (?:beyond|past|exceeds)|cannot be improved",
                     objective, re.IGNORECASE), objective
