"""review_labels.py: human labels with reasons, a fixed design/holdout split, never a
change to a proposal's status."""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import review_labels
from host_sandbox import build_host
from proposal import load_proposal

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "review_labels.py")
INSTALLED = "---\nname: demo\ndescription: d\n---\n\n## Steps\n\nStep one.\n" + "Kept.\n" * 30


def _proposal_text(pid, new="Step one.\\nStep two."):
    return (f"---\nproposal_id: {pid}\ntype: improve_existing\ntarget_skill: demo\n"
            f"confidence: 0.9\nstatus: proposed\nsummary: s {pid}\nproposed_changes:\n"
            f"  - field: body\n    old_value: \"Step one.\"\n    new_value: \"{new}\"\n---\n\nBody.\n")


@pytest.fixture
def queue(tmp_path, monkeypatch):
    box = build_host("claude_code", tmp_path, monkeypatch)
    box.write_skill("demo", INSTALLED)
    directory = tmp_path / "proposals"
    directory.mkdir()
    (directory / "a.md").write_text(_proposal_text("a"))
    (directory / "b.md").write_text(_proposal_text("b", new=""))  # incomplete
    monkeypatch.setenv("SKILL_EVOLUTION_PROPOSALS_DIR", str(directory))
    return directory


def _answers(*values):
    it = iter(values)
    return lambda prompt: next(it)


def test_labels_are_recorded_valid_first_and_status_untouched(queue):
    n = review_labels.label_interactively(ask=_answers("u", "clear evidence", "i", ""))

    labels = review_labels.read_labels()
    assert n == 2
    assert labels["a"]["label"] == "useful" and labels["a"]["reason"] == "clear evidence"
    assert labels["a"]["diagnostic_state"] == "valid"
    assert labels["b"]["label"] == "incomplete" and labels["b"]["diagnostic_state"] == "incomplete"
    assert load_proposal(str(queue / "a.md")).status.value == "proposed"


def test_a_reason_is_required_except_for_incomplete(queue):
    # The empty reason is asked again; "skip" moves past b.
    review_labels.label_interactively(ask=_answers("h", "", "breaks step two", "s"))
    assert review_labels.read_labels()["a"]["reason"] == "breaks step two"
    with pytest.raises(ValueError):
        review_labels.make_row({"proposal_id": "a", "file": "a.md", "state": "valid"},
                               str(queue / "a.md"), "harmful", "  ")


def test_unknown_answers_are_asked_again_and_quit_stops(queue):
    n = review_labels.label_interactively(ask=_answers("maybe", "q"))
    assert n == 0 and review_labels.read_labels() == {}


def test_labeled_proposals_are_not_offered_again_until_edited(queue):
    review_labels.label_interactively(ask=_answers("u", "ok", "i", ""))
    assert review_labels.label_interactively(ask=_answers()) == 0

    (queue / "a.md").write_text(_proposal_text("a", new="Step one.\\nStep three."))
    assert review_labels.stats()["outdated"] == 1
    assert review_labels.label_interactively(ask=_answers("d", "same as c")) == 1
    assert review_labels.read_labels()["a"]["label"] == "duplicate"


def test_split_is_fixed_per_id_and_roughly_thirty_percent_holdout():
    assert review_labels.split_for("20260828-001") == review_labels.split_for("20260828-001")
    share = sum(review_labels.split_for(f"p{i}") == "holdout" for i in range(2000)) / 2000
    assert 0.25 < share < 0.35


def test_stats_counts_labels_and_what_is_left(queue):
    review_labels.label_interactively(limit=1, ask=_answers("u", "ok"))

    s = review_labels.stats()

    assert s["labeled"] == 1 and s["pending_unlabeled"] == 1
    assert s["by_label"] == {"useful": 1}


def test_next_without_a_terminal_exits_one(queue):
    run = subprocess.run([sys.executable, SCRIPT, "--next"], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, env=dict(os.environ))
    assert run.returncode == 1
    assert "interactive terminal" in run.stderr


def test_export_prints_the_current_labels(queue):
    review_labels.label_interactively(ask=_answers("u", "ok", "i", ""))
    run = subprocess.run([sys.executable, SCRIPT, "--export"], capture_output=True, text=True,
                         env=dict(os.environ))
    rows = [json.loads(line) for line in run.stdout.splitlines()]
    assert {r["proposal_id"] for r in rows} == {"a", "b"}
