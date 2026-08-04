"""Tests for scripts/host.py's ClaudeCodeAdapter (U3).

All fixtures are hand-authored/synthetic, constructed at test time under pytest's
tmp_path -- nothing here reads or derives from the real ~/.claude/projects/ or
~/.claude/skills/ trees on this machine.

The JSONL record shape (type/sessionId/timestamp/message.content-as-block-list/etc.) is
built from the plan's description of what real Claude Code transcripts look like, not
from a live inspection of one -- see the final report for which fields are best-guess
(e.g. custom-title's exact title-bearing field name) and will need independent
verification against real session data.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

import fetch_sessions
import host


# ── Fixture helpers ──────────────────────────────────────────────────

def _write_jsonl(path, records):
    """Write a list of dicts (or raw strings, for malformed-line tests) as one JSONL file."""
    lines = []
    for r in records:
        if isinstance(r, str):
            lines.append(r)
        else:
            lines.append(json.dumps(r))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _session_file(tmp_path, project="proj-1", filename="sess-file.jsonl"):
    d = tmp_path / "projects" / project
    d.mkdir(parents=True, exist_ok=True)
    return d / filename


def _write_skill(root, skill_dir_name, name=None, description="does a thing"):
    skill_dir = root / "skills" / skill_dir_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_name = name if name is not None else skill_dir_name
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: {description}\n---\n\n# {skill_name}\n\nBody text.\n"
    )


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    """Point the adapter at an isolated synthetic ~/.claude-shaped tree instead of the
    real one, via the same call-time env-var override every other tunable in this repo
    uses (SKILL_EVOLUTION_CLAUDE_CODE_HOME)."""
    monkeypatch.setenv(host.CLAUDE_CODE_HOME_ENV_VAR, str(tmp_path))
    return tmp_path


def user_record(session_id, ts, text, extra=None):
    rec = {
        "type": "user",
        "sessionId": session_id,
        "timestamp": ts,
        "message": {"role": "user", "content": text},
    }
    if extra:
        rec.update(extra)
    return rec


def assistant_record(session_id, ts, text, model="claude-sonnet-5", content=None):
    return {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "model": model,
            "content": content if content is not None else text,
        },
    }


def custom_title_record(session_id, title):
    return {"type": "custom-title", "sessionId": session_id, "title": title}


def system_record(session_id, ts=None):
    rec = {"type": "system", "sessionId": session_id}
    if ts is not None:
        rec["timestamp"] = ts
    return rec


def mode_record(session_id, ts=None):
    rec = {"type": "mode", "sessionId": session_id}
    if ts is not None:
        rec["timestamp"] = ts
    return rec


# ── Scenario 1: happy path over a multi-record mixed fixture ────────

def test_happy_path_multi_record_fixture(claude_home):
    path = _session_file(claude_home)
    _write_jsonl(path, [
        system_record("sess-abc"),
        mode_record("sess-abc"),
        custom_title_record("sess-abc", "Debugging a flaky test"),
        user_record("sess-abc", "2026-07-20T10:00:00Z", "Can you help me debug this?"),
        assistant_record("sess-abc", "2026-07-20T10:00:05Z", "Sure, let's look at it."),
    ])

    adapter = host.ClaudeCodeAdapter()
    results = list(adapter.iter_sessions())

    assert len(results) == 1
    session = results[0]
    assert session["session_id"] == "sess-abc"
    assert session["title"] == "Debugging a flaky test"
    assert session["model"] == "claude-sonnet-5"
    # started_at is stored as an epoch float (matching Hermes's SQLite REAL type, R4),
    # not the raw ISO string a JSONL record carries.
    assert session["started_at"] == host._parse_claude_code_timestamp("2026-07-20T10:00:00Z")
    assert isinstance(session["started_at"], float)
    assert session["message_count"] == 2
    assert session["user_messages"] == 1
    assert session["assistant_messages"] == 1
    assert set(session.keys()) == {
        "session_id", "started_at", "title", "model", "source",
        "message_count", "user_messages", "assistant_messages", "messages",
    }


# ── Scenario 2: no custom-title -> fallback to first user message, truncated ─

def test_title_falls_back_to_first_user_message_when_no_custom_title(claude_home):
    path = _session_file(claude_home)
    long_text = "This is a fairly long opening user message that should get truncated " * 3
    assert len(long_text) > 100
    _write_jsonl(path, [
        user_record("sess-xyz", "2026-07-20T10:00:00Z", long_text),
        assistant_record("sess-xyz", "2026-07-20T10:00:05Z", "ok"),
    ])

    adapter = host.ClaudeCodeAdapter()
    session = list(adapter.iter_sessions())[0]

    assert session["title"] == long_text[:100]
    assert len(session["title"]) == 100


# ── Scenario 3: malformed JSON line mid-file is skipped, not fatal ──

def test_malformed_json_line_is_skipped_with_warning_rest_of_file_processed(claude_home, capsys):
    path = _session_file(claude_home)
    _write_jsonl(path, [
        user_record("sess-bad", "2026-07-20T10:00:00Z", "first message"),
        "{not valid json!!",
        assistant_record("sess-bad", "2026-07-20T10:00:05Z", "second message"),
    ])

    adapter = host.ClaudeCodeAdapter()
    results = list(adapter.iter_sessions())

    assert len(results) == 1
    session = results[0]
    assert session["message_count"] == 2
    assert session["user_messages"] == 1
    assert session["assistant_messages"] == 1

    err = capsys.readouterr().err
    assert "malformed" in err.lower() or "warning" in err.lower()


# ── Scenario 4: empty file yields nothing, not a crash ──────────────

def test_empty_file_yields_nothing(claude_home):
    path = _session_file(claude_home)
    path.write_text("", encoding="utf-8")

    adapter = host.ClaudeCodeAdapter()
    results = list(adapter.iter_sessions())  # must not raise

    assert results == []


# ── Scenario 5: only non-message records -> a session with zero messages ─

def test_session_with_only_non_message_records_yields_zero_message_session(claude_home):
    path = _session_file(claude_home)
    _write_jsonl(path, [
        system_record("sess-quiet", ts="2026-07-20T09:00:00Z"),
        mode_record("sess-quiet", ts="2026-07-20T09:00:01Z"),
    ])

    adapter = host.ClaudeCodeAdapter()
    results = list(adapter.iter_sessions())

    # A session is yielded (the file had real records) -- it is NOT treated as "no
    # session at all", which is reserved for a genuinely empty file (scenario 4). This
    # distinction matters because a file with only system/mode records still represents
    # a real recorded session (e.g. one where the user never sent a message), and
    # dropping it entirely would look identical to "this session never existed".
    assert len(results) == 1
    session = results[0]
    assert session["session_id"] == "sess-quiet"
    assert session["message_count"] == 0
    assert session["user_messages"] == 0
    assert session["assistant_messages"] == 0
    assert session["messages"] == []


# ── Scenario 6: system/mode interleaved with real messages ──────────

def test_system_and_mode_records_interleaved_are_dropped_without_keyerror(claude_home):
    path = _session_file(claude_home)
    _write_jsonl(path, [
        system_record("sess-mix", ts="2026-07-20T08:00:00Z"),
        user_record("sess-mix", "2026-07-20T08:00:01Z", "hi"),
        mode_record("sess-mix"),
        assistant_record("sess-mix", "2026-07-20T08:00:02Z", "hello"),
        system_record("sess-mix"),
        # A user-typed record with no "message" key at all -- must not raise KeyError.
        {"type": "user", "sessionId": "sess-mix", "timestamp": "2026-07-20T08:00:03Z"},
    ])

    adapter = host.ClaudeCodeAdapter()
    results = list(adapter.iter_sessions())  # must not raise KeyError

    assert len(results) == 1
    session = results[0]
    assert session["message_count"] == 3  # 2 user (one with no message key) + 1 assistant
    assert session["user_messages"] == 2
    assert session["assistant_messages"] == 1


# ── Scenario 7: message.content as a block list (text/thinking/tool_use/tool_result) ─

def test_content_block_list_flattens_text_only_no_attribute_error(claude_home):
    path = _session_file(claude_home)
    _write_jsonl(path, [
        user_record("sess-blocks", "2026-07-20T07:00:00Z", "plain string content works too"),
        assistant_record(
            "sess-blocks", "2026-07-20T07:00:01Z", text=None,
            content=[
                {"type": "thinking", "text": "internal reasoning nobody should see"},
                {"type": "text", "text": "Here is the answer: "},
                {"type": "tool_use", "name": "bash", "input": {"command": "rm -rf /tmp/x"}},
                {"type": "text", "text": "done."},
                {"type": "tool_result", "content": "tool output blob"},
            ],
        ),
    ])

    adapter = host.ClaudeCodeAdapter()
    results = list(adapter.iter_sessions())  # must not raise AttributeError

    assert len(results) == 1
    session = results[0]
    all_previews = " ".join(m["content_preview"] for m in session["messages"])

    assert "Here is the answer:" in all_previews
    assert "done." in all_previews
    # thinking/tool_use/tool_result content must never appear anywhere in the output.
    assert "internal reasoning" not in all_previews
    assert "rm -rf" not in all_previews
    assert "tool output blob" not in all_previews


# ── Scenario 8: leading records lack a timestamp ────────────────────

def test_started_at_resolves_from_first_timestamped_record_not_record_zero(claude_home):
    path = _session_file(claude_home)
    _write_jsonl(path, [
        mode_record("sess-late-ts"),           # no timestamp
        custom_title_record("sess-late-ts", "a title"),  # no timestamp
        {"type": "system", "sessionId": "sess-late-ts"},  # no timestamp
        user_record("sess-late-ts", "2026-07-21T12:00:00Z", "hello"),
        assistant_record("sess-late-ts", "2026-07-21T12:00:01Z", "hi"),
    ])

    adapter = host.ClaudeCodeAdapter()
    session = list(adapter.iter_sessions())[0]

    assert session["started_at"] == host._parse_claude_code_timestamp("2026-07-21T12:00:00Z")


# ── Scenario 9: secret dropped, email masked, via the shared summarizer ─

def test_secret_message_dropped_and_pii_masked_via_shared_summarizer(claude_home):
    path = _session_file(claude_home)
    secret_text = "here is my key sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"
    assert fetch_sessions.contains_secret(secret_text)
    email_text = "reach me at carlo0071@example.com about this bug"

    _write_jsonl(path, [
        user_record("sess-secret", "2026-07-20T06:00:00Z", secret_text),
        assistant_record("sess-secret", "2026-07-20T06:00:01Z", email_text),
    ])

    adapter = host.ClaudeCodeAdapter()
    session = list(adapter.iter_sessions())[0]

    # The secret-carrying message is dropped entirely by _summarize_messages().
    assert session["message_count"] == 2  # both raw messages counted before redaction
    previews = [m["content_preview"] for m in session["messages"]]
    assert not any("sk-ant-api" in p for p in previews)
    assert any("[PII:email]" in p for p in previews)
    assert not any("carlo0071@example.com" in p for p in previews)


# ── Scenario 10: fallback title itself containing a secret/PII ──────

def test_fallback_title_never_stores_raw_secret_or_pii(claude_home):
    path = _session_file(claude_home)
    secret_text = "my token is ghp_abcdefghijklmnopqrstuvwxyz012345"
    assert fetch_sessions.contains_secret(secret_text)

    _write_jsonl(path, [
        user_record("sess-title-secret", "2026-07-20T05:00:00Z", secret_text),
    ])

    adapter = host.ClaudeCodeAdapter()
    session = list(adapter.iter_sessions())[0]

    assert "ghp_" not in session["title"]

    # A separate session whose fallback-title source contains PII, not a secret.
    path2 = _session_file(claude_home, filename="sess2.jsonl")
    pii_text = "email me at carlo0071@example.com please"
    _write_jsonl(path2, [
        user_record("sess-title-pii", "2026-07-20T05:00:00Z", pii_text),
    ])

    session2 = [s for s in adapter.iter_sessions() if s["session_id"] == "sess-title-pii"][0]
    assert "carlo0071@example.com" not in session2["title"]


# ── Scenario 10b: a custom-title record itself containing a secret/PII ──
# Regression test for a P0 finding (security review): title_from_custom used to be
# stored verbatim, bypassing the same redaction the fallback-title path already applies.

def test_custom_title_record_never_stores_raw_secret_or_pii(claude_home):
    path = _session_file(claude_home)
    secret_text = "my token is ghp_abcdefghijklmnopqrstuvwxyz012345"
    assert fetch_sessions.contains_secret(secret_text)

    _write_jsonl(path, [
        custom_title_record("sess-ct-secret", secret_text),
        user_record("sess-ct-secret", "2026-07-20T05:00:00Z", "hello"),
    ])

    adapter = host.ClaudeCodeAdapter()
    session = list(adapter.iter_sessions())[0]

    assert "ghp_" not in session["title"]

    path2 = _session_file(claude_home, filename="sess2.jsonl")
    pii_text = "email me at carlo0071@example.com please"
    _write_jsonl(path2, [
        custom_title_record("sess-ct-pii", pii_text),
        user_record("sess-ct-pii", "2026-07-20T05:00:00Z", "hello"),
    ])

    session2 = [s for s in adapter.iter_sessions() if s["session_id"] == "sess-ct-pii"][0]
    assert "carlo0071@example.com" not in session2["title"]
    assert "[PII:email]" in session2["title"]


# ── Scenario 11: iter_skills() dot-prefix filtering + category stamping ─

def test_iter_skills_skips_dot_prefixed_dir_and_stamps_user_category(claude_home):
    _write_skill(claude_home, "money-admin-messaging")
    _write_skill(claude_home, ".archive")

    adapter = host.ClaudeCodeAdapter()
    found = adapter.iter_skills()

    assert len(found) == 1
    skill = found[0]
    assert skill["name"] == "money-admin-messaging"
    assert skill["category"] == "user"
    assert set(skill.keys()) == {"name", "category", "description", "path", "size"}
    assert isinstance(skill["size"], int)
    assert isinstance(skill["path"], str)


# ── Scenario 12: read_skill_body() match / no-match / ambiguous ─────

def test_read_skill_body_match_no_match_and_ambiguous(claude_home):
    _write_skill(claude_home, "deploy-helper")

    adapter = host.ClaudeCodeAdapter()

    body = adapter.read_skill_body("deploy-helper")
    assert body is not None
    assert "Body text." in body

    assert adapter.read_skill_body("does-not-exist") is None


def test_read_skill_body_ambiguous_match_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv(host.CLAUDE_CODE_HOME_ENV_VAR, str(tmp_path))
    # Two skill directories that both parse to the same declared `name` in frontmatter
    # (not just the same directory name) -- an ambiguous match by the value iter_skills()
    # actually keys on.
    _write_skill(tmp_path, "dup-skill-a", name="dup-skill")
    _write_skill(tmp_path, "dup-skill-b", name="dup-skill")

    adapter = host.ClaudeCodeAdapter()
    assert adapter.read_skill_body("dup-skill") is None


# ── Registry wiring ──────────────────────────────────────────────────

def test_claude_code_adapter_registered_under_claude_code_name():
    assert isinstance(host.HOST_ADAPTERS["claude_code"], host.ClaudeCodeAdapter)


def test_get_adapter_resolves_claude_code_via_env_var(monkeypatch):
    monkeypatch.setenv(host.HOST_ENV_VAR, "claude_code")
    adapter = host.get_adapter()
    assert isinstance(adapter, host.ClaudeCodeAdapter)
    assert adapter.name == "claude_code"


# ── Bonus: since-based filtering (both pre-filter layers) ───────────

def test_since_excludes_a_session_entirely_out_of_window(claude_home):
    import time
    from datetime import datetime, timedelta, timezone

    old_path = _session_file(claude_home, filename="old.jsonl")
    _write_jsonl(old_path, [
        user_record("sess-old", "2020-01-01T00:00:00Z", "ancient message"),
    ])
    old_mtime = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old_path, (old_mtime, old_mtime))

    recent_path = _session_file(claude_home, filename="recent.jsonl")
    now = datetime.now(timezone.utc)
    _write_jsonl(recent_path, [
        user_record("sess-recent", now.isoformat().replace("+00:00", "Z"), "recent message"),
    ])

    adapter = host.ClaudeCodeAdapter()
    since = now - timedelta(hours=1)
    results = list(adapter.iter_sessions(since=since))

    assert {s["session_id"] for s in results} == {"sess-recent"}


def test_since_none_is_unbounded(claude_home):
    from datetime import datetime, timezone

    old_path = _session_file(claude_home, filename="ancient.jsonl")
    _write_jsonl(old_path, [
        user_record("sess-ancient", "1999-01-01T00:00:00Z", "very old message"),
    ])
    old_mtime = datetime(1999, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old_path, (old_mtime, old_mtime))

    adapter = host.ClaudeCodeAdapter()
    results = list(adapter.iter_sessions(since=None))

    assert {s["session_id"] for s in results} == {"sess-ancient"}


def test_since_ignores_a_stray_old_timestamp_on_a_leading_non_message_record(claude_home):
    """Regression test for a P1 finding (adversarial review): a `system`/`mode` record
    can legitimately carry its own `timestamp` field (verified against real session
    data), and if it happens to predate the `since` window while the actual
    conversation is recent, the since-filter must not use that housekeeping record's
    timestamp to drop the whole session."""
    from datetime import datetime, timedelta, timezone

    path = _session_file(claude_home)
    now = datetime.now(timezone.utc)
    recent_iso = now.isoformat().replace("+00:00", "Z")
    _write_jsonl(path, [
        system_record("sess-stray-old-ts", ts="2020-01-01T00:00:00Z"),  # old, non-message
        mode_record("sess-stray-old-ts", ts="2020-01-01T00:00:01Z"),    # old, non-message
        user_record("sess-stray-old-ts", recent_iso, "recent message"),
        assistant_record("sess-stray-old-ts", recent_iso, "recent reply"),
    ])

    adapter = host.ClaudeCodeAdapter()
    since = now - timedelta(hours=1)
    results = list(adapter.iter_sessions(since=since))

    assert {s["session_id"] for s in results} == {"sess-stray-old-ts"}
    # started_at metadata may still reflect the earliest timestamp seen (the system
    # record's), but the since-filter itself must have keyed off the message timestamp.
    session = results[0]
    assert session["message_count"] == 2


# ── Additional branch coverage (testing review, P3) ─────────────────

def test_numeric_timestamp_is_parsed_like_an_iso_string(claude_home):
    """_parse_claude_code_timestamp() accepts a numeric epoch too -- exercise that
    branch through a real record, not just the helper in isolation."""
    path = _session_file(claude_home)
    _write_jsonl(path, [
        user_record("sess-numeric-ts", 1784541600, "hello"),  # epoch seconds, not a string
    ])

    adapter = host.ClaudeCodeAdapter()
    session = list(adapter.iter_sessions())[0]

    assert session["started_at"] == 1784541600.0
    assert isinstance(session["started_at"], float)


def test_session_id_falls_back_to_filename_stem_when_no_record_has_one(claude_home):
    path = _session_file(claude_home, filename="fallback-session-id.jsonl")
    _write_jsonl(path, [
        {"type": "user", "timestamp": "2026-07-20T10:00:00Z",
         "message": {"role": "user", "content": "hello, no sessionId anywhere"}},
    ])

    adapter = host.ClaudeCodeAdapter()
    session = list(adapter.iter_sessions())[0]

    assert session["session_id"] == "fallback-session-id"


def test_source_is_extracted_from_an_entrypoint_field(claude_home):
    path = _session_file(claude_home)
    _write_jsonl(path, [
        user_record("sess-entrypoint", "2026-07-20T10:00:00Z", "hi",
                    extra={"entrypoint": "claude-desktop"}),
    ])

    adapter = host.ClaudeCodeAdapter()
    session = list(adapter.iter_sessions())[0]

    assert session["source"] == "claude-desktop"


def test_iter_sessions_and_iter_skills_return_empty_when_directories_are_missing(claude_home):
    """claude_home fixture points SKILL_EVOLUTION_CLAUDE_CODE_HOME at an empty tmp_path
    with neither skills/ nor projects/ created -- both methods must degrade to an empty
    result, not raise."""
    adapter = host.ClaudeCodeAdapter()

    assert list(adapter.iter_sessions()) == []
    assert adapter.iter_skills() == []
