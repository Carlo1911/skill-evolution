"""A malformed retention value warns and keeps everything instead of breaking the run.

Reproduced on both hosts: with SKILL_EVOLUTION_STATE_RETENTION="30 days", fetch marked the
session processed, then raised while parsing the value and exited before writing any
NDJSON -- the session was never analyzed. With SKILL_EVOLUTION_HISTORY_RETENTION="30 days",
evaluate_and_record() raised after appending history, so every apply_proposal() reported
"evaluation gate raised".
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import fetch_sessions
import proposal as proposal_module
import state
from host_sandbox import HOSTS, build_host
from proposal import ProposedChange, SkillEvolutionProposal

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "fetch_sessions.py")
BAD_VALUES = ["30 days", "abc", "nan", "1.5", "d"]


@pytest.mark.parametrize("module", [state, fetch_sessions], ids=["state", "fetch_sessions"])
@pytest.mark.parametrize("raw", BAD_VALUES)
def test_state_retention_typo_warns_and_is_unbounded(module, raw, capsys):
    assert module._parse_state_retention(raw) is None
    err = capsys.readouterr().err
    assert "SKILL_EVOLUTION_STATE_RETENTION" in err and "unbounded" in err


@pytest.mark.parametrize("raw", BAD_VALUES)
def test_history_retention_typo_warns_and_is_unbounded(raw, capsys):
    assert evaluate._parse_retention(raw) is None
    assert "SKILL_EVOLUTION_HISTORY_RETENTION" in capsys.readouterr().err


@pytest.mark.parametrize("module", [state, fetch_sessions], ids=["state", "fetch_sessions"])
def test_a_typo_never_prunes_the_state_file(module, tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"hermes:old": "2020-01-01T00:00:00+00:00"}))

    module.prune_processed(retention="30 days", host="hermes", path=str(path))

    assert json.loads(path.read_text()) == {"hermes:old": "2020-01-01T00:00:00+00:00"}


@pytest.mark.parametrize("host_name", HOSTS)
def test_fetch_with_a_state_retention_typo_still_emits_its_sessions(host_name, tmp_path, monkeypatch):
    box = build_host(host_name, tmp_path, monkeypatch)
    box.add_sessions([("s1", time.time() - 600)])
    monkeypatch.setenv("SKILL_EVOLUTION_STATE_RETENTION", "30 days")

    run = subprocess.run([sys.executable, SCRIPT, "--db-path", str(box.db_path)],
                         capture_output=True, text=True, env=dict(os.environ))

    assert run.returncode == 0, run.stderr
    assert [json.loads(line)["session_id"] for line in run.stdout.splitlines()] == ["s1"]
    assert "not a valid retention" in run.stderr
    assert "s1" in box.processed()


@pytest.mark.parametrize("host_name", HOSTS)
def test_a_failing_prune_cannot_lose_marked_sessions(host_name, tmp_path, monkeypatch):
    box = build_host(host_name, tmp_path, monkeypatch)
    box.add_sessions([("s1", time.time() - 600)])

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(fetch_sessions, "prune_processed", boom)
    monkeypatch.setattr(type(box.adapter), "prune_processed", boom)

    if host_name == "hermes":
        sessions = fetch_sessions.fetch_sessions(db_path=str(box.db_path), host="hermes")
    else:
        sessions = fetch_sessions.fetch_sessions_for_host("claude_code")

    assert [s["session_id"] for s in sessions] == ["s1"]


@pytest.mark.parametrize("host_name", HOSTS)
def test_apply_with_a_history_retention_typo_still_decides(host_name, tmp_path, monkeypatch, capsys):
    box = build_host(host_name, tmp_path, monkeypatch)
    box.write_skill("demo", "---\nname: demo\ndescription: d\n---\n\n# Demo\n\nStep two.\n")
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_TARGETS", "skill")
    monkeypatch.setenv("SKILL_EVOLUTION_HISTORY_RETENTION", "30 days")
    p = SkillEvolutionProposal(proposal_id="p1", target_skill="demo", confidence=0.9, summary="s",
                               proposed_changes=[ProposedChange("body", "Step two.", "Step two!")])

    result = proposal_module.apply_proposal(p, min_confidence=0.5, directory=str(tmp_path / "q"))

    assert result["can_apply"] is True, result
    assert "SKILL_EVOLUTION_HISTORY_RETENTION" in capsys.readouterr().err
