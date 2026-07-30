"""Processed-session state file compatibility.

The real ~/.hermes/skill_evolution_state.json is a flat {session_id: iso_timestamp}
dict with 101 entries, written by the already-deployed pipeline. Both this repo's
state.py and fetch_sessions.py instead expect
{"processed_sessions": [...], "last_analyzed_at": ..., "version": 1} -- the shape the
deployed SKILL.md also documents. Reality matches neither.

Consequences before this was handled: load_processed() returned [] against the real
file, so dedup was dead and every session looked unprocessed on every run; and
mark_processed() would have overwritten 101 entries of another producer's state.

state.py and fetch_sessions.py deliberately duplicate this logic (each script is a
standalone stdlib CLI), so both are asserted here to keep them from drifting.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import fetch_sessions
import state

MODULES = [state, fetch_sessions]
IDS = ["state", "fetch_sessions"]

LEGACY = {
    "20260714_130038_f8f8da": "2026-07-20T18:23:32.798884+00:00",
    "cron_c8db388257fc_20260714_020009": "2026-07-20T18:23:32.798884+00:00",
}
DOCUMENTED = {
    "processed_sessions": ["aaa", "bbb"],
    "last_analyzed_at": "2026-07-20T02:00:00-05:00",
    "version": 1,
}


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = str(tmp_path / "skill_evolution_state.json")

    def use(module):
        monkeypatch.setattr(module, "STATE_FILE", path)
        return path

    use.path = path
    return use


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_reads_the_documented_shape(module, state_file):
    path = state_file(module)
    with open(path, "w") as f:
        json.dump(DOCUMENTED, f)

    assert set(module.load_processed()) == {"aaa", "bbb"}


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_reads_the_real_flat_dict_shape(module, state_file):
    """The shape actually on disk today -- previously silently read as empty."""
    path = state_file(module)
    with open(path, "w") as f:
        json.dump(LEGACY, f)

    assert set(module.load_processed()) == set(LEGACY)


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_missing_file_yields_empty(module, state_file):
    state_file(module)
    assert module.load_processed() == []


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_corrupt_file_yields_empty_without_raising(module, state_file):
    path = state_file(module)
    with open(path, "w") as f:
        f.write("{not json")

    assert module.load_processed() == []


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_marking_preserves_the_existing_flat_dict_shape(module, state_file):
    """Never clobber another producer's file into a different schema.

    Pre-existing legacy (un-prefixed) entries are left exactly as-is -- mark_processed()
    never touches or reformats them. A genuinely new id, though, is written under the
    "<host>:<id>" key (KTD5's host-prefix scheme; here host defaults to "hermes" since no
    SKILL_EVOLUTION_HOST is set) rather than bare, which is why "new_session_1" shows up
    on disk as "hermes:new_session_1" and load_processed() is what strips that back off.
    """
    path = state_file(module)
    with open(path, "w") as f:
        json.dump(LEGACY, f)

    module.mark_processed(list(LEGACY) + ["new_session_1"])

    with open(path) as f:
        written = json.load(f)
    assert "processed_sessions" not in written, "flat-dict file was rewritten in the other shape"
    assert set(written) == set(LEGACY) | {"hermes:new_session_1"}
    # pre-existing timestamps must survive untouched, under their original (bare) keys
    for k, v in LEGACY.items():
        assert written[k] == v
    # the read side transparently reunifies legacy-bare and host-prefixed entries
    assert set(module.load_processed()) == set(LEGACY) | {"new_session_1"}


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_marking_keeps_documented_shape_when_that_is_what_exists(module, state_file):
    path = state_file(module)
    with open(path, "w") as f:
        json.dump(DOCUMENTED, f)

    module.mark_processed(["aaa", "bbb", "ccc"])

    with open(path) as f:
        written = json.load(f)
    assert set(written["processed_sessions"]) == {"aaa", "bbb", "ccc"}
    assert written["version"] == 1


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_marking_a_fresh_file_uses_the_documented_shape(module, state_file):
    state_file(module)
    module.mark_processed(["s1"])

    with open(state_file.path) as f:
        written = json.load(f)
    assert written["processed_sessions"] == ["s1"]
    assert written["version"] == 1


def test_both_modules_agree_on_the_state_path():
    assert state.STATE_FILE == fetch_sessions.STATE_FILE


# ── Host-prefix separation (KTD5) ────────────────────────────────────────
#
# The real deployed state file has 101 legacy entries with no host prefix at all,
# written before any host concept existed. load_processed(host="hermes") must keep
# reading them with zero migration; a different host must not pick them up.

@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_legacy_unprefixed_entries_belong_to_hermes_only(module, state_file):
    path = state_file(module)
    with open(path, "w") as f:
        json.dump(LEGACY, f)

    assert set(module.load_processed(host="hermes")) == set(LEGACY)
    assert set(module.load_processed(host="claude_code")) == set()


@pytest.mark.parametrize("module", MODULES, ids=IDS)
def test_mark_processed_writes_a_host_prefixed_key_for_a_non_default_host(module, state_file):
    path = state_file(module)
    with open(path, "w") as f:
        json.dump(LEGACY, f)

    module.mark_processed(["new_for_claude_code"], host="claude_code")

    with open(path) as f:
        written = json.load(f)
    assert "claude_code:new_for_claude_code" in written
    # the hermes-side legacy entries are untouched and still invisible to claude_code
    assert set(module.load_processed(host="claude_code")) == {"new_for_claude_code"}
    assert set(module.load_processed(host="hermes")) == set(LEGACY)
