"""Tests for scripts/proposal_diagnostics.py: the read-only proposal-queue diagnostic.

Each pending proposal gets one primary state (by precedence) plus every reason that
applies, and possible-duplicate flags on top. Nothing is ever written: the persisted
`status` stays the reviewer's decision.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import host
import proposal as proposal_module
import proposal_diagnostics
from proposal import ProposalStatus, ProposalType, ProposedChange, SkillEvolutionProposal

LIVE_BODY = "---\nname: live\ndescription: d\n---\n\n# Live\n\nUse the old step.\n"
REAL_CREATE_BODY = "---\nname: fresh\ndescription: d\n---\n\n# Fresh\n\nDo the new thing.\n"


class _TreeHost(host.HostAdapter):
    """Live and archived skills as real files, like an installed tree."""

    def __init__(self, root, live, archived):
        self.live = self._write(root / "live", live)
        self.archived = self._write(root / "archive", archived)

    @staticmethod
    def _write(base, skills):
        records = []
        for i, (name, body) in enumerate(skills):
            path = base / f"{name}-{i}" / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(body)
            records.append({"name": name, "category": "user", "description": "",
                            "path": str(path), "size": len(body)})
        return records

    def iter_sessions(self, since=None):
        return []

    def iter_skills(self, extra_roots=None):
        return list(self.live)

    def iter_archived_skills(self):
        return list(self.archived)


@pytest.fixture
def queue(tmp_path, monkeypatch):
    """A proposals dir plus a host with: `live` (one copy), `twin` (two distinct files),
    `retired` (archived only)."""
    adapter = _TreeHost(tmp_path / "skills",
                        live=[("live", LIVE_BODY), ("twin", "one"), ("twin", "two")],
                        archived=[("retired", "old")])
    monkeypatch.setitem(host.HOST_ADAPTERS, "tree", adapter)
    monkeypatch.setenv(host.HOST_ENV_VAR, "tree")
    directory = tmp_path / "proposals"
    directory.mkdir()
    return directory


def _save(directory, pid, *, type=ProposalType.IMPROVE_EXISTING, target="live",
          changes=None, status=ProposalStatus.PROPOSED):
    p = SkillEvolutionProposal(
        proposal_id=pid, type=type, target_skill=target, confidence=0.9,
        summary=f"summary {pid}", rationale="r",
        proposed_changes=changes if changes is not None else [
            ProposedChange(field="body", old_value="Use the old step.", new_value="Use the new step.")],
        status=status,
    )
    proposal_module.save_proposal(p, directory=str(directory))
    return p


def _by_id(directory, **kwargs):
    return {d["proposal_id"]: d for d in proposal_diagnostics.diagnose_queue(str(directory), **kwargs)}


def _reasons(diag):
    return [r["code"] for r in diag["reasons"]]


def test_valid_proposal(queue):
    _save(queue, "ok")

    diag = _by_id(queue)["ok"]

    assert diag["state"] == "valid"
    assert diag["reasons"] == []
    assert diag["possible_duplicates"] == []


def test_unreadable_files_are_reported_and_the_rest_still_classified(queue):
    (queue / "broken.md").write_text("no frontmatter here")
    (queue / "bad-status.md").write_text(
        "---\nproposal_id: bad-status\ntype: improve_existing\nstatus: already_covered\n"
        "confidence: 0.9\n---\n\nbody\n")
    _save(queue, "ok")

    diags = proposal_diagnostics.diagnose_queue(str(queue))

    unreadable = {d["file"]: d for d in diags if d["state"] == "unreadable"}
    assert set(unreadable) == {"broken.md", "bad-status.md"}
    assert all(d["reasons"][0]["detail"] for d in unreadable.values())
    assert any(d["proposal_id"] == "ok" and d["state"] == "valid" for d in diags)


def test_empty_new_value_is_incomplete(queue):
    _save(queue, "empty", changes=[ProposedChange(field="body", old_value="x", new_value="")])

    assert _by_id(queue)["empty"]["state"] == "incomplete"


def test_non_skill_field_is_incomplete(queue):
    _save(queue, "script", changes=[ProposedChange(field="scripts/evaluate.py", new_value="code")])

    diag = _by_id(queue)["script"]

    assert diag["state"] == "incomplete"
    assert "scripts/evaluate.py" in diag["reasons"][0]["detail"]


def test_placeholder_create_new_is_incomplete_with_the_write_plan_reason(queue):
    _save(queue, "stub", type=ProposalType.CREATE_NEW, target=None, changes=[
        ProposedChange(field="name", new_value="fresh"),
        ProposedChange(field="body", new_value="---\nname: fresh\n---\nSee proposal body for full draft content"),
    ])

    diag = _by_id(queue)["stub"]

    assert diag["state"] == "incomplete"
    assert "placeholder" in diag["reasons"][0]["detail"]


def test_archived_only_target_is_archived(queue):
    _save(queue, "old", target="retired")

    assert _by_id(queue)["old"]["state"] == "archived"


def test_target_nowhere_is_unresolved_not_nonexistent(queue):
    _save(queue, "ghost", target="ghost")

    assert _by_id(queue)["ghost"]["state"] == "unresolved"


def test_target_with_two_distinct_live_files_is_ambiguous(queue):
    _save(queue, "amb", target="twin")

    assert _by_id(queue)["amb"]["state"] == "ambiguous"


def test_anchor_missing_from_installed_body_is_stale(queue):
    _save(queue, "stale", changes=[ProposedChange(field="body", old_value="Gone text.", new_value="New.")])

    diag = _by_id(queue)["stale"]

    assert diag["state"] == "stale"
    assert "anchor stale" in diag["reasons"][0]["detail"]


def test_anchor_present_twice_is_stale_with_the_ambiguity_reason(queue, monkeypatch):
    adapter = host.HOST_ADAPTERS["tree"]
    with open(adapter.live[0]["path"], "a") as f:
        f.write("Use the old step.\n")
    _save(queue, "twice")

    diag = _by_id(queue)["twice"]

    assert diag["state"] == "stale"
    assert "anchor ambiguous" in diag["reasons"][0]["detail"]


def test_incomplete_on_an_archived_target_lists_both_reasons(queue):
    _save(queue, "both", target="retired",
          changes=[ProposedChange(field="body", old_value="x", new_value="")])

    diag = _by_id(queue)["both"]

    assert diag["state"] == "incomplete"
    assert _reasons(diag) == ["incomplete", "archived"]


def test_create_new_with_a_free_name_is_valid(queue):
    _save(queue, "new", type=ProposalType.CREATE_NEW, target=None, changes=[
        ProposedChange(field="name", new_value="fresh"),
        ProposedChange(field="body", new_value=REAL_CREATE_BODY),
    ])

    diag = _by_id(queue)["new"]

    assert diag["state"] == "valid"
    assert diag["possible_duplicates"] == []


def test_create_new_reusing_a_live_name_is_flagged(queue):
    _save(queue, "clash", type=ProposalType.CREATE_NEW, target=None, changes=[
        ProposedChange(field="name", new_value="live"),
        ProposedChange(field="body", new_value=REAL_CREATE_BODY),
    ])

    diag = _by_id(queue)["clash"]

    assert diag["state"] == "valid"
    assert any("already exists" in d["detail"] for d in diag["possible_duplicates"])


def test_identical_pending_changes_flag_each_other(queue):
    _save(queue, "a")
    _save(queue, "b")

    diags = _by_id(queue)

    assert diags["a"]["state"] == diags["b"]["state"] == "valid"
    assert [d["other"] for d in diags["a"]["possible_duplicates"]] == ["b"]
    assert [d["other"] for d in diags["b"]["possible_duplicates"]] == ["a"]


def test_shared_anchor_with_an_applied_proposal_is_flagged(queue):
    _save(queue, "done", status=ProposalStatus.APPLIED,
          changes=[ProposedChange(field="body", old_value="Use the old step.", new_value="Other text.")])
    _save(queue, "pending")

    diags = _by_id(queue)

    assert "done" not in diags  # applied proposals are reference, not classified
    assert [d["other"] for d in diags["pending"]["possible_duplicates"]] == ["done"]


def test_new_value_already_in_installed_body_is_flagged(queue):
    _save(queue, "covered", changes=[ProposedChange(field="body", old_value="Use the old step.",
                                                    new_value="# Live")])

    diag = _by_id(queue)["covered"]

    assert any("already present" in d["detail"] for d in diag["possible_duplicates"])


def test_all_statuses_option_classifies_applied_too(queue):
    _save(queue, "done", status=ProposalStatus.APPLIED)

    assert "done" in _by_id(queue, include_all=True)


def test_nothing_is_written(queue, tmp_path):
    _save(queue, "ok")
    (queue / "broken.md").write_text("no frontmatter")
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in queue.iterdir()}

    proposal_diagnostics.diagnose_queue(str(queue))

    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in queue.iterdir()} == before


def test_cli_text_summary_and_json(queue, monkeypatch, capsys):
    _save(queue, "ok")
    _save(queue, "ghost", target="ghost")

    monkeypatch.setattr(sys, "argv", ["proposal_diagnostics.py", "--dir", str(queue)])
    proposal_diagnostics.main()
    text = capsys.readouterr().out
    assert "valid: 1" in text and "unresolved: 1" in text

    monkeypatch.setattr(sys, "argv", ["proposal_diagnostics.py", "--dir", str(queue), "--format", "json"])
    proposal_diagnostics.main()
    data = json.loads(capsys.readouterr().out)
    assert {d["proposal_id"]: d["state"] for d in data} == {"ok": "valid", "ghost": "unresolved"}


def test_works_against_the_claude_code_host(tmp_path, monkeypatch):
    home = tmp_path / "cc"
    for rel, body in {"skills/live/SKILL.md": LIVE_BODY,
                      "skills/.archive/retired/SKILL.md": "---\nname: retired\n---\nold"}.items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(body)
    monkeypatch.setenv(host.CLAUDE_CODE_HOME_ENV_VAR, str(home))
    monkeypatch.setenv(host.HOST_ENV_VAR, "claude_code")
    directory = tmp_path / "proposals"
    directory.mkdir()
    _save(directory, "ok")
    _save(directory, "old", target="retired")
    _save(directory, "ghost", target="ghost")

    states = {k: v["state"] for k, v in _by_id(directory).items()}

    assert states == {"ok": "valid", "old": "archived", "ghost": "unresolved"}
