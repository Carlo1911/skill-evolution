"""Tests for scripts/evaluate.py's versioned evaluation history store (U2)."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import evaluate
from evaluate import (
    EvalResult, RegressionEvaluator, append_history, get_history_archive_path,
    migrate_proposal_history, prune_history, read_history,
)


def _result(score=0.8, evaluator_name="dummy", passed=True, feedback="ok"):
    return EvalResult(score=score, feedback=feedback, passed=passed, evaluator_name=evaluator_name)


@pytest.fixture
def history_path(tmp_path):
    return str(tmp_path / "eval_history.jsonl")


def test_append_then_read_preserves_both_entries_in_order(history_path):
    append_history("skill:foo", _result(score=0.5), session_ids=["s1"], path=history_path)
    append_history("skill:foo", _result(score=0.9), session_ids=["s2"], path=history_path)

    entries = read_history("skill:foo", path=history_path)
    assert len(entries) == 2
    assert entries[0]["score"] == 0.5
    assert entries[1]["score"] == 0.9


def test_read_history_for_unknown_target_returns_empty_list(history_path):
    assert read_history("skill:never-evaluated", path=history_path) == []


def test_read_history_when_file_does_not_exist_returns_empty_list(tmp_path):
    missing = str(tmp_path / "does_not_exist.jsonl")
    assert read_history("skill:foo", path=missing) == []


def test_append_from_two_separate_calls_both_land(history_path):
    # Simulates two separate process runs both appending (not truncating).
    append_history("skill:foo", _result(score=0.1), path=history_path)
    append_history("skill:foo", _result(score=0.2), path=history_path)
    append_history("skill:bar", _result(score=0.3), path=history_path)

    foo_entries = read_history("skill:foo", path=history_path)
    bar_entries = read_history("skill:bar", path=history_path)
    assert len(foo_entries) == 2
    assert len(bar_entries) == 1


def test_prune_by_count_keeps_most_recent_n_plus_the_earliest_anchor(history_path):
    """Retention is "keep the N most recent, PLUS the earliest entry as a permanent
    anchor" -- not a hard cap at N. The earliest entry is always retained so
    original_size_for_target()'s cumulative-drift baseline never silently shifts forward
    as pruning trims a target's history. Here it's a fourth entry beyond the count=2
    window because it doesn't coincide with the most-recent/last-passing anchors."""
    for score in [0.1, 0.2, 0.3, 0.4, 0.5]:
        append_history("skill:foo", _result(score=score), path=history_path)

    prune_history(path=history_path, retention="2")

    entries = read_history("skill:foo", path=history_path)
    assert [e["score"] for e in entries] == [0.1, 0.4, 0.5]


def test_prune_at_limit_zero_keeps_the_required_anchors_not_a_single_entry(history_path):
    """Limit zero no longer means "keep exactly one entry" -- it means "keep zero from the
    count window, plus whichever anchors aren't already covered." With two entries here,
    both the most-recent and the earliest anchors are distinct, so both survive."""
    append_history("skill:foo", _result(score=0.1), path=history_path)
    append_history("skill:foo", _result(score=0.9), path=history_path)

    prune_history(path=history_path, retention="0")

    entries = read_history("skill:foo", path=history_path)
    assert [e["score"] for e in entries] == [0.1, 0.9]


def test_prune_with_unset_retention_is_unbounded(history_path):
    for score in [0.1, 0.2, 0.3]:
        append_history("skill:foo", _result(score=score), path=history_path)

    prune_history(path=history_path, retention=None)

    entries = read_history("skill:foo", path=history_path)
    assert len(entries) == 3


def test_prune_preserves_other_targets_independently(history_path):
    for score in [0.1, 0.2, 0.3]:
        append_history("skill:foo", _result(score=score), path=history_path)
    append_history("skill:bar", _result(score=0.7), path=history_path)

    prune_history(path=history_path, retention="1")

    # skill:foo keeps its count=1 window (0.3) plus the earliest-entry anchor (0.1).
    assert [e["score"] for e in read_history("skill:foo", path=history_path)] == [0.1, 0.3]
    # skill:bar has one entry total -- it's simultaneously every anchor, so nothing changes.
    assert [e["score"] for e in read_history("skill:bar", path=history_path)] == [0.7]


# ── Archiving: pruned entries are moved, not discarded ────────────────────

def test_prune_archives_dropped_entries_in_original_order(history_path):
    for score in [0.1, 0.2, 0.3, 0.4, 0.5]:
        append_history("skill:foo", _result(score=score), path=history_path)

    prune_history(path=history_path, retention="2")

    archived = _read_jsonl(get_history_archive_path(history_path))
    # 0.1 survives as the earliest-entry anchor; 0.2 and 0.3 are the ones actually dropped.
    assert [e["score"] for e in archived] == [0.2, 0.3]


