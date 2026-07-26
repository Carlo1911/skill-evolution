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
