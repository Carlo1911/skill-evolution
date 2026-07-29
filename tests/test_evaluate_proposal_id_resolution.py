"""Regression tests for resolving a proposal id on the CLI (P0-2).

`--eval-target proposal --proposal-id <id>` passed the id straight into
`proposal.load_proposal()`, which takes a *path* -- so the id its own `--help` advertises
raised FileNotFoundError, and the `if not p:` guard below the call could never fire because
load_proposal() raises rather than returning None.

Passing a path worked but was worse than it looked: the history key became
`proposal:docs/skill-evolution/proposals/<id>.md`, putting a filesystem path into the target
namespace that RegressionEvaluator and optimize_skill.find_low_scoring_targets() key on.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate

REPO = os.path.join(os.path.dirname(__file__), "..")

PROPOSAL = """---
proposal_id: 20260729-777
created_at: 2026-07-29T03:00:00-05:00
type: improve_existing
target_skill: some-skill
confidence: 0.8
summary: A summary long enough to be scored as prose
status: proposed
proposed_changes:
  - field: description
    old_value: old text here
    new_value: a new description for the skill
    description: tweak the description
---

# Proposal

## Rationale

Grounded in session evidence.
"""


@pytest.fixture
def proposals_dir(tmp_path):
    d = tmp_path / "proposals"
    d.mkdir()
    (d / "20260729-777.md").write_text(PROPOSAL)
    return d


def _run(args, env_extra):
    env = {**os.environ, "SKILL_EVOLUTION_EVALUATORS": "deterministic", **env_extra}
    return subprocess.run(
        [sys.executable, "scripts/evaluate.py", *args],
        cwd=REPO, env=env, capture_output=True, text=True,
    )


def test_bare_id_resolves_against_the_proposals_dir(proposals_dir, tmp_path):
    """The documented invocation: an id, not a path."""
    r = _run(
        ["--eval-target", "proposal", "--proposal-id", "20260729-777"],
        {"SKILL_EVOLUTION_PROPOSALS_DIR": str(proposals_dir),
         "SKILL_EVOLUTION_HISTORY_PATH": str(tmp_path / "h.jsonl")},
    )
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr
    assert "deterministic:" in r.stdout


def test_history_key_is_the_proposal_id_never_a_path(proposals_dir, tmp_path):
    """Even when given a path, the recorded target must be `proposal:<id>`.

    A path-shaped key pollutes the namespace RegressionEvaluator baselines against.
    """
    history = tmp_path / "h.jsonl"
    r = _run(
        ["--eval-target", "proposal", "--proposal-id",
         str(proposals_dir / "20260729-777.md")],
        {"SKILL_EVOLUTION_PROPOSALS_DIR": str(proposals_dir),
         "SKILL_EVOLUTION_HISTORY_PATH": str(history)},
    )
    assert r.returncode == 0, r.stderr

    entries = [l for l in history.read_text().splitlines() if l.strip()]
    assert entries, "expected a history entry"
    import json
    targets = [json.loads(l)["target"] for l in entries]
    assert targets == ["proposal:20260729-777"]
    assert not any("/" in t for t in targets)


def test_missing_proposal_reports_cleanly_instead_of_a_traceback(tmp_path):
    r = _run(
        ["--eval-target", "proposal", "--proposal-id", "does-not-exist"],
        {"SKILL_EVOLUTION_PROPOSALS_DIR": str(tmp_path),
         "SKILL_EVOLUTION_HISTORY_PATH": str(tmp_path / "h.jsonl")},
    )
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "does-not-exist" in r.stderr