def test_prune_does_not_create_archive_file_when_nothing_dropped(history_path, tmp_path):
    append_history("skill:foo", _result(score=0.5), path=history_path)

    prune_history(path=history_path, retention="10")  # nothing to drop

    assert not os.path.exists(get_history_archive_path(history_path))


def test_prune_archive_path_defaults_to_sibling_of_a_custom_path(tmp_path):
    custom_path = str(tmp_path / "nested" / "dir" / "history.jsonl")
    for score in [0.1, 0.2, 0.3]:
        append_history("skill:foo", _result(score=score), path=custom_path)

    prune_history(path=custom_path, retention="1")

    expected_archive = str(tmp_path / "nested" / "dir" / "history.archive.jsonl")
    assert get_history_archive_path(custom_path) == expected_archive
    assert os.path.exists(expected_archive)
    # Not leaked into cwd via the default path.
    assert not os.path.exists("history.archive.jsonl")


def test_prune_archive_path_env_var_override(history_path, tmp_path, monkeypatch):
    override = str(tmp_path / "elsewhere" / "custom_archive.jsonl")
    monkeypatch.setenv("SKILL_EVOLUTION_HISTORY_ARCHIVE_PATH", override)
    for score in [0.1, 0.2, 0.3]:
        append_history("skill:foo", _result(score=score), path=history_path)

    prune_history(path=history_path, retention="1")

    assert os.path.exists(override)


def test_prune_appends_to_existing_archive_across_separate_calls(history_path):
    for score in [0.1, 0.2, 0.3]:
        append_history("skill:foo", _result(score=score), path=history_path)
    prune_history(path=history_path, retention="1")  # drops 0.2

    for score in [0.4, 0.5, 0.6]:
        append_history("skill:foo", _result(score=score), path=history_path)
    prune_history(path=history_path, retention="1")  # drops more, but must not truncate

    archived_scores = [e["score"] for e in _read_jsonl(get_history_archive_path(history_path))]
    assert 0.2 in archived_scores
    assert len(archived_scores) >= 2  # second prune added more without wiping the first


# ── The two anchors that close real correctness holes ─────────────────────

def test_earliest_entry_survives_aggressive_count_pruning(history_path):
    """original_size_for_target()'s cumulative-drift baseline must never silently move
    forward just because pruning trimmed a target's oldest entries."""
    for size in [1000, 2000, 3000, 4000, 5000]:
        append_history("skill:foo", _result(score=0.9), path=history_path, content_size=size)

    prune_history(path=history_path, retention="1")

    import evaluate
    assert evaluate.original_size_for_target("skill:foo", path=history_path) == 1000


def test_last_passing_entry_survives_when_followed_by_pruned_failing_retries(history_path, monkeypatch):
    """The RegressionEvaluator hole: a passing baseline followed by several failing
    retries must not lose its baseline just because the retries are chronologically newer
    and the passing entry falls outside the count window."""
    import evaluate
    monkeypatch.setattr(evaluate, "get_history_path", lambda: history_path)

    append_history("skill:foo", _result(score=0.9, passed=True), path=history_path)
    for score in [0.1, 0.2, 0.3, 0.4]:
        append_history("skill:foo", _result(score=score, passed=False), path=history_path)

    prune_history(path=history_path, retention="1")

    # Confirmed against the evaluator itself, not just survivor counts -- this is the
    # behavior the anchor exists to preserve. RegressionEvaluator.evaluate() reads via
    # read_history()/get_history_path(), not a context-supplied path, hence the monkeypatch.
    result = RegressionEvaluator().evaluate(
        "irrelevant content", context={"target": "skill:foo", "new_score": 0.5})
    assert "no baseline" not in result.feedback.lower()


def _read_jsonl(path):
    import json
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# ── migrate_proposal_history() tests ───────────────────────────────────────

def test_migrate_proposal_history_moves_entries(history_path):
    """Happy path: entries keyed proposal:<id> are rewritten to skill:<name>."""
    append_history("proposal:abc-123", _result(score=0.5), path=history_path)
    append_history("proposal:abc-123", _result(score=0.7), path=history_path)
    append_history("proposal:other", _result(score=0.3), path=history_path)

    count = migrate_proposal_history("abc-123", "skill:my-skill", path=history_path)

    assert count == 2
    migrated = read_history("skill:my-skill", path=history_path)
    assert len(migrated) == 2
    assert [e["score"] for e in migrated] == [0.5, 0.7]
    # The other proposal's entry is untouched
    other = read_history("proposal:other", path=history_path)
    assert len(other) == 1
    # The old target is gone
    old = read_history("proposal:abc-123", path=history_path)
    assert len(old) == 0


