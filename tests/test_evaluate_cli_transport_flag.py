"""The single-target CLI branches of evaluate.py must keep the provider-fault flag.

`evaluate.py --eval-target proposal|tool_calls|analyzer_prompt` builds its own combined
gate entry instead of going through evaluate_and_record(), so it has to carry
`transport_failure` forward itself -- otherwise an outage is recorded as a content
failure, the same defect skill_quality.py had.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import evaluate


OUTAGE = evaluate.EvalResult(score=0.0, feedback="llm_judge failed closed: HTTP 503",
                             passed=False, evaluator_name="llm_judge", transport_failure=True)
CONTENT_FAIL = evaluate.EvalResult(score=0.2, feedback="wrong procedure", passed=False,
                                   evaluator_name="llm_judge")
OK = evaluate.EvalResult(score=1.0, feedback="", passed=True, evaluator_name="regression")


@pytest.fixture
def history(tmp_path, monkeypatch):
    path = tmp_path / "eval_history.jsonl"
    monkeypatch.setenv("SKILL_EVOLUTION_HISTORY_PATH", str(path))
    fake = SimpleNamespace(proposal_id="p-1", summary="s", rationale="r")
    monkeypatch.setattr(evaluate, "_load_proposal_by_id_or_path", lambda pid, mod: fake)
    return path


CASES = [
    (["--eval-target", "proposal", "--proposal-id", "p-1"], "evaluate_proposal"),
    (["--eval-target", "tool_calls", "--session-id", "s-1"], "evaluate_tool_calls"),
    (["--eval-target", "analyzer_prompt", "--session-id", "s-1", "--proposal-id", "p-1"],
     "evaluate_analyzer_prompt"),
]


def _run(monkeypatch, argv, target_fn, results):
    monkeypatch.setattr(evaluate, target_fn, lambda *a, **k: list(results))
    monkeypatch.setattr(sys, "argv", ["evaluate.py", *argv])
    evaluate.main()


@pytest.mark.parametrize("argv,target_fn", CASES)
def test_provider_outage_is_flagged(history, monkeypatch, argv, target_fn):
    _run(monkeypatch, argv, target_fn, [OUTAGE, OK])

    (entry,) = [json.loads(line) for line in history.read_text().splitlines()]
    assert entry["transport_failure"] is True


@pytest.mark.parametrize("argv,target_fn", CASES)
def test_content_failure_stays_unflagged(history, monkeypatch, argv, target_fn):
    _run(monkeypatch, argv, target_fn, [CONTENT_FAIL, OK])

    (entry,) = [json.loads(line) for line in history.read_text().splitlines()]
    assert "transport_failure" not in entry
    assert entry["passed"] is False
