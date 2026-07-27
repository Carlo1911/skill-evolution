"""Tests that the gate measures a body change against the installed skill, not against the
proposal's own claim about it.

`old_value` is written by the analyzer LLM -- docs/session-analyzer-prompt.md asks it to
emit `old_value: <current value>` and proposal.py persists whatever it wrote. That is
proposal-supplied input, not an observation.

It became load-bearing when the absolute size cap turned into a ratchet: the cap now asks
"is this candidate larger than what it replaces?", so an inflated `old_value` would raise
the very ceiling it is checked against. A proposal claiming a 951KB baseline could ship a
950KB body past a 15KB cap while every percentage check reported a rounding-error delta.

This is not primarily an adversarial story. The LLM is transcribing a body it read through
skill_manage inspection; a duplicated or truncated transcription is an ordinary failure.
The --retroactive path makes it worse, re-scoring proposals saved weeks ago against an
`old_value` that may no longer describe anything on disk.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import skill_index

BODY = "---\nname: demo\ndescription: does a thing\n---\n\n# Demo\n\n"


def _sized(target_bytes):
    filler = "Guidance line that carries real instruction content.\n"
    body = BODY
    while len(body.encode("utf-8")) < target_bytes:
        body += filler
    return body


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in ("SKILL_EVOLUTION_MAX_SHRINK_PCT", "SKILL_EVOLUTION_MAX_GROWTH_PCT",
                "SKILL_EVOLUTION_MAX_SKILL_SIZE_KB", "SKILL_EVOLUTION_MAX_SHRINK_BYTES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    monkeypatch.setenv("SKILL_EVOLUTION_HISTORY_PATH", str(tmp_path / "history.jsonl"))


def _install(tmp_path, monkeypatch, name, content, duplicate=False):
    """Write a real SKILL.md and point scan_skills() at it."""
    path = tmp_path / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    entries = [{"name": name, "category": "general-skills",
                "description": "d", "path": str(path), "size": len(content)}]
    if duplicate:
        entries.append(dict(entries[0], category="other-skills"))
    monkeypatch.setattr(skill_index, "scan_skills", lambda: entries)
    return path


def _proposal(new_value, old_value, target_skill="demo", field="body"):
    class _Change:
        pass

    change = _Change()
    change.field = field
    change.new_value = new_value
    change.old_value = old_value

    class _Proposal:
        proposal_id = "p1"
        summary = ""
        rationale = ""

    proposal = _Proposal()
    proposal.target_skill = target_skill
    proposal.proposed_changes = [change]
    return proposal


def test_baseline_comes_from_the_installed_skill_not_the_proposal(tmp_path, monkeypatch):
    """A candidate admissible against the claim but not against the installed file is
    judged on the installed file."""
    _install(tmp_path, monkeypatch, "demo", _sized(10000))

    # Claims a 30000B baseline, so 26000B would look like a modest -13% tightening.
    # Against the real 10000B body it is +160% growth.
    results = evaluate.evaluate_skill_text(_proposal(_sized(26000), _sized(30000)))

    assert results[0].passed is False
    assert "10" in results[0].feedback  # measured against the real body, not the claim


def test_forged_old_value_cannot_ratchet_past_the_cap(tmp_path, monkeypatch):
    """The scenario the ratchet would otherwise open: an enormous claimed baseline turns
    the absolute cap into a ceiling the proposal sets for itself."""
    _install(tmp_path, monkeypatch, "demo", _sized(9000))

    results = evaluate.evaluate_skill_text(_proposal(_sized(950_000), _sized(951_000)))

    assert results[0].passed is False
    assert "exceeds" in results[0].feedback


def test_falls_back_to_old_value_when_the_skill_is_unresolvable(monkeypatch):
    """Degrade, don't block: a lookup miss must not fail a gate decision outright."""
    monkeypatch.setattr(skill_index, "scan_skills", lambda: [])

    ok = evaluate.evaluate_skill_text(_proposal(_sized(9500), _sized(10000)))
    assert ok[0].passed is True, ok[0].feedback

    bad = evaluate.evaluate_skill_text(_proposal(_sized(3000), _sized(10000)))
    assert bad[0].passed is False


def test_ambiguous_skill_match_falls_back(tmp_path, monkeypatch):
    """Two categories carrying the same name resolve to nothing, mirroring the .archive/
    twin case skill_index already guards against."""
    _install(tmp_path, monkeypatch, "demo", _sized(9000), duplicate=True)

    results = evaluate.evaluate_skill_text(_proposal(_sized(9500), _sized(10000)))

    assert results[0].passed is True, results[0].feedback


def test_description_change_does_not_use_the_installed_file_size(tmp_path, monkeypatch):
    """The installed file is not what a description replaces. Substituting it would make
    every description edit look like a ~-98% shrink."""
    _install(tmp_path, monkeypatch, "demo", _sized(10000))

    # Near-equal lengths, so the percentage checks are satisfied and the only thing that
    # could fail this is the installed file's 10000B leaking in as the baseline -- which
    # would read as a ~-99.6% shrink.
    results = evaluate.evaluate_skill_text(
        _proposal("Guides the agent through doing a thing well.",
                  "Guides the agent through doing a thing.", field="description"))

    assert results[0].passed is True, results[0].feedback


def test_recorded_history_size_matches_the_size_that_was_judged(tmp_path, monkeypatch):
    """evaluate_and_record() must resolve the baseline the same way evaluate_skill_text()
    did, or original_size_for_target() later reads back a number no gate ever used."""
    _install(tmp_path, monkeypatch, "demo", _sized(10000))
    installed_size = len(_sized(10000).encode("utf-8"))

    evaluate.evaluate_and_record(_proposal(_sized(9500), _sized(30000)))

    entries = evaluate.read_history("skill:demo")
    assert entries[-1]["baseline_size"] == installed_size
