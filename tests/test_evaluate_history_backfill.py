"""Tests for backfill_transport_failures(): marking historical provider-fault entries.

Entries written before every gate-entry path carried `transport_failure` record a
provider outage as an ordinary failed evaluation, so find_low_scoring_targets() reads
them as quality signals. The backfill recognizes only the exact message shapes the
provider layer emits -- never the bare `llm_judge failed closed:` prefix, which judge
parse errors share.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import evaluate
import optimize_skill


def _gate(target, feedback, passed=False, score=0.5, **extra):
    entry = {
        "target": target,
        "timestamp": "2026-08-03T00:00:00+00:00",
        "session_ids": [],
        "score": score,
        "feedback": feedback,
        "passed": passed,
        "evaluator_name": "gate",
        "kind": "skill_text",
    }
    entry.update(extra)
    return json.dumps(entry)


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n")


def _entries(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


HTTP_401 = "llm_judge failed closed: Claude provider call failed: HTTP Error 401: Unauthorized"
CHAIN_401 = ("llm_judge failed closed: All 1 provider(s) failed: claude: claude: "
             "Claude provider call failed: HTTP Error 401: Unauthorized")


def test_marks_both_observed_provider_shapes(tmp_path):
    history = tmp_path / "eval_history.jsonl"
    _write(history, [_gate("skill:a", HTTP_401), _gate("skill:b", CHAIN_401)])

    report = evaluate.backfill_transport_failures(str(history), apply=True)

    assert report["matched"] == 2
    assert [e.get("transport_failure") for e in _entries(history)] == [True, True]


def test_other_provider_message_shapes_are_recognized(tmp_path):
    history = tmp_path / "eval_history.jsonl"
    feedbacks = [
        "llm_judge failed closed: Unexpected OpenAI response shape: {}",
        "llm_judge failed closed: OpenAI provider selected but OPENAI_API_KEY is not set. Export it.",
        "llm_judge failed closed: Unknown provider(s) 'nope'. Available: claude",
        "llm_judge failed closed: LM Studio provider call failed: <urlopen error refused>",
    ]
    _write(history, [_gate(f"skill:s{i}", fb) for i, fb in enumerate(feedbacks)])

    report = evaluate.backfill_transport_failures(str(history), apply=True)

    assert report["matched"] == len(feedbacks)


def test_judge_parse_errors_are_never_marked(tmp_path):
    history = tmp_path / "eval_history.jsonl"
    _write(history, [
        _gate("skill:a", "llm_judge failed closed: malformed JSON in judge response: Expecting value"),
        _gate("skill:b", "llm_judge failed closed: judge response key 'correctness' out of range [0,1]: 3"),
        _gate("skill:c", "llm_judge failed closed: no JSON object found in judge response: hi"),
    ])

    report = evaluate.backfill_transport_failures(str(history), apply=True)

    assert report["matched"] == 0
    assert all("transport_failure" not in e for e in _entries(history))


def test_passing_already_flagged_and_non_gate_entries_are_untouched(tmp_path):
    history = tmp_path / "eval_history.jsonl"
    _write(history, [
        _gate("skill:a", HTTP_401, passed=True),
        _gate("skill:b", HTTP_401, transport_failure=True),
        json.dumps({"target": "skill:c", "feedback": HTTP_401, "passed": False,
                    "evaluator_name": "llm_judge", "score": 0.0}),
    ])
    before = history.read_bytes()

    report = evaluate.backfill_transport_failures(str(history), apply=True)

    assert report["matched"] == 0
    assert history.read_bytes() == before


def test_dry_run_leaves_file_identical_and_makes_no_backup(tmp_path):
    history = tmp_path / "eval_history.jsonl"
    _write(history, [_gate("skill:a", HTTP_401)])
    before = history.read_bytes()

    report = evaluate.backfill_transport_failures(str(history))

    assert report["matched"] == 1
    assert report["backup_path"] is None
    assert history.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["eval_history.jsonl"]


def test_apply_keeps_a_byte_identical_backup_and_only_adds_the_flag(tmp_path):
    history = tmp_path / "eval_history.jsonl"
    unchanged = _gate("skill:keep", "skill/llm_judge=fail")
    _write(history, [unchanged, _gate("skill:a", HTTP_401)])
    before = history.read_bytes()

    report = evaluate.backfill_transport_failures(str(history), apply=True)

    with open(report["backup_path"], "rb") as f:
        assert f.read() == before
    lines = history.read_text().splitlines()
    assert lines[0] == unchanged
    marked = json.loads(lines[1])
    original = json.loads(_gate("skill:a", HTTP_401))
    assert marked == {**original, "transport_failure": True}


def test_corrupt_line_survives_in_place(tmp_path):
    history = tmp_path / "eval_history.jsonl"
    _write(history, [_gate("skill:a", HTTP_401), "{not json", _gate("skill:b", HTTP_401)])

    report = evaluate.backfill_transport_failures(str(history), apply=True)

    assert report["matched"] == 2
    assert report["unparseable"] == 1
    assert history.read_text().splitlines()[1] == "{not json"


def test_second_apply_marks_nothing(tmp_path):
    history = tmp_path / "eval_history.jsonl"
    _write(history, [_gate("skill:a", HTTP_401)])
    evaluate.backfill_transport_failures(str(history), apply=True)
    after_first = history.read_bytes()

    report = evaluate.backfill_transport_failures(str(history), apply=True)

    assert report["matched"] == 0
    assert history.read_bytes() == after_first


def test_marked_targets_fall_back_to_their_last_unmarked_entry(tmp_path):
    """The point of the backfill: the optimizer stops reading outages as quality."""
    history = tmp_path / "eval_history.jsonl"
    _write(history, [
        _gate("skill:outage-only", HTTP_401),
        _gate("skill:had-real-score", "judge said fine", passed=True, score=0.9),
        _gate("skill:had-real-score", CHAIN_401),
    ])
    before = {e["target"] for e in optimize_skill.find_low_scoring_targets(history_path=str(history))}
    assert before == {"skill:outage-only", "skill:had-real-score"}

    evaluate.backfill_transport_failures(str(history), apply=True)

    assert optimize_skill.find_low_scoring_targets(history_path=str(history)) == []


def test_missing_file_reports_nothing(tmp_path):
    report = evaluate.backfill_transport_failures(str(tmp_path / "absent.jsonl"), apply=True)

    assert report["matched"] == 0
    assert not (tmp_path / "absent.jsonl").exists()
