"""Tests for proposal.py's unreadable-file reporting and its `--show` CLI.

`load_proposal()` used to raise a bare "Invalid proposal file: <path>", so neither
`--list` nor proposal_diagnostics.py could say *why* a file was skipped, and `--show` on
such a file died with a traceback instead of the error its "not found" branch prints.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import proposal as proposal_module

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
