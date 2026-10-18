"""The grounding check: is a proposal's new text supported by the sessions it cites?

No gate path supplied `sessions`, and the evaluator ran one mode per call, so grounding
never ran. Sessions are now read by id through the host adapter (already redacted), the
check compares only the proposed new text, window by window, and runs alongside the drift
or duplicate check.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import embedding_similarity
import evaluate
import host
from embedding_similarity import EmbeddingSimilarityEvaluator
from host_sandbox import HOSTS, build_host
from proposal import ProposedChange, SkillEvolutionProposal

VOCAB = ["cwd", "deleted", "exit", "126", "pwd", "docker", "volume", "prune", "steps", "kept"]


class BagOfWords:
    """Deterministic stand-in for the embedding model: word counts over VOCAB."""

    def embed(self, texts):
        return [[t.lower().count(w) for w in VOCAB] + [0.01] for t in texts]


@pytest.fixture
def evaluator():
    with patch("embedding_similarity.get_embedding_backend", return_value=BagOfWords()):
        yield EmbeddingSimilarityEvaluator()


# ── reading sessions by id ─────────────────────────────────────────

@pytest.fixture(params=HOSTS)
def box(request, tmp_path, monkeypatch):
    return build_host(request.param, tmp_path, monkeypatch)


def test_read_sessions_returns_full_redacted_text_in_order(box):
    long = "the cwd was deleted " * 60  # longer than the 500-char preview
    box.add_sessions([("s1", 1000.0, [("user", long), ("assistant", "run pwd")]),
                      ("s2", 2000.0, [("user", "mail me at a@b.co")])])

    sessions = box.adapter.read_sessions(["s2", "s1", "missing"])

    assert [s["session_id"] for s in sessions] == ["s2", "s1"]
    assert sessions[0]["messages"][0]["content"] == "mail me at [PII:email]"
    assert len(sessions[1]["messages"][0]["content"]) > 500  # not the 500-char preview


def test_read_sessions_of_nothing_is_empty(box):
    assert box.adapter.read_sessions([]) == []


def test_claude_code_refuses_ids_that_leave_projects(tmp_path, monkeypatch):
    box = build_host("claude_code", tmp_path, monkeypatch)
    (box.root / "projects").mkdir()
    (box.root / "outside.jsonl").write_text('{"type": "user", "message": {"content": "x"}}\n')

    assert box.adapter.read_sessions(["../outside", "*", ""]) == []


def test_hermes_with_no_database_creates_none(tmp_path, monkeypatch):
    box = build_host("hermes", tmp_path, monkeypatch)
    missing = tmp_path / "nowhere" / "state.db"
    monkeypatch.setenv("SKILL_EVOLUTION_DB_PATH", str(missing))

    assert box.adapter.read_sessions(["s1"]) == []
    assert not missing.exists()


# ── the gate hands the evaluator the sessions and the new text ───────────

BODY = "---\nname: demo\ndescription: d\n---\n\n## Steps\n\nStep one.\n" + "Kept.\n" * 30


def _proposal(new="Step one.\n\nIf pwd fails, the cwd was deleted: exit 126.", sessions=("s1",)):
    return SkillEvolutionProposal(proposal_id="p1", target_skill="demo", confidence=0.9,
                                  summary="s", rationale="r", session_ids=list(sessions),
                                  proposed_changes=[ProposedChange("body", "Step one.", new)])


def _captured_context(proposal):
    seen = {}

    def capture(content, target, context=None):
        seen.update(context or {})
        return []
    with patch("evaluate.run_evaluators", side_effect=capture):
        evaluate.evaluate_skill_text(proposal)
    return seen


def test_the_gate_passes_cited_sessions_and_only_the_new_text(box, monkeypatch):
    box.write_skill("demo", BODY)
    box.add_sessions([("s1", 1000.0, [("user", "exit 126 everywhere"), ("assistant", "the cwd was deleted")])])
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic,embedding_similarity")

    context = _captured_context(_proposal())

    assert context["sessions"] == ["exit 126 everywhere", "the cwd was deleted"]
    assert context["grounding_text"] == "Step one.\n\nIf pwd fails, the cwd was deleted: exit 126."
    assert "baseline" in context


def test_no_sessions_are_read_unless_the_evaluator_is_enabled(box, monkeypatch):
    box.write_skill("demo", BODY)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic")
    with patch.object(type(box.adapter), "read_sessions", side_effect=AssertionError("read")):
        context = _captured_context(_proposal())
    assert "sessions" not in context


def test_a_proposal_without_session_ids_gets_no_sessions(box, monkeypatch):
    box.write_skill("demo", BODY)
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic,embedding_similarity")
    assert "sessions" not in _captured_context(_proposal(sessions=()))


# ── several checks in one call ────────────────────────────────────

def test_drift_passing_and_grounding_failing_fails_naming_both(evaluator):
    baseline = "## Steps\n\nkept kept kept\n"
    content = baseline + "docker volume prune\n"
    result = evaluator.evaluate(content, {"baseline": baseline,
                                          "sessions": ["the cwd was deleted, exit 126"],
                                          "grounding_text": "docker volume prune"})
    assert result.passed is False
    assert "drift:" in result.feedback and "grounding:" in result.feedback


def test_grounded_text_passes_with_score_one(evaluator):
    result = evaluator.evaluate("x", {"sessions": ["pwd shows the deleted cwd, exit 126"],
                                      "grounding_text": "pwd: deleted cwd gives exit 126"})
    assert result.passed and result.score == 1.0


def test_nothing_to_compare_abstains(evaluator):
    result = evaluator.evaluate("x", {"sessions": ["", "   "], "grounding_text": "pwd"})
    assert result.not_applicable


def test_an_ungrounded_section_lowers_the_score_in_proportion(evaluator, monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_EMBEDDING_GROUNDING_THRESHOLD", "0.99")
    sessions = ["the cwd was deleted: pwd fails with exit 126"]
    supported = "## Fix\n\npwd, cwd deleted, exit 126\n"
    invented = "## Other\n\ndocker volume prune\n"
    strict = EmbeddingSimilarityEvaluator()
    with patch("embedding_similarity.get_embedding_backend", return_value=BagOfWords()):
        only_supported = strict.evaluate("x", {"sessions": sessions, "grounding_text": supported})
        half = strict.evaluate("x", {"sessions": sessions, "grounding_text": supported + invented})

    assert only_supported.passed
    assert not half.passed
    assert 0.3 < half.score < 0.8
