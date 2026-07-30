"""Tests for prune_processed(): pruning the processed-session state file.

Real production growth: SESSION_QUERY caps each cron run at max_sessions (default 20), so
the file grows by at most ~20 entries/day under default settings -- slow, but genuinely
unbounded, since nothing ever removed an entry before this.

Pruning is real only for the flat {session_id: iso_timestamp} shape that is actually
deployed and growing in production. The documented {"processed_sessions": [...]} shape
carries no per-session timestamp and its list order isn't chronological either (built from
a Python set union in fetch_sessions()), so there is no temporal signal to prune by -- see
prune_processed()'s docstring. That's a clearly-warned no-op, not a silent gap.

state.py and fetch_sessions.py duplicate prune_processed() deliberately (matching the
existing convention for load_processed()/mark_processed(), asserted in
test_state_schema_compat.py) -- parametrized here for the same reason.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import fetch_sessions
import state

MODULES = [state, fetch_sessions]
IDS = ["state", "fetch_sessions"]


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = str(tmp_path / "skill_evolution_state.json")

    def use(module):
        monkeypatch.setattr(module, "STATE_FILE", path)
        return path

    use.path = path
    return use


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _write(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def _read(path):
    with open(path) as f:
        return json.load(f)


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_prune_by_count_keeps_most_recent_n_flat_dict(module, state_file):
    path = state_file(module)
    _write(path, {"a": _iso(5), "b": _iso(4), "c": _iso(3), "d": _iso(2), "e": _iso(1)})

    module.prune_processed(retention="2")

    assert set(_read(path)) == {"d", "e"}


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_prune_by_age_drops_entries_older_than_cutoff_flat_dict(module, state_file):
    path = state_file(module)
    _write(path, {"old1": _iso(100), "old2": _iso(90), "recent": _iso(1)})

    module.prune_processed(retention="30d")

    assert set(_read(path)) == {"recent"}


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_prune_never_drops_keep_ids_even_below_configured_limit(module, state_file):
    """The floor that closes the self-defeating-loop hole: whatever a run just marked
    processed must survive even if the configured retention would otherwise drop it --
    entries written in the same call share an identical timestamp, so a naive
    recency-sort has no tiebreak among them without this."""
    path = state_file(module)
    now = _iso(0)
    _write(path, {"old": _iso(10), "just_written_1": now, "just_written_2": now})

    module.prune_processed(retention="0", keep_ids=["just_written_1", "just_written_2"])

    assert set(_read(path)) == {"just_written_1", "just_written_2"}


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_prune_is_noop_when_retention_unset(module, state_file):
    path = state_file(module)
    original = {"a": _iso(500), "b": _iso(1)}
    _write(path, original)

    module.prune_processed()

    assert _read(path) == original


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_prune_is_noop_on_documented_shape_and_warns_every_call(module, state_file, capsys):
    path = state_file(module)
    documented = {"processed_sessions": ["aaa", "bbb"], "last_analyzed_at": _iso(0), "version": 1}
    _write(path, documented)

    module.prune_processed(retention="1")
    module.prune_processed(retention="1")

    assert _read(path) == documented  # byte-for-byte untouched
    err = capsys.readouterr().err
    assert err.count("warning:") == 2  # fires every call, not once-and-suppressed


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_prune_passes_through_last_analyzed_at_and_version_when_present_on_flat_shape(module, state_file):
    """load_processed()'s own exclusion list implies a flat-dict file can carry these two
    keys alongside session entries -- they must survive pruning untouched, not be treated
    as (or accidentally pruned as) session ids."""
    path = state_file(module)
    _write(path, {
        "last_analyzed_at": "2020-01-01T00:00:00+00:00",
        "version": 1,
        "old": _iso(500),
        "recent": _iso(1),
    })

    module.prune_processed(retention="30d")

    written = _read(path)
    assert written["last_analyzed_at"] == "2020-01-01T00:00:00+00:00"
    assert written["version"] == 1
    assert "old" not in written
    assert "recent" in written


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_prune_malformed_timestamps_treated_as_expired_with_capped_warning(module, state_file, capsys):
    path = state_file(module)
    data = {f"bad{i}": "not-a-timestamp" for i in range(8)}
    data["recent"] = _iso(1)
    _write(path, data)

    module.prune_processed(retention="30d")

    written = _read(path)
    assert set(written) == {"recent"}
    err = capsys.readouterr().err
    assert "8 state entries" in err
    assert "...and 3 more" in err  # first 5 named, rest counted


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_prune_retention_parsing_matches_evaluate_py_syntax(module, state_file):
    path = state_file(module)
    _write(path, {"old": _iso(100), "recent": _iso(1)})

    module.prune_processed(retention="90d")
    assert set(_read(path)) == {"recent"}

    _write(path, {"old": _iso(200), "recent": _iso(1)})
    module.prune_processed(retention="6mo")  # 180 days
    assert set(_read(path)) == {"recent"}

    _write(path, {"old": _iso(2), "recent": _iso(1)})
    module.prune_processed(retention="1")  # bare int -> count
    assert set(_read(path)) == {"recent"}


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_prune_with_negative_retention_still_respects_keep_ids(module, state_file):
    """A pathological input (e.g. an operator typo like "-5d") pushes the cutoff into the
    future, which would flag literally everything -- including this run's own writes -- as
    expired. keep_ids is what saves the just-written batch from that."""
    path = state_file(module)
    now = _iso(0)
    _write(path, {"old": _iso(10), "just_written": now})

    module.prune_processed(retention="-5d", keep_ids=["just_written"])

    assert "just_written" in _read(path)


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_prune_on_missing_file_is_a_noop(module, state_file):
    state_file(module)  # points STATE_FILE at a path that doesn't exist yet
    module.prune_processed(retention="1")  # must not raise or create the file
    assert not os.path.exists(state_file.path)


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_prune_only_touches_the_resolved_hosts_own_entries(module, state_file):
    """A shared state file can carry entries for more than one host (KTD5's prefix
    scheme). Pruning host="hermes" must never drop or even consider a "claude_code:"
    entry, and vice versa -- each host's retention window is independent."""
    path = state_file(module)
    cc_old_ts = "2020-01-01T00:00:00+00:00"
    cc_recent_ts = _iso(1)
    _write(path, {
        "hermes_old": _iso(100),          # legacy-bare -> belongs to hermes
        "claude_code:cc_old": cc_old_ts,  # belongs to claude_code -- also ancient
        "claude_code:cc_recent": cc_recent_ts,
    })

    module.prune_processed(retention="30d", host="hermes")

    written = _read(path)
    # hermes's own ancient entry is pruned...
    assert "hermes_old" not in written
    # ...but claude_code's entries -- ancient or not -- are left completely untouched,
    # since they're out of scope for a host="hermes" prune call.
    assert written["claude_code:cc_old"] == cc_old_ts
    assert written["claude_code:cc_recent"] == cc_recent_ts
