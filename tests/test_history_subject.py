"""History tells a score of the installed skill from a score of a proposal's candidate.

skill_quality.py and the gate both write `skill:<name>` entries of kind `skill_text`. On
2026-10-18 every one of the 15 targets the optimizer listed came from a gate entry -- a
proposal that failed structure checks or proposed an oversized body -- so the list said
nothing about the quality of the skills it named.
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
from evaluate import EvalResult
from host_sandbox import HOSTS, build_host
from proposal import ProposedChange, SkillEvolutionProposal

BODY = "---\nname: demo\ndescription: d\n---\n\n## Steps\n\nStep one.\n" + "Kept.\n" * 30


def _history(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture(params=HOSTS)
def box(request, tmp_path, monkeypatch):
    sandbox = build_host(request.param, tmp_path, monkeypatch)
    sandbox.write_skill("demo", BODY)
    monkeypatch.setenv("SKILL_EVOLUTION_HISTORY_PATH", str(tmp_path / "h.jsonl"))
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    monkeypatch.setenv("SKILL_EVOLUTION_GATE_TARGETS", "skill")
    return sandbox


# ── what gets written ──────────────────────────────────────────────

def test_skill_quality_records_the_installed_text(box, tmp_path):
    skill_quality.evaluate_skill_quality("demo")

    [entry] = _history(tmp_path / "h.jsonl")
    assert entry["subject"] == "installed"
    assert entry["content_sha256"] == evaluate.content_digest(BODY)


def test_the_gate_records_the_candidate_it_scored(box, tmp_path):
    p = SkillEvolutionProposal(proposal_id="p1", target_skill="demo", confidence=0.9,
                               summary="s", rationale="r", session_ids=["s1"],
                               proposed_changes=[ProposedChange("body", "Step one.", "Step one, clarified.")])
    evaluate.evaluate_and_record(p)

    [entry] = _history(tmp_path / "h.jsonl")
    assert entry["subject"] == "candidate"
    assert entry["content_sha256"] == evaluate.content_digest(
        BODY.replace("Step one.", "Step one, clarified."))


def test_a_malformed_candidate_is_still_a_candidate_but_has_no_digest(box, tmp_path):
    p = SkillEvolutionProposal(proposal_id="p1", target_skill="demo", confidence=0.9,
                               summary="s", rationale="r",
                               proposed_changes=[ProposedChange("body", "Step one.", "")])
    evaluate.evaluate_and_record(p)

    [entry] = _history(tmp_path / "h.jsonl")
    assert entry["subject"] == "candidate"
    assert "content_sha256" not in entry


# ── reading entries written before the field ─────────────────────────

@pytest.mark.parametrize("entry, expected", [
    ({"target": "skill:a", "kind": "skill_text", "session_ids": [], "content_size": 10,
      "baseline_size": 10}, "installed"),
    ({"target": "skill:a", "kind": "skill_text", "session_ids": ["s1"], "content_size": 10,
      "baseline_size": 10}, "candidate"),
    ({"target": "skill:a", "kind": "skill_text", "session_ids": [], "content_size": 12,
      "baseline_size": 10}, "candidate"),
    ({"target": "skill:a", "session_ids": [], "content_size": 10, "baseline_size": 10}, "installed"),
    ({"target": "proposal:p1", "kind": "proposal"}, None),
    ({"target": "skill:a", "kind": "skill_text", "subject": "candidate", "session_ids": [],
      "content_size": 10, "baseline_size": 10}, "candidate"),
])
def test_entry_subject_infers_legacy_entries(entry, expected):
    assert evaluate.entry_subject(entry) == expected


# ── what the optimizer lists ───────────────────────────────────────

def _entry(score, subject, digest=None, target="skill:demo", **extra):
    e = {"target": target, "timestamp": "2026-10-18T00:00:00+00:00", "score": score,
         "passed": score >= 0.6, "feedback": "judge feedback", "evaluator_name": "gate",
         "kind": "skill_text", "subject": subject, **extra}
    if digest:
        e["content_sha256"] = digest
    return e


def _write(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


def test_a_failed_candidate_does_not_list_a_well_scored_skill(box, tmp_path):
    path = tmp_path / "h.jsonl"
    _write(path, [_entry(0.9, "installed", evaluate.content_digest(BODY)), _entry(0.0, "candidate")])
    skipped = {}

    assert optimize_skill.find_low_scoring_targets(history_path=str(path), skipped=skipped) == []
    assert skipped == {"candidate_only": 0, "not_installed": 0, "changed_since": 0}


def test_a_low_installed_score_lists_the_skill(box, tmp_path):
    path = tmp_path / "h.jsonl"
    _write(path, [_entry(0.3, "installed", evaluate.content_digest(BODY)), _entry(0.9, "candidate")])

    found = optimize_skill.find_low_scoring_targets(history_path=str(path))

    assert [e["target"] for e in found] == ["skill:demo"]


def test_a_target_scored_only_as_a_candidate_is_counted_not_listed(box, tmp_path):
    path = tmp_path / "h.jsonl"
    _write(path, [_entry(0.0, "candidate")])
    skipped = {}

    assert optimize_skill.find_low_scoring_targets(history_path=str(path), skipped=skipped) == []
    assert skipped["candidate_only"] == 1


def test_a_skill_changed_since_it_was_scored_is_dropped(box, tmp_path):
    path = tmp_path / "h.jsonl"
    _write(path, [_entry(0.3, "installed", evaluate.content_digest("older text"))])
    skipped = {}

    assert optimize_skill.find_low_scoring_targets(history_path=str(path), skipped=skipped) == []
    assert skipped["changed_since"] == 1


def test_a_skill_no_longer_installed_is_dropped(box, tmp_path):
    path = tmp_path / "h.jsonl"
    _write(path, [_entry(0.3, "installed", target="skill:gone")])
    skipped = {}

    assert optimize_skill.find_low_scoring_targets(history_path=str(path), skipped=skipped) == []
    assert skipped["not_installed"] == 1


def test_an_entry_without_a_digest_is_kept(box, tmp_path):
    path = tmp_path / "h.jsonl"
    _write(path, [_entry(0.3, "installed")])

    assert len(optimize_skill.find_low_scoring_targets(history_path=str(path))) == 1


def test_other_namespaces_are_unchanged(box, tmp_path):
    path = tmp_path / "h.jsonl"
    _write(path, [{"target": "proposal:p1", "timestamp": "t", "score": 0.2, "passed": False,
                   "feedback": "weak", "evaluator_name": "gate", "kind": "proposal"}])

    found = optimize_skill.find_low_scoring_targets(history_path=str(path), target_namespace="all")

    assert [e["target"] for e in found] == ["proposal:p1"]


def test_list_candidates_reports_what_it_skipped(box, tmp_path, capsys, monkeypatch):
    path = tmp_path / "h.jsonl"
    _write(path, [_entry(0.0, "candidate")])
    monkeypatch.setenv("SKILL_EVOLUTION_OPTIMIZER_ENABLED", "true")
    monkeypatch.setattr(sys, "argv", ["optimize_skill.py", "--list-candidates"])

    optimize_skill.main()

    captured = capsys.readouterr()
    assert "No low-scoring targets found." in captured.out
    assert "1 scored only as a proposal's candidate" in captured.err
