"""Every judged history entry names the provider, model and rubric version that scored it.

With a fallback chain such as `opencode,lmstudio` one score may come from a hosted model
and the next from a 3.8B local one, and history could not tell them apart -- nor tell a
change in the skill from a change in the judge.
"""

import hashlib
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import skill_quality
from evaluate import EvalResult, LLMJudgeEvaluator, ProviderError, RegressionEvaluator
from host_sandbox import HOSTS, build_host
from proposal import ProposedChange, SkillEvolutionProposal

GOOD = json.dumps({"correctness": 0.9, "procedure_following": 0.9, "conciseness": 0.9,
                   "feedback": "fine"})
BODY = "---\nname: demo\ndescription: d\n---\n\n## Steps\n\nStep one.\n" + "Kept.\n" * 30


def _fail(prompt, timeout=60):
    raise ProviderError("HTTP 401")


def _answer(prompt, timeout=60):
    return GOOD


@pytest.fixture
def chain(monkeypatch):
    """`claude` fails, `ollama` answers with model m-local."""
    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "claude", _fail)
    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "ollama", _answer)
    monkeypatch.setenv("SKILL_EVOLUTION_PROVIDER", "claude,ollama")
    monkeypatch.setenv("SKILL_EVOLUTION_OLLAMA_MODEL", "m-local")


def test_the_judge_names_the_provider_in_the_chain_that_answered(chain):
    result = LLMJudgeEvaluator().evaluate("content")

    assert result.passed
    assert result.judge == {"provider": "ollama", "model": "m-local",
                            "rubric_version": evaluate.JUDGE_RUBRIC_VERSION}


def test_a_malformed_answer_still_names_who_gave_it(monkeypatch, chain):
    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "ollama", lambda prompt, timeout=60: "no json")

    result = LLMJudgeEvaluator().evaluate("content")

    assert not result.passed and not result.transport_failure
    assert result.judge["provider"] == "ollama"


def test_a_provider_outage_has_no_judge(monkeypatch, chain):
    monkeypatch.setitem(evaluate.PROVIDER_CALLERS, "ollama", _fail)

    result = LLMJudgeEvaluator().evaluate("content")

    assert result.transport_failure and result.judge is None


def test_a_stubbed_call_provider_records_no_judge(monkeypatch):
    monkeypatch.setattr(evaluate, "call_provider", lambda prompt, evaluator_name=None: GOOD)

    assert LLMJudgeEvaluator().evaluate("content").judge is None


def test_provider_model_matches_what_each_caller_uses(monkeypatch):
    monkeypatch.setenv("SKILL_EVOLUTION_OPENCODE_MODEL", "legacy")
    assert evaluate.provider_model("opencode") == "legacy"
    monkeypatch.setenv("SKILL_EVOLUTION_OPENCODE_ZEN_MODEL", "zen-specific")
    assert evaluate.provider_model("opencode_zen") == "zen-specific"
    assert evaluate.provider_model("opencode_go") == evaluate.DEFAULT_OPENCODE_GO_MODEL
    assert evaluate.provider_model("nope") == ""


# ── every write path carries it ─────────────────────────────────────

@pytest.fixture(params=HOSTS)
def box(request, tmp_path, monkeypatch, chain):
    sandbox = build_host(request.param, tmp_path, monkeypatch)
    sandbox.write_skill("demo", BODY)
    monkeypatch.setenv("SKILL_EVOLUTION_HISTORY_PATH", str(tmp_path / "h.jsonl"))
    monkeypatch.setenv("SKILL_EVOLUTION_EVALUATORS", "deterministic,llm_judge,regression")
    return sandbox


def _entries(tmp_path):
    return [json.loads(line) for line in (tmp_path / "h.jsonl").read_text().splitlines()]


