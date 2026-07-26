"""Malformed numeric env vars must fall back to their default, not crash the run.

Every tunable was read with a bare int()/float() over os.environ.get, across 11 call sites.
A typo therefore raised ValueError out of whichever component read it first --
`SKILL_EVOLUTION_MAX_GROWTH_PCT=abc` took down the deterministic evaluator with a stack
trace rather than a gate decision.

That was tolerable while this code was not the live pipeline. It is now: the nightly cron
job runs this repo directly, unattended, so one typo'd variable means a lost run and an
error report instead of proposals. R21's fail-closed posture argues for degrading to the
documented default and saying so, not for propagating a parse error.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import fetch_sessions
import optimize_skill

BODY = "---\nname: demo\ndescription: does a thing\n---\n\n# Demo\n\nGuidance.\n"

# (env var, module-level default, a getter that must survive a garbage value)
NUMERIC_VARS = [
    ("SKILL_EVOLUTION_MAX_SKILL_SIZE_KB", evaluate.DEFAULT_MAX_SKILL_SIZE_KB),
    ("SKILL_EVOLUTION_MAX_GROWTH_PCT", evaluate.DEFAULT_MAX_GROWTH_PCT),
    ("SKILL_EVOLUTION_MAX_SHRINK_PCT", evaluate.DEFAULT_MAX_SHRINK_PCT),
    ("SKILL_EVOLUTION_MAX_CUMULATIVE_GROWTH_PCT", evaluate.DEFAULT_MAX_CUMULATIVE_GROWTH_PCT),
    ("SKILL_EVOLUTION_MAX_CUMULATIVE_SHRINK_PCT", evaluate.DEFAULT_MAX_CUMULATIVE_SHRINK_PCT),
    ("SKILL_EVOLUTION_LLM_JUDGE_THRESHOLD", evaluate.DEFAULT_LLM_JUDGE_THRESHOLD),
]

GARBAGE = ["abc", "", "   ", "12abc", "1.2.3", "None", "--5"]


@pytest.mark.parametrize("garbage", GARBAGE)
@pytest.mark.parametrize("var,default", NUMERIC_VARS, ids=[v[0] for v in NUMERIC_VARS])
def test_evaluator_survives_malformed_numeric_env(var, default, garbage, monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    monkeypatch.setenv(var, garbage)

    # Must produce a gate decision, not raise.
    result = evaluate.DeterministicEvaluator().evaluate(
        BODY, context={"content_kind": "body",
                       "baseline_size": len(BODY.encode("utf-8")),
                       "original_size": len(BODY.encode("utf-8"))})

    assert result.passed is True, result.feedback


@pytest.mark.parametrize("garbage", GARBAGE)
def test_env_float_helper_falls_back_to_default(garbage, monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_TEST_FLOAT", garbage)
    assert evaluate.env_float("SKILL_EVOLUTION_TEST_FLOAT", 12.5) == 12.5


@pytest.mark.parametrize("garbage", GARBAGE)
def test_env_int_helper_falls_back_to_default(garbage, monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_TEST_INT", garbage)
    assert evaluate.env_int("SKILL_EVOLUTION_TEST_INT", 7) == 7


def test_env_helpers_still_read_valid_values(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_TEST_FLOAT", "33.5")
    monkeypatch.setenv("SKILL_EVOLUTION_TEST_INT", "42")
    assert evaluate.env_float("SKILL_EVOLUTION_TEST_FLOAT", 1.0) == 33.5
    assert evaluate.env_int("SKILL_EVOLUTION_TEST_INT", 1) == 42


def test_env_helpers_accept_surrounding_whitespace(monkeypatch):
    """A trailing newline from `export VAR=$(...)` should not silently reset a tunable."""
    monkeypatch.setenv("SKILL_EVOLUTION_TEST_FLOAT", " 33.5\n")
    monkeypatch.setenv("SKILL_EVOLUTION_TEST_INT", "\t42 ")
    assert evaluate.env_float("SKILL_EVOLUTION_TEST_FLOAT", 1.0) == 33.5
    assert evaluate.env_int("SKILL_EVOLUTION_TEST_INT", 1) == 42


def test_malformed_value_is_reported_not_swallowed(monkeypatch, capsys):
    """Falling back silently would hide a misconfiguration for as long as it persists."""
    monkeypatch.setenv("SKILL_EVOLUTION_TEST_FLOAT", "abc")
    evaluate.env_float("SKILL_EVOLUTION_TEST_FLOAT", 9.0)

    err = capsys.readouterr().err
    assert "SKILL_EVOLUTION_TEST_FLOAT" in err
    assert "abc" in err
    assert "9" in err  # states the default it fell back to


@pytest.mark.parametrize("garbage", ["abc", "", "1.5"])
def test_optimizer_min_sessions_survives_malformed_env(garbage, monkeypatch):
    monkeypatch.setenv(optimize_skill.MIN_SESSIONS_ENV_VAR, garbage)
    assert optimize_skill._resolve_min_sessions() == optimize_skill.MIN_SESSIONS


@pytest.mark.parametrize("garbage", ["abc", "", "2.7"])
def test_sessions_for_skill_survives_malformed_cap(garbage, monkeypatch, tmp_path):
    """The cap is read inside a DB query path; a typo must not abort the fetch.

    Uses a real empty schema rather than a missing file: sessions_for_skill() on a
    nonexistent path raises sqlite3.OperationalError (sqlite creates the file, then the
    query finds no tables), which would mask whether the env parse was the thing that
    failed.
    """
    import sqlite3

    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, "
        "title TEXT, started_at REAL);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
        "content TEXT, tool_calls TEXT, timestamp REAL);"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv(fetch_sessions.MAX_SESSIONS_FOR_SKILL_ENV_VAR, garbage)

    assert fetch_sessions.sessions_for_skill("whatever", db_path=str(db)) == []