def test_migrate_proposal_history_noop_when_no_entries(history_path):
    """Zero entries to migrate returns 0, writes nothing."""
    append_history("proposal:other", _result(score=0.3), path=history_path)

    count = migrate_proposal_history("abc-123", "skill:my-skill", path=history_path)

    assert count == 0
    assert not os.path.exists(get_history_archive_path(history_path))


def test_migrate_proposal_history_idempotent(history_path):
    """Calling twice does not duplicate entries."""
    append_history("proposal:abc-123", _result(score=0.5), path=history_path)

    count1 = migrate_proposal_history("abc-123", "skill:my-skill", path=history_path)
    count2 = migrate_proposal_history("abc-123", "skill:my-skill", path=history_path)

    assert count1 == 1
    assert count2 == 0  # second call finds nothing to migrate
    entries = read_history("skill:my-skill", path=history_path)
    assert len(entries) == 1


def test_migrate_proposal_history_archives_before_rewrite(history_path):
    """Original entries are appended to the archive file before the primary is touched."""
    append_history("proposal:abc-123", _result(score=0.5), path=history_path)
    append_history("proposal:abc-123", _result(score=0.7), path=history_path)

    migrate_proposal_history("abc-123", "skill:my-skill", path=history_path)

    archived = _read_jsonl(get_history_archive_path(history_path))
    assert len(archived) == 2
    assert [e["score"] for e in archived] == [0.5, 0.7]
    # Archived entries still carry the old target key
    assert all(e["target"] == "proposal:abc-123" for e in archived)


def test_migrate_proposal_history_merges_with_existing_skill_history(history_path):
    """When skill:<name> already has entries, migrated entries are appended after them."""
    append_history("proposal:abc-123", _result(score=0.7), path=history_path)
    append_history("skill:my-skill", _result(score=0.3), path=history_path)
    append_history("skill:my-skill", _result(score=0.4), path=history_path)

    migrate_proposal_history("abc-123", "skill:my-skill", path=history_path)

    entries = read_history("skill:my-skill", path=history_path)
    assert len(entries) == 3
    # Existing entries come first, migrated after
    assert [e["score"] for e in entries] == [0.3, 0.4, 0.7]


def test_migrate_proposal_history_preserves_other_targets(history_path):
    """Unrelated targets are not affected by the migration."""
    append_history("proposal:abc-123", _result(score=0.5), path=history_path)
    append_history("skill:unrelated", _result(score=0.9), path=history_path)

    migrate_proposal_history("abc-123", "skill:my-skill", path=history_path)

    unrelated = read_history("skill:unrelated", path=history_path)
    assert len(unrelated) == 1
    assert unrelated[0]["score"] == 0.9


def test_prune_keeps_the_entry_the_cumulative_baseline_is_read_from(tmp_path):
    """The earliest entry is often sizeless (a malformed, ambiguous or splice-refused
    attempt records no sizes). Protecting only it let a count prune drop the first *sized*
    entry and move original_size_for_target() from 1000 to 1200."""
    history = tmp_path / "h.jsonl"
    rows = [{"target": "skill:x", "timestamp": f"2026-10-0{i}T00:00:00+00:00", "passed": ok,
             "score": 1.0, **sizes}
            for i, (ok, sizes) in enumerate([
                (False, {}),
                (True, {"baseline_size": 1000, "content_size": 1100}),
                (True, {"baseline_size": 1100, "content_size": 1200}),
                (True, {"baseline_size": 1200, "content_size": 1300}),
            ], start=1)]
    history.write_text("".join(json.dumps(r) + "\n" for r in rows))

    evaluate.prune_history(path=str(history), retention="1",
                           archive_path=str(tmp_path / "archive.jsonl"))

    assert evaluate.original_size_for_target("skill:x", path=str(history)) == 1000


def test_prune_keeps_the_earliest_content_size_when_no_baseline_was_recorded(tmp_path):
    history = tmp_path / "h.jsonl"
    rows = [{"target": "skill:x", "timestamp": f"2026-10-0{i}T00:00:00+00:00", "passed": True,
             "score": 1.0, **sizes}
            for i, sizes in enumerate([{}, {"content_size": 500}, {"content_size": 600},
                                       {"content_size": 700}], start=1)]
    history.write_text("".join(json.dumps(r) + "\n" for r in rows))

    evaluate.prune_history(path=str(history), retention="1",
                           archive_path=str(tmp_path / "archive.jsonl"))

    assert evaluate.original_size_for_target("skill:x", path=str(history)) == 500