def test_the_gate_records_the_judge_for_every_target(box, tmp_path):
    p = SkillEvolutionProposal(proposal_id="p1", target_skill="demo", confidence=0.9,
                               summary="s", rationale="r",
                               proposed_changes=[ProposedChange("body", "Step one.", "Step one, clarified.")])
    evaluate.evaluate_and_record(p)

    entries = _entries(tmp_path)
    assert {e["target"] for e in entries} == {"skill:demo", "proposal:p1"}
    assert all(e["judge"]["model"] == "m-local" for e in entries)


def test_skill_quality_records_the_judge(box, tmp_path):
    skill_quality.evaluate_skill_quality("demo")

    [entry] = _entries(tmp_path)
    assert entry["judge"]["provider"] == "ollama"


def test_the_cli_gate_result_carries_the_judge(chain):
    combined = evaluate._cli_gate_result([LLMJudgeEvaluator().evaluate("content")])
    assert combined.judge["provider"] == "ollama"


def test_an_entry_without_a_judge_has_no_key(tmp_path):
    path = tmp_path / "h.jsonl"
    evaluate.append_history("skill:x", EvalResult(1.0, "ok", True, "gate"), path=str(path))
    assert "judge" not in json.loads(path.read_text())


# ── regression notes a change of judge, without changing its verdict ─────────

def _regress(monkeypatch, tmp_path, baseline_judge, current_judge, new_score):
    path = tmp_path / "h.jsonl"
    evaluate.append_history("skill:x", EvalResult(0.8, "ok", True, "gate", judge=baseline_judge),
                            path=str(path))
    monkeypatch.setattr(evaluate, "get_history_path", lambda: str(path))
    return RegressionEvaluator().evaluate("c", {"target": "skill:x", "new_score": new_score,
                                                "judge": current_judge})


BIG = {"provider": "opencode", "model": "big-pickle", "rubric_version": "v1"}
SMALL = {"provider": "lmstudio", "model": "phi", "rubric_version": "v1"}


@pytest.mark.parametrize("new_score, verdict", [(0.9, True), (0.5, False)])
def test_a_different_judge_is_noted_and_the_verdict_unchanged(monkeypatch, tmp_path, new_score, verdict):
    result = _regress(monkeypatch, tmp_path, BIG, SMALL, new_score)

    assert result.passed is verdict
    assert "baseline judged by opencode/big-pickle rubric v1; this run lmstudio/phi rubric v1" in result.feedback


def test_the_same_judge_adds_nothing(monkeypatch, tmp_path):
    result = _regress(monkeypatch, tmp_path, BIG, dict(BIG), 0.9)
    assert "judged by" not in result.feedback


def test_run_evaluators_hands_regression_the_current_judge(box, tmp_path, monkeypatch):
    evaluate.append_history("skill:demo", EvalResult(0.5, "ok", True, "gate", judge=BIG))

    results = evaluate.run_evaluators("text", "skill:demo")

    regression = [r for r in results if r.evaluator_name == "regression"][0]
    assert "this run ollama/m-local" in regression.feedback


# ── rubric version ────────────────────────────────────────────────

PINNED_PROMPT_SHA256 = "28181ba11d12931bc7a2c914a80a6b7589c3155bc53c128c641c0f691d2447bc"
PINNED_RUBRIC_VERSION = "2026-10-18.1"


def test_changing_the_judge_prompt_requires_a_new_rubric_version(monkeypatch):
    monkeypatch.setattr(evaluate.secrets, "token_hex", lambda n=16: "0" * (2 * n))
    digest = hashlib.sha256(LLMJudgeEvaluator()._build_prompt("").encode()).hexdigest()

    if evaluate.JUDGE_RUBRIC_VERSION == PINNED_RUBRIC_VERSION:
        assert digest == PINNED_PROMPT_SHA256, (
            "the judge prompt changed: bump evaluate.JUDGE_RUBRIC_VERSION, then update "
            "PINNED_RUBRIC_VERSION and PINNED_PROMPT_SHA256 here")
    else:
        pytest.fail("JUDGE_RUBRIC_VERSION was bumped: re-pin PINNED_RUBRIC_VERSION and "
                    f"PINNED_PROMPT_SHA256 (now {digest})")
