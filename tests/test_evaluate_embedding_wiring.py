"""embedding_similarity must be usable wherever evaluate is, not only from its CLI.

It used to register itself only inside evaluate.py's `__main__` block, so
apply_proposal() and skill_quality.py -- which import evaluate as a library -- raised
"Unknown evaluator" when it was enabled. Each check runs in a fresh interpreter so no
other test's import of embedding_similarity can make it pass by accident.
"""

import os
import subprocess
import sys

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")


def _run(code):
    env = {**os.environ, "SKILL_EVOLUTION_EVALUATORS": "deterministic,embedding_similarity"}
    return subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {SCRIPTS!r}); {code}"],
                          capture_output=True, text=True, env=env)


def test_library_import_resolves_embedding_similarity():
    proc = _run("import proposal, evaluate; print([e.name for e in evaluate.get_enabled_evaluators()])")

    assert proc.returncode == 0, proc.stderr
    assert "embedding_similarity" in proc.stdout


def test_resolving_the_evaluator_does_not_load_fastembed():
    proc = _run("import evaluate; evaluate.get_enabled_evaluators(); print('fastembed' in sys.modules)")

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False"


# ── U5: the gate's skill target hands embedding_similarity real comparison context ──
# End to end through evaluate_and_record()/apply_proposal() with a fake embedding
# backend: the isolated evaluator tests pass context in by hand, which is exactly how the
# missing baseline_size wiring went unnoticed before.

import json
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, SCRIPTS)

import evaluate  # noqa: E402
import host  # noqa: E402
import proposal as proposal_module  # noqa: E402
import skill_quality  # noqa: E402
from proposal import ProposalType, ProposedChange, SkillEvolutionProposal  # noqa: E402

INSTALLED = "---\nname: s\ndescription: d\n---\n\n# S\n\nOld guidance.\n"


@pytest.fixture
def history(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("SKILL_EVOLUTION_GATE_"):
            monkeypatch.delenv(key, raising=False)
    path = tmp_path / "eval_history.jsonl"
    monkeypatch.setattr(evaluate, "get_history_path", lambda: str(path))
    monkeypatch.setattr(proposal_module, "save_proposal", lambda p, directory=None: "noop")
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "embedding_similarity")
    return path


@pytest.fixture
def backend():
    fake = Mock()
    fake.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
    with patch("embedding_similarity.get_embedding_backend", return_value=fake):
        yield fake


