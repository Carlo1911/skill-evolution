"""Two parsers that read past what they were given.

- rewrite_frontmatter_description() scanned the whole file, so on a skill whose
  frontmatter had no `description:` the Claude Code adapter rewrote a body line instead of
  refusing (reproduced; Hermes only emits a `patch`, but the gate's composed candidate
  uses the same helper on both hosts).
- LLMJudgeEvaluator matched `\\{.*\\}` greedily, so a reasoning model that mentioned
  `{frontmatter}` before its JSON answer failed closed with "malformed JSON".
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
import skill_index
from host_sandbox import HOSTS, build_host
from proposal import ProposedChange, SkillEvolutionProposal

NO_DESCRIPTION = "---\nname: demo\n---\n\n# Demo\n\ndescription: keep this body line\n\nStep two.\n"
ANSWER = {"correctness": 0.9, "procedure_following": 0.8, "conciseness": 0.7, "feedback": "ok"}


# ── rewrite_frontmatter_description ─────────────────────────────────

def test_rewrites_the_frontmatter_description():
    content = "---\nname: x\ndescription: old\n---\n\nbody\n"

    assert skill_index.rewrite_frontmatter_description(content, "new") == \
        "---\nname: x\ndescription: new\n---\n\nbody\n"


@pytest.mark.parametrize("content", [
    NO_DESCRIPTION,                                   # description only in the body
    "# No frontmatter\n\ndescription: body line\n",   # no frontmatter at all
    "---\nname: x\n",                                 # frontmatter never closed, no description
])
def test_never_reaches_outside_the_frontmatter(content):
    assert skill_index.rewrite_frontmatter_description(content, "new") is None


def test_refuses_a_multi_line_value():
    assert skill_index.rewrite_frontmatter_description(
        "---\ndescription: old\n---\n", "two\nlines") is None


def test_claude_code_apply_refuses_instead_of_rewriting_a_body_line(tmp_path, monkeypatch):
    box = build_host("claude_code", tmp_path, monkeypatch)
    box.write_skill("demo", NO_DESCRIPTION)

    result = box.adapter.apply_skill_write({
        "type": "improve_existing", "target_skill": "demo", "proposal_id": "x",
        "changes": [{"field": "description", "old_value": None, "new_value": "new"}],
    })

    assert result["can_apply"] is False
    assert box.read_skill("demo") == NO_DESCRIPTION


@pytest.mark.parametrize("host_name", HOSTS)
def test_gate_refuses_folding_a_description_into_a_skill_without_one(host_name, tmp_path, monkeypatch):
    box = build_host(host_name, tmp_path, monkeypatch)
    box.write_skill("demo", NO_DESCRIPTION)
    p = SkillEvolutionProposal(proposal_id="p1", target_skill="demo", confidence=0.9, summary="s",
                               proposed_changes=[ProposedChange("body", "Step two.", "Step two!"),
                                                 ProposedChange("description", None, "new")])

    candidate = evaluate._build_candidate(p)

    assert candidate.error is not None and "description" in candidate.error
    assert "keep this body line" in candidate.content


# ── judge response parsing ──────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    json.dumps(ANSWER),
    "Thinking: the {frontmatter} looks fine.\n" + json.dumps(ANSWER),
    "```json\n" + json.dumps(ANSWER) + "\n```",
    '{"draft": true}\nFinal answer: ' + json.dumps(ANSWER),
])
def test_judge_finds_its_answer(raw):
    score, parsed = evaluate.LLMJudgeEvaluator().parse_and_score(raw)

    assert parsed["feedback"] == "ok"
    assert score == pytest.approx(0.8)


def test_the_last_rubric_object_wins():
    first = dict(ANSWER, correctness=0.1, feedback="draft")
    raw = json.dumps(first) + "\nRevised: " + json.dumps(ANSWER)

    _, parsed = evaluate.LLMJudgeEvaluator().parse_and_score(raw)

    assert parsed["feedback"] == "ok"


@pytest.mark.parametrize("raw", [
    "no json here",
    '{"correctness": 0.9}',                                   # missing rubric keys
    json.dumps(dict(ANSWER, correctness=1.5)),                # out of range
    json.dumps(dict(ANSWER, conciseness="high")),             # not numeric
    "[1, 2, 3]",
])
def test_judge_still_fails_closed(raw):
    with pytest.raises(ValueError):
        evaluate.LLMJudgeEvaluator().parse_and_score(raw)


def test_a_parse_failure_is_not_mistaken_for_a_provider_fault():
    """The backfill classifies provider faults by message shape; the new parse error must
    stay out of that pattern so it is never relabelled as an outage."""
    message = "llm_judge failed closed: no JSON object with the rubric keys found in judge response: 'x'"

    assert not evaluate._PROVIDER_FAULT_FEEDBACK.match(message)
