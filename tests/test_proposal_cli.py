"""Tests for proposal.py's unreadable-file reporting, its `--show` CLI, and proposals
whose file name is not their id.

`load_proposal()` used to raise a bare "Invalid proposal file: <path>", so neither
`--list` nor proposal_diagnostics.py could say *why* a file was skipped, and `--show` on
such a file died with a traceback instead of the error its "not found" branch prints.
And since save_proposal() always wrote <proposal_id>.md, two files sharing an id meant
applying one overwrote the other.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import host
import proposal as proposal_module
from proposal import ProposalStatus, ProposedChange, SkillEvolutionProposal

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "proposal.py")

FRONTMATTER = "proposal_id: p1\ntype: improve_existing\nstatus: proposed\nconfidence: 0.9\n"


def _run_show(directory, proposal_id):
    env = dict(os.environ, SKILL_EVOLUTION_PROPOSALS_DIR=str(directory))
    return subprocess.run([sys.executable, SCRIPT, "--show", proposal_id],
                          capture_output=True, text=True, env=env)


@pytest.mark.parametrize("content, problem", [
    (FRONTMATTER + "---\n\nbody\n", "missing opening '---' frontmatter delimiter on line 1"),
    ("---\n" + FRONTMATTER + "\nbody\n", "no closing '---' line follows"),
])
def test_load_proposal_names_the_frontmatter_problem(tmp_path, content, problem):
    path = tmp_path / "p1.md"
    path.write_text(content)

    with pytest.raises(ValueError) as exc:
        proposal_module.load_proposal(str(path))

    assert problem in str(exc.value)
    assert str(path) in str(exc.value)


def test_closing_delimiter_on_the_last_line_without_newline_loads(tmp_path):
    """Live: 20260823-001.md is frontmatter only and ends in '---' with no newline."""
    path = tmp_path / "p1.md"
    path.write_text("---\n" + FRONTMATTER + "---")

    loaded = proposal_module.load_proposal(str(path))

    assert loaded.proposal_id == "p1"


def test_a_delimiter_prefix_is_not_a_closing_line(tmp_path):
    path = tmp_path / "p1.md"
    path.write_text("---\n" + FRONTMATTER + "----\n\nbody\n")

    with pytest.raises(ValueError, match="no closing '---' line follows"):
        proposal_module.load_proposal(str(path))


def test_list_proposals_warning_carries_the_reason(tmp_path, capsys):
    (tmp_path / "p1.md").write_text(FRONTMATTER + "---\n\nbody\n")

    assert proposal_module.list_proposals(str(tmp_path)) == []

    err = capsys.readouterr().err
    assert "skipping unreadable proposal p1.md" in err
    assert "missing opening '---'" in err


def test_show_unreadable_proposal_reports_without_traceback(tmp_path):
    (tmp_path / "p1.md").write_text(FRONTMATTER + "---\n\nbody\n")

    result = _run_show(tmp_path, "p1")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Proposal 'p1' is unreadable" in result.stderr
    assert "missing opening '---'" in result.stderr
    assert "Traceback" not in result.stderr


def test_show_missing_proposal_still_reports_not_found(tmp_path):
    result = _run_show(tmp_path, "nope")

    assert result.returncode == 1
    assert "Proposal 'nope' not found" in result.stderr


# ── Two files, one proposal id ──────────────────────────────────────────────

def _write(directory, fname, pid, summary="s"):
    p = SkillEvolutionProposal(proposal_id=pid, target_skill="live", confidence=0.9,
                               summary=summary, rationale="r", proposed_changes=[
                                   ProposedChange(field="body", old_value="a", new_value="b")])
    (directory / fname).write_text(p.render())


def test_show_finds_a_proposal_by_id_when_its_file_is_named_otherwise(tmp_path):
    _write(tmp_path, "p1-new.md", "p1", summary="the copy")

    result = _run_show(tmp_path, "p1")

    assert result.returncode == 0
    assert "summary: the copy" in result.stdout


def test_show_refuses_an_id_claimed_by_several_files(tmp_path):
    _write(tmp_path, "p1.md", "p1")
    _write(tmp_path, "p1-new.md", "p1")

    result = _run_show(tmp_path, "p1")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Proposal 'p1' is ambiguous" in result.stderr
    assert "p1-new.md, p1.md" in result.stderr


def test_an_unreadable_namesake_still_counts_as_a_claim(tmp_path):
    (tmp_path / "p1.md").write_text("no frontmatter")
    _write(tmp_path, "p1-new.md", "p1")

    with pytest.raises(ValueError, match="p1-new.md, p1.md"):
        proposal_module.find_proposal_file("p1", str(tmp_path))


def test_a_namesake_declaring_another_id_does_not_hold_this_one(tmp_path):
    _write(tmp_path, "p1.md", "other")

    with pytest.raises(FileNotFoundError):
        proposal_module.find_proposal_file("p1", str(tmp_path))


def test_source_path_is_never_rendered(tmp_path):
    _write(tmp_path, "p1-new.md", "p1")

    loaded = proposal_module.load_proposal(str(tmp_path / "p1-new.md"))

    assert loaded.source_path == str(tmp_path / "p1-new.md")
    assert "source_path" not in loaded.render()
    assert str(tmp_path) not in loaded.render()


def test_save_writes_a_loaded_proposal_back_to_its_own_file(tmp_path):
    _write(tmp_path, "p1.md", "p1", summary="original")
    _write(tmp_path, "p1-new.md", "p1", summary="the copy")
    loaded = proposal_module.load_proposal(str(tmp_path / "p1-new.md"))
    loaded.summary = "edited"

    path = proposal_module.save_proposal(loaded, str(tmp_path))

    assert path == str(tmp_path / "p1-new.md")
    assert "summary: edited" in (tmp_path / "p1-new.md").read_text()
    assert "summary: original" in (tmp_path / "p1.md").read_text()


def test_save_into_another_directory_still_uses_the_id(tmp_path):
    (tmp_path / "a").mkdir()
    _write(tmp_path / "a", "p1-new.md", "p1")
    loaded = proposal_module.load_proposal(str(tmp_path / "a" / "p1-new.md"))

    path = proposal_module.save_proposal(loaded, str(tmp_path / "b"))

    assert path == str(tmp_path / "b" / "p1.md")


class _AcceptingHost(host.HostAdapter):
    name = "accepting"
    supports_write = True

    def iter_sessions(self, since=None):
        return []

    def iter_skills(self, extra_roots=None):
        return []

    def read_skill_body(self, skill_name):
        return None

    def apply_skill_write(self, plan):
        return {"can_apply": True, "applied_by": "direct"}


def test_applying_a_copy_marks_the_copy_and_leaves_its_namesake_alone(tmp_path, monkeypatch):
    """The live hazard: applying 20260823-001-new.md wrote `status: applied` over
    20260823-001.md and left the copy `proposed`, i.e. applicable a second time."""
    monkeypatch.setitem(host.HOST_ADAPTERS, "accepting", _AcceptingHost())
    monkeypatch.setenv(host.HOST_ENV_VAR, "accepting")
    monkeypatch.setattr(evaluate, "evaluate_and_record", lambda proposal: ([], None, True))
    _write(tmp_path, "p1.md", "p1", summary="original")
    _write(tmp_path, "p1-new.md", "p1", summary="the copy")
    original = (tmp_path / "p1.md").read_text()
    copy = proposal_module.load_proposal(str(tmp_path / "p1-new.md"))

    result = proposal_module.apply_proposal(copy, min_confidence=0.5, directory=str(tmp_path))

    assert result["can_apply"] is True
    assert (tmp_path / "p1.md").read_text() == original
    reloaded = proposal_module.load_proposal(str(tmp_path / "p1-new.md"))
    assert reloaded.status == ProposalStatus.APPLIED
