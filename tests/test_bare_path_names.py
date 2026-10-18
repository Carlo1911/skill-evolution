"""A state or history path that is a bare file name resolves against the cwd.

Reproduced on both hosts: SKILL_EVOLUTION_STATE_FILE=bare.json crashed fetch with
`FileNotFoundError: ''` (os.makedirs of an empty directory part), and
SKILL_EVOLUTION_HISTORY_PATH=bare.jsonl made every apply_proposal() report
"evaluation gate raised".
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import fetch_sessions
import proposal as proposal_module
import state
from host_sandbox import HOSTS, build_host
from proposal import ProposedChange, SkillEvolutionProposal

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "fetch_sessions.py")


@pytest.mark.parametrize("module", [state, fetch_sessions], ids=["state", "fetch_sessions"])
def test_mark_and_prune_accept_a_bare_state_file_name(module, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    module.mark_processed(["s1"], host="hermes", path="bare.json")
    module.prune_processed(retention="1", keep_ids=[], host="hermes", path="bare.json")

    assert (tmp_path / "bare.json").exists()


@pytest.mark.parametrize("host_name", HOSTS)
def test_fetch_with_a_bare_state_file_name(host_name, tmp_path, monkeypatch):
    box = build_host(host_name, tmp_path, monkeypatch)
    box.add_sessions([("s1", time.time() - 600)])
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.setenv(fetch_sessions.STATE_FILE_ENV_VAR, "bare.json")

    run = subprocess.run([sys.executable, SCRIPT, "--db-path", str(box.db_path)],
                         capture_output=True, text=True, env=dict(os.environ), cwd=workdir)

    assert run.returncode == 0, run.stderr
    assert [json.loads(line)["session_id"] for line in run.stdout.splitlines()] == ["s1"]
    assert "s1" in (workdir / "bare.json").read_text()


@pytest.mark.parametrize("host_name", HOSTS)
def test_apply_with_a_bare_history_file_name(host_name, tmp_path, monkeypatch):
    box = build_host(host_name, tmp_path, monkeypatch)
    box.write_skill("demo", "---\nname: demo\ndescription: d\n---\n\n# Demo\n\nStep two.\n")
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_TARGETS", "skill")
    monkeypatch.setenv("SKILL_EVOLUTION_HISTORY_PATH", "bare.jsonl")
    monkeypatch.chdir(tmp_path)
    p = SkillEvolutionProposal(proposal_id="p1", target_skill="demo", confidence=0.9, summary="s",
                               proposed_changes=[ProposedChange("body", "Step two.", "Step two!")])

    result = proposal_module.apply_proposal(p, min_confidence=0.5, directory=str(tmp_path / "q"))

    assert result["can_apply"] is True, result
    assert (tmp_path / "bare.jsonl").exists()