def _entries(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def _improve(field="body", old="Old guidance.", new="New guidance."):
    return SkillEvolutionProposal(
        proposal_id="p-1", type=ProposalType.IMPROVE_EXISTING, target_skill="s",
        confidence=0.9, summary="s", rationale="r",
        proposed_changes=[ProposedChange(field=field, old_value=old, new_value=new)],
    )


def _create(body):
    return SkillEvolutionProposal(
        proposal_id="p-new", type=ProposalType.CREATE_NEW, target_skill=None,
        confidence=0.9, summary="new skill", rationale="r",
        proposed_changes=[ProposedChange(field="name", new_value="fresh-skill"),
                          ProposedChange(field="body", new_value=body)],
    )


class _SkillsHost(host.HostAdapter):
    """A host whose installed skills are real files under tmp_path."""
    supports_write = True

    def __init__(self, skills):
        self.skills = skills
        self.iter_calls = 0

    def iter_sessions(self, since=None):
        return []

    def iter_skills(self, extra_roots=None):
        self.iter_calls += 1
        return [{"name": n, "category": "user", "description": "", "path": str(p), "size": 0}
                for n, p in self.skills]

    def apply_skill_write(self, plan):
        raise AssertionError("a blocked proposal must never reach the write")


def _use_host(monkeypatch, tmp_path, bodies):
    skills = []
    for name, body in bodies.items():
        p = tmp_path / name / "SKILL.md"
        p.parent.mkdir(parents=True)
        p.write_text(body)
        skills.append((name, p))
    adapter = _SkillsHost(skills)
    monkeypatch.setitem(host.HOST_ADAPTERS, "skills_host", adapter)
    monkeypatch.setenv(host.HOST_ENV_VAR, "skills_host")
    return adapter


def test_body_change_with_installed_body_runs_drift(history, backend, monkeypatch):
    monkeypatch.setattr(evaluate, "installed_skill_body", lambda name: INSTALLED)

    evaluate.evaluate_and_record(_improve(), gate_targets=["skill"])

    embedded = [t for call in backend.embed.call_args_list for t in call.args[0]]
    assert any("New guidance." in t for t in embedded)
    assert any("Old guidance." in t for t in embedded)
    assert "skill/embedding_similarity=pass" in _entries(history)[0]["feedback"]


def test_body_change_without_installed_body_abstains_and_sizes_are_unchanged(history, backend, monkeypatch):
    monkeypatch.setattr(evaluate, "installed_skill_body", lambda name: None)

    evaluate.evaluate_and_record(_improve(), gate_targets=["skill"])

    (entry,) = _entries(history)
    assert "skill/embedding_similarity=n/a" in entry["feedback"]
    assert entry["baseline_size"] == len("Old guidance.".encode("utf-8"))
    backend.embed.assert_not_called()


def test_description_change_abstains(history, backend, monkeypatch):
    monkeypatch.setattr(evaluate, "installed_skill_body", lambda name: INSTALLED)

    evaluate.evaluate_and_record(_improve(field="description", old="d", new="better d"),
                                 gate_targets=["skill"])

    assert "skill/embedding_similarity=n/a" in _entries(history)[0]["feedback"]


def test_proposal_target_abstains(history, backend, monkeypatch):
    monkeypatch.setattr(evaluate, "installed_skill_body", lambda name: INSTALLED)

    evaluate.evaluate_and_record(_improve(), gate_targets=["proposal"])

    assert "proposal/embedding_similarity=n/a" in _entries(history)[0]["feedback"]


def test_create_new_compares_against_every_installed_skill_by_name(history, backend, monkeypatch, tmp_path):
    _use_host(monkeypatch, tmp_path, {"alpha": "---\nname: alpha\n---\nA", "beta": "---\nname: beta\n---\nB"})

    (result,) = evaluate.evaluate_skill_text(_create("---\nname: fresh-skill\n---\nNew"))

    assert result.not_applicable is False
    bodies = backend.embed.call_args_list[1].args[0]
    assert sorted(bodies) == ["---\nname: alpha\n---\nA", "---\nname: beta\n---\nB"]


def test_create_new_without_embedding_evaluator_reads_no_skill_bodies(history, monkeypatch, tmp_path):
    adapter = _use_host(monkeypatch, tmp_path, {"alpha": "A"})
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    # Isolate the duplicate-set path from the (pre-existing) baseline lookup.
    monkeypatch.setattr(evaluate, "installed_skill_body", lambda name: None)

    evaluate.evaluate_skill_text(_create("---\nname: fresh-skill\n---\nNew"))

    assert adapter.iter_calls == 0


def test_near_duplicate_create_new_is_blocked_by_apply_proposal(history, monkeypatch, tmp_path):
    body = "---\nname: fresh-skill\ndescription: d\n---\n\n# Fresh\n\nDeploy things.\n"
    _use_host(monkeypatch, tmp_path, {"deploy-helper": "deploy", "unrelated": "other"})
    fake = Mock()
    fake.embed.side_effect = [[[1.0, 0.0]], [[0.99, 0.141], [0.0, 1.0]]]

    with patch("embedding_similarity.get_embedding_backend", return_value=fake):
        result = proposal_module.apply_proposal(_create(body), min_confidence=0.5)

    assert result["can_apply"] is False
    feedback = " ".join(r["feedback"] for r in result["evaluation_results"])
    assert "deploy-helper" in feedback


def test_skill_quality_abstains_and_leaves_the_mean_alone(backend, monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic,embedding_similarity")
    monkeypatch.setattr(evaluate, "installed_skill_body", lambda name: INSTALLED)
    monkeypatch.setattr(evaluate, "append_history", lambda *a, **k: None)
    monkeypatch.setattr(skill_quality, "get_previous_score", lambda name: None)

    result = skill_quality.evaluate_skill_quality("s")

    assert result.current_score == 1.0
    backend.embed.assert_not_called()
