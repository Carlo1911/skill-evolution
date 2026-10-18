"""`proposal.py --validate`: the analyzer checks the file it just wrote with the queue
diagnostic's own rules, and gets a non-zero exit for anything a reviewer could not apply."""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import proposal as proposal_module
from host_sandbox import HOSTS, build_host

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "proposal.py")
INSTALLED = "---\nname: demo\ndescription: d\n---\n\n## Steps\n\nStep one.\n" + "Kept.\n" * 30


def _proposal_text(old: str, new: str, pid="p1") -> str:
    return (f"---\nproposal_id: {pid}\ntype: improve_existing\ntarget_skill: demo\n"
            f"confidence: 0.9\nstatus: proposed\nproposed_changes:\n  - field: body\n"
            f"    old_value: |-\n      {old}\n    new_value: |-\n      {new}\n---\n\nBody.\n")


@pytest.fixture(params=HOSTS)
def queue(request, tmp_path, monkeypatch):
    box = build_host(request.param, tmp_path, monkeypatch)
    box.write_skill("demo", INSTALLED)
    directory = tmp_path / "proposals"
    directory.mkdir()
    monkeypatch.setenv("SKILL_EVOLUTION_PROPOSALS_DIR", str(directory))
    return directory


def _validate(*targets, capsys):
    saved = sys.argv
    sys.argv = ["proposal.py", "--validate", *targets]
    try:
        with pytest.raises(SystemExit) as exc:
            proposal_module.main()
    finally:
        sys.argv = saved
    return exc.value.code, capsys.readouterr().out


def test_a_valid_file_exits_zero(queue, capsys):
    path = queue / "p1.md"
    path.write_text(_proposal_text("Step one.", "Step one.\\n\\nStep two."))
    code, out = _validate(str(path), capsys=capsys)
    assert code == 0, out
    assert "p1.md: valid" in out


def test_an_empty_new_value_exits_one_with_the_reason(queue, capsys):
    path = queue / "p1.md"
    path.write_text(_proposal_text("Step one.", ""))
    code, out = _validate(str(path), capsys=capsys)
    assert code == 1
    assert "incomplete" in out and "new_value is empty" in out


def test_a_stale_anchor_exits_one(queue, capsys):
    path = queue / "p1.md"
    path.write_text(_proposal_text("Not in the skill.", "Something."))
    code, out = _validate(str(path), capsys=capsys)
    assert code == 1
    assert "p1.md: stale" in out


def test_an_unknown_id_exits_one(queue, capsys):
    code, out = _validate("nope-001", capsys=capsys)
    assert code == 1
    assert "nope-001: unreadable" in out


def test_an_id_resolves_through_the_proposals_dir(queue, capsys):
    (queue / "p1.md").write_text(_proposal_text("Step one.", "Step one, clarified."))
    code, out = _validate("p1", capsys=capsys)
    assert code == 0, out


def test_the_script_runs_standalone(tmp_path, monkeypatch):
    # claude_code only: the subprocess cannot inherit the Hermes sandbox's monkeypatched
    # skills root, and would read the real ~/.hermes/skills.
    box = build_host("claude_code", tmp_path, monkeypatch)
    box.write_skill("demo", INSTALLED)
    path = tmp_path / "p1.md"
    path.write_text(_proposal_text("Step one.", ""))
    run = subprocess.run([sys.executable, SCRIPT, "--validate", str(path)],
                         capture_output=True, text=True, env=dict(os.environ))
    assert run.returncode == 1
    assert "incomplete" in run.stdout
