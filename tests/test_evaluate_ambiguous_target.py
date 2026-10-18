"""A proposal whose target skill name is declared by two different installed files must
fail the gate rather than fall back to the analyzer's own old_value.

read_skill_body() returns None for an ambiguous name, and _resolve_baseline() used to
treat that like "not found" and measure the size ratchet, the percentage guards and the
anchor check against the proposal's own claimed text. Nothing on the gate path said why.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import host
import proposal as proposal_module
import skill_quality
from proposal import ProposalStatus, ProposalType, ProposedChange, SkillEvolutionProposal

BODY = "---\nname: github\ndescription: d\n---\n\n# GitHub\n\nOld step.\n"


class _FilesHost(host.HostAdapter):
    supports_write = True

    def __init__(self, records):
        self.records = records

    def iter_sessions(self, since=None):
        return []

    def iter_skills(self, extra_roots=None):
        return list(self.records)

    def apply_skill_write(self, plan):
        raise AssertionError("an ambiguous target must never reach the write")


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return {"name": "github", "category": "user", "description": "", "path": str(path), "size": len(text)}


@pytest.fixture
def ambiguous_host(tmp_path, monkeypatch):
    records = [_write(tmp_path / "github" / "SKILL.md", BODY),
               _write(tmp_path / "software-development" / "github" / "SKILL.md", BODY + "\nMore.\n")]
    monkeypatch.setitem(host.HOST_ADAPTERS, "files", _FilesHost(records))
    monkeypatch.setenv(host.HOST_ENV_VAR, "files")
    return records


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("SKILL_EVOLUTION_GATE_"):
            monkeypatch.delenv(key, raising=False)
    path = tmp_path / "eval_history.jsonl"
    monkeypatch.setattr(evaluate, "get_history_path", lambda: str(path))
    monkeypatch.setattr(proposal_module, "save_proposal", lambda p, directory=None: "noop")

    class _MustNotRun(evaluate.Evaluator):
        name = "must_not_run"

        def evaluate(self, content, context=None):
            raise AssertionError("evaluator ran")

    monkeypatch.setattr(evaluate, "REGISTRY", {"must_not_run": _MustNotRun})
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "must_not_run")
    return path


def _proposal(field="body", old="Old step.", new="New step."):
    return SkillEvolutionProposal(
        proposal_id="p-1", type=ProposalType.IMPROVE_EXISTING, target_skill="github",
        confidence=0.9, summary="s", rationale="r",
        proposed_changes=[ProposedChange(field=field, old_value=old, new_value=new)],
    )


def test_body_change_on_ambiguous_target_fails_closed_naming_both_files(ambiguous_host):
    (result,) = evaluate.evaluate_skill_text(_proposal())

    assert result.passed is False
    assert result.evaluator_name == "structure"
    assert "ambiguous" in result.feedback
    for record in ambiguous_host:
        assert record["path"] in result.feedback


def test_description_change_on_ambiguous_target_fails_closed(ambiguous_host):
    (result,) = evaluate.evaluate_skill_text(_proposal(field="description", old="d", new="better"))

    assert result.passed is False
    assert result.evaluator_name == "structure"
    assert "ambiguous" in result.feedback


def test_apply_proposal_refuses_and_leaves_status(ambiguous_host):
    p = _proposal()

    result = proposal_module.apply_proposal(p, min_confidence=0.5)

    assert result["can_apply"] is False
    assert p.status == ProposalStatus.PROPOSED
    structure = [r for r in result["evaluation_results"] if r["evaluator_name"] == "structure"]
    assert structure and "ambiguous" in structure[0]["feedback"]


def test_ambiguous_target_records_no_sizes(ambiguous_host, isolated):
    evaluate.evaluate_and_record(_proposal(), gate_targets=["skill"])

    (entry,) = [json.loads(line) for line in isolated.read_text().splitlines()]
    assert entry["passed"] is False
    assert "content_size" not in entry and "baseline_size" not in entry


def test_symlinked_twin_is_one_skill_not_ambiguous(tmp_path, monkeypatch):
    record = _write(tmp_path / "store" / "github" / "SKILL.md", BODY)
    link = tmp_path / "skills" / "github"
    link.parent.mkdir(parents=True)
    os.symlink(tmp_path / "store" / "github", link)
    twin = {**record, "path": str(link / "SKILL.md")}
    monkeypatch.setitem(host.HOST_ADAPTERS, "files", _FilesHost([record, twin]))
    monkeypatch.setenv(host.HOST_ENV_VAR, "files")

    assert evaluate.ambiguous_skill_paths("github") == []


def test_skill_quality_names_the_ambiguity(ambiguous_host, capsys):
    assert skill_quality.evaluate_skill_quality("github") is None

    err = capsys.readouterr().err
    assert "ambiguous" in err
    assert "not found" not in err
