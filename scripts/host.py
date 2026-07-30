#!/usr/bin/env python3
"""Host adapter seam: one selector between the pipeline and whichever agent host it
reads sessions/skills from.

The pipeline's Hermes coupling was previously spread across four modules (see
CLAUDE.md's "Conventions specific to this codebase"). This module is the first of
those four seams to gain a real abstraction: a `HostAdapter` ABC plus a registry,
resolved through one function reading one env var -- the same shape this repo has
already settled on twice (`REGISTRY`/`register_evaluator()` and `PROVIDER_CALLERS`/
`resolve_provider()` in evaluate.py). A third instance of a pattern used twice is the
conservative choice, not a new abstraction.

This unit (U1) only stands the seam up and puts `HermesAdapter` behind it, reproducing
today's behaviour byte-for-byte by delegating to the existing `fetch_sessions.py` and
`skill_index.py` functions -- it does not change any existing caller. Wiring the rest of
the codebase to go through `get_adapter()` instead of importing those modules directly
is a later unit's job, as is a second adapter for a non-Hermes host.
"""

import json
import os
import sys
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import fetch_sessions
import skill_index


# ── Adapter interface ────────────────────────────────────────────────

class HostAdapter(ABC):
    """Base interface every host adapter implements.

    Three methods are enough to cover every skill/session read in the codebase (R3):
    `iter_sessions()` for the analyzer's session evidence, `iter_skills()` for "what
    skills currently exist", and `read_skill_body()` for a single skill's installed
    text (used by the evaluation gate's baseline-size lookup, see
    evaluate.installed_skill_body()). No module outside an adapter should construct a
    host-specific path once every caller is wired through here (U2's job, not this
    unit's).
    """

    name: str = ""

    @abstractmethod
    def iter_sessions(self, since: Optional[datetime] = None) -> Iterable[Dict[str, Any]]:
        """Yield/return this host's session dicts, optionally bounded to `since`.

        `since=None` means "no lower bound" -- as much history as the host can supply,
        not "use whatever this host's own default window is".
        """
        raise NotImplementedError

    @abstractmethod
    def iter_skills(self) -> List[Dict[str, Any]]:
        """Return this host's installed-skill index: name/category/description/path/size."""
        raise NotImplementedError

    def read_skill_body(self, skill_name: str) -> Optional[str]:
        """Best-effort text of `skill_name`'s currently installed body, or None.

        None on no match, an ambiguous match, or a read failure -- never raise. Callers
        (e.g. the evaluation gate's baseline-size check) degrade to whatever baseline
        they already had rather than failing a gate decision on an unrelated lookup.

        Concrete (not abstract): every host stores skills as a name/path pair from
        iter_skills(), so "find the one match, open it, return None on any failure" is
        the same lookup for any host. A subclass overrides this only if its storage
        can't answer "read this path as text" (e.g. a future host backed by a database
        or an API rather than a filesystem).
        """
        matches = [s for s in self.iter_skills() if s["name"] == skill_name]
        if len(matches) != 1:
            return None
        try:
            with open(matches[0]["path"], encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None


# ── Registry (plain name -> instance dict, no dynamic discovery) ────
# Instances, not classes: unlike evaluate.REGISTRY (which stores Evaluator subclasses
# and instantiates one per resolved name), a host adapter carries no per-call state, so
# there is nothing gained by re-instantiating on every get_adapter() call.

HOST_ADAPTERS: Dict[str, HostAdapter] = {}

HOST_ENV_VAR = "SKILL_EVOLUTION_HOST"
DEFAULT_HOST = "hermes"


def register_adapter(name: str, adapter: HostAdapter) -> None:
    """Register a host adapter instance under `name` in the module registry."""
    HOST_ADAPTERS[name] = adapter


def resolve_host(host: Optional[str] = None) -> str:
    """Resolve the active host name: explicit arg > SKILL_EVOLUTION_HOST > default.

    Mirrors evaluate.resolve_provider()'s resolution order exactly.
    """
    if host:
        return host
    return os.environ.get(HOST_ENV_VAR, DEFAULT_HOST)


def get_adapter(host: Optional[str] = None) -> HostAdapter:
    """Resolve and return the active HostAdapter.

    Raises ValueError for an unknown host name, listing the registered names --
    mirrors evaluate.call_provider()'s validation and message for an unknown provider.
    Fail-loud on purpose: a typo'd host that quietly reads the wrong tree is worse than
    a crash.
    """
    resolved = resolve_host(host)
    if resolved not in HOST_ADAPTERS:
        raise ValueError(
            f"Unknown host '{resolved}'. Available: {', '.join(sorted(HOST_ADAPTERS)) or '(none registered)'}"
        )
    return HOST_ADAPTERS[resolved]


# ── Hermes adapter ───────────────────────────────────────────────────

# "No `since` given" means "as much history as this host can supply", not "apply
# fetch_sessions()'s own DEFAULT_LOOKBACK_HOURS default". fetch_sessions() has no
# unbounded mode of its own (it always subtracts lookback_hours*3600 from now()), so an
# effectively-unbounded lookback is expressed as a very large number of hours (~100
# years) rather than changing fetch_sessions()'s signature or behavior.
_UNBOUNDED_LOOKBACK_HOURS = 24 * 365 * 100


class HermesAdapter(HostAdapter):
    """Reproduces today's Hermes-coupled behaviour byte-for-byte (R2), just behind the
    adapter seam. Every method delegates to the existing implementation rather than
    duplicating logic:

    - iter_sessions() -> fetch_sessions.fetch_sessions(dry_run=True) -- read-only, so
      this adapter never marks a session processed or otherwise mutates state as a side
      effect of being asked to iterate.
    - iter_skills() -> skill_index.scan_skills()
    - read_skill_body() -> the same filter-then-read logic evaluate.installed_skill_body()
      already implements inline, so both call sites behave identically until a later
      unit rewires installed_skill_body() to call this adapter instead.
    """

    name = "hermes"

    def iter_sessions(self, since: Optional[datetime] = None) -> Iterable[Dict[str, Any]]:
        if since is None:
            lookback_hours: float = _UNBOUNDED_LOOKBACK_HOURS
        else:
            now = datetime.now(since.tzinfo) if since.tzinfo is not None else datetime.now()
            lookback_hours = max((now - since).total_seconds() / 3600, 0)

        return fetch_sessions.fetch_sessions(
            db_path=fetch_sessions.get_state_db_path(),
            lookback_hours=lookback_hours,
            dry_run=True,
            # Pass this adapter's own identity explicitly rather than letting
            # fetch_sessions() re-derive it from the ambient env var: get_adapter("hermes")
            # can be requested explicitly while SKILL_EVOLUTION_HOST names a different
            # host, and the processed-state namespace must follow the resolved adapter,
            # not whatever the env var says right now.
            host=self.name,
        )

    def iter_skills(self) -> List[Dict[str, Any]]:
        return skill_index.scan_skills()

    # read_skill_body() uses HostAdapter's concrete implementation: it calls
    # self.iter_skills() above, which calls skill_index.scan_skills() as an attribute,
    # so a test that monkeypatches skill_index.scan_skills is still observed.


register_adapter("hermes", HermesAdapter())


# ── Claude Code adapter (U3) ─────────────────────────────────────────
# Reads sessions/skills from Claude Code's on-disk layout instead of Hermes's SQLite
# state.db + ~/.hermes/skills/<category>/<skill>/ tree:
#   - skills:  ~/.claude/skills/*/SKILL.md          (flat -- no category level)
#   - sessions: ~/.claude/projects/*/*.jsonl         (one JSONL file per session)
#
# The JSONL record format is undocumented and versioned, so every field extraction below
# is best-effort and defensive by construction (allowlist role mapping, missing-field
# fallbacks, never raising on a malformed line) rather than assuming a fixed schema.

CLAUDE_CODE_HOME_ENV_VAR = "SKILL_EVOLUTION_CLAUDE_CODE_HOME"

# How much of a fallback-derived title (first user message, no custom-title record) to
# keep after redaction -- mirrors fetch_sessions._summarize_messages()'s content_preview
# cap in spirit, just much shorter since a title is a one-line label, not evidence text.
TITLE_FALLBACK_MAX_CHARS = 100


def _default_claude_code_home() -> str:
    """`~/.claude`, resolved at call time (not baked into a module constant at import) so
    a test's monkeypatched HOME env var is honoured on every call, the same way
    fetch_sessions.get_state_file()/get_state_db_path() re-read their env var at call
    time rather than freezing it at import."""
    return os.path.expanduser("~/.claude")


def _resolve_claude_code_home() -> str:
    """Resolve the Claude Code home directory: SKILL_EVOLUTION_CLAUDE_CODE_HOME > `~/.claude`.

    Same call-time-env-read shape as fetch_sessions.get_state_file(): read at call time,
    blank ignored, so a test or an isolated run can redirect this adapter's root without
    monkeypatching HOME itself.
    """
    return os.environ.get(CLAUDE_CODE_HOME_ENV_VAR, "").strip() or _default_claude_code_home()


def _parse_claude_code_timestamp(value: Any) -> Optional[float]:
    """Best-effort epoch-seconds parse of a Claude Code record's `timestamp` field.

    Accepts a numeric epoch or an ISO-8601 string (normalizing a trailing `Z`, which
    datetime.fromisoformat() does not accept on Python versions before 3.11). Returns
    None on anything unparseable -- callers must treat that as "unknown", not "too old":
    excluding a session because its timestamp failed to parse would silently drop real
    history, which is worse than occasionally not pre-filtering it.
    """
    if isinstance(value, bool):
        return None  # bool is an int subclass; not a plausible timestamp
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(v).timestamp()
        except ValueError:
            return None
    return None


def _flatten_claude_code_content(content: Any) -> str:
    """Flatten a Claude Code message's `content` into a plain string.

    `content` is very often a list of typed blocks (`text`, `thinking`, `tool_use`,
    `tool_result`), not a plain string -- this is the common case in real transcripts,
    not an edge case. Only `text` blocks contribute to the result; `thinking`, `tool_use`,
    and `tool_result` block content is dropped entirely -- never folded into the
    concatenated text, never stashed anywhere else in the output either. A `content` that
    is already a plain string passes through unchanged.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if text:
                    parts.append(text)
        return "".join(parts)
    return ""


def _role_for_claude_code_type(record_type: Any) -> Optional[str]:
    """Map a record's `type` to a message role, by ALLOWLIST.

    Only "user" and "assistant" map to a message. Every other type seen in practice
    (`attachment`, `custom-title`, `last-prompt`, `queue-operation`, `system`, `mode`) --
    and any future/undocumented type this format grows -- maps to None (not a message) by
    simply not appearing on this list. A denylist of known-bad types would misparse any
    new type as a message the moment the format adds one; an allowlist cannot.
    """
    if record_type == "user":
        return "user"
    if record_type == "assistant":
        return "assistant"
    return None


class ClaudeCodeAdapter(HostAdapter):
    """Reads sessions/skills from Claude Code's on-disk layout (`~/.claude/`).

    Both iter_sessions() and iter_skills() route through the shared redaction chokepoint
    (fetch_sessions.contains_secret()/redact_pii()/_summarize_messages()) rather than
    reimplementing any of it -- see CLAUDE.md's "Secrets and PII are handled differently,
    on purpose" for why a duplicate implementation here would be a correctness risk, not
    just style debt.
    """

    name = "claude_code"

    # ── skills: ~/.claude/skills/*/SKILL.md (flat, no category level) ──

    def iter_skills(self) -> List[Dict[str, Any]]:
        base = Path(_resolve_claude_code_home()) / "skills"
        if not base.exists():
            return []

        results = []
        for skill_dir in sorted(base.iterdir()):
            if not skill_dir.is_dir():
                continue
            # Path.iterdir()/glob() do not exclude dot-prefixed entries the way a shell
            # glob does. skill_index.scan_skills() filters dot-prefixed dirs at the
            # *category* level for Hermes (.archive/, .curator_backups/, .hub/); this
            # host has no category level, so the equivalent filter is re-applied one
            # level down, at the skill-directory level.
            if skill_dir.name.startswith("."):
                continue

            # skill_index.build_skill_record() covers "no SKILL.md here"
            # (FileNotFoundError, an OSError subclass) and any other read failure by
            # returning None -- the same record shape scan_skills() builds for Hermes,
            # just with a constant category instead of a derived one: this host has no
            # real category level today. "user" distinguishes ~/.claude/skills/ from a
            # future project-level .claude/skills/ tree, out of scope for this unit.
            record = skill_index.build_skill_record(skill_dir / "SKILL.md", category="user")
            if record is not None:
                results.append(record)

        return results

    # read_skill_body() uses HostAdapter's concrete implementation.

    # ── sessions: ~/.claude/projects/*/*.jsonl (one file per session) ──

    def iter_sessions(self, since: Optional[datetime] = None) -> Iterable[Dict[str, Any]]:
        since_epoch = since.timestamp() if since is not None else None
        projects_dir = Path(_resolve_claude_code_home()) / "projects"
        if not projects_dir.exists():
            return []

        results = []
        for jsonl_path in sorted(projects_dir.glob("*/*.jsonl")):
            if since_epoch is not None:
                # Cheapest possible pre-filter, before opening the file at all: a
                # session's JSONL file is only ever appended to as the session
                # progresses, so its mtime is always >= every record's timestamp inside
                # it. If the file hasn't been touched since before the cutoff, nothing
                # inside it can be newer than the cutoff either.
                try:
                    if jsonl_path.stat().st_mtime < since_epoch:
                        continue
                except OSError:
                    pass  # can't stat it; fall through and let the real parse decide

            try:
                session = self._parse_session_file(jsonl_path, since_epoch)
            except OSError as e:
                print(f"warning: could not read {jsonl_path}: {e}", file=sys.stderr)
                continue
            if session is not None:
                results.append(session)

        return results

    def _parse_session_file(
        self, path: Path, since_epoch: Optional[float]
    ) -> Optional[Dict[str, Any]]:
        """Parse one session's JSONL file into a session dict, or None to signal "skip
        this file entirely" -- either it had zero parseable records (a genuinely empty
        file corresponds to no session ever having happened, not a zero-message one), or
        its resolved started_at fell outside `since_epoch` (the second, finer-grained
        pre-filter layer -- see iter_sessions()'s mtime check for the first).
        """
        session_id = None
        started_at = None
        title_from_custom = None
        model = ""
        source = ""
        messages: List[Dict[str, str]] = []
        first_user_content = None
        saw_any_record = False
        since_check_done = False

        for record in self._iter_jsonl_records(path):
            saw_any_record = True
            if not isinstance(record, dict):
                continue

            rtype = record.get("type")
            role = _role_for_claude_code_type(rtype)

            if session_id is None:
                sid = record.get("sessionId")
                if sid:
                    session_id = sid

            if started_at is None and "timestamp" in record:
                # Scan forward past leading records that lack a timestamp (small
                # records like custom-title/mode commonly come first) -- this is the
                # first record that actually HAS one, not literally record zero.
                # Stored as an epoch float, not the raw ISO string: R4 requires
                # iter_sessions() output to match Hermes in *type*, not just key set, and
                # Hermes's started_at is a SQLite REAL (a Python float). A timestamp that
                # fails to parse falls back to 0.0 rather than leaving started_at as a
                # differently-typed string.
                started_at = _parse_claude_code_timestamp(record["timestamp"]) or 0.0

            # The since-window decision is gated on the first MESSAGE-bearing record's
            # own timestamp, deliberately independent of `started_at` above. A non-message
            # record (system/mode/etc.) can carry an unrelated timestamp -- e.g. a hook
            # logged before the conversation actually started -- and real session files
            # show `system` records with a `timestamp` field. Gating the early-return on
            # that timestamp would silently drop a real, in-window session because of a
            # housekeeping record's date, not the conversation's.
            if not since_check_done and role is not None and "timestamp" in record:
                since_check_done = True
                epoch = _parse_claude_code_timestamp(record["timestamp"])
                # Mirrors the SQL path filtering by started_at before ever fetching
                # message bodies: stop reading the rest of this file as soon as we know
                # it's out of window, rather than parsing every remaining message first.
                if since_epoch is not None and epoch is not None and epoch < since_epoch:
                    return None

            if rtype == "custom-title" and title_from_custom is None:
                candidate = (
                    record.get("title")
                    or record.get("customTitle")
                    or record.get("value")
                )
                if candidate:
                    title_from_custom = str(candidate)

            if not source:
                entrypoint = record.get("entrypoint")
                if entrypoint:
                    source = str(entrypoint)

            if role is None:
                continue  # not a message: attachment/custom-title/system/mode/etc.

            message = record.get("message")
            if not isinstance(message, dict):
                message = {}

            if role == "assistant" and not model:
                m = message.get("model")
                if m:
                    model = str(m)

            content = _flatten_claude_code_content(message.get("content", ""))
            if role == "user" and first_user_content is None:
                first_user_content = content

            messages.append({"role": role, "content": content})

        if not saw_any_record:
            return None  # genuinely empty file: no session ever happened here

        if session_id is None:
            session_id = path.stem

        title_candidate = title_from_custom if title_from_custom is not None else first_user_content
        title = self._sanitize_title(title_candidate)

        # Reuse the shared summarizer directly rather than reimplementing truncation,
        # secret-dropping, or PII-masking -- it only ever reads msg["role"]/msg["content"],
        # which is exactly the flattened shape built above.
        msg_summary, user_msgs, asst_msgs = fetch_sessions._summarize_messages(messages)

        return {
            "session_id": session_id,
            "started_at": started_at,
            "title": title,
            "model": model,
            "source": source,
            "message_count": len(messages),
            "user_messages": user_msgs,
            "assistant_messages": asst_msgs,
            "messages": msg_summary,
        }

    @staticmethod
    def _sanitize_title(candidate: Optional[str]) -> str:
        """Redact/mask a candidate title the same way every message body gets --
        applies to BOTH title sources: a `custom-title` record's text and the
        first-user-message fallback. Neither source passes through
        `_summarize_messages()` (a title isn't a message), so without this either one
        would leak a secret or PII straight into the session dict's `title` field,
        unredacted, in a way no other code path checks.
        """
        if not candidate:
            return ""
        if fetch_sessions.contains_secret(candidate):
            return "[redacted: title source contained a secret]"
        masked = fetch_sessions.redact_pii(candidate)
        return masked[:TITLE_FALLBACK_MAX_CHARS]

    @staticmethod
    def _iter_jsonl_records(path: Path):
        """Yield parsed JSON records from a JSONL file, one per line.

        A malformed JSON line is skipped with a stderr warning -- it must never abort
        the rest of the file's walk, since one bad line from an interrupted write
        shouldn't cost an entire session's worth of otherwise-valid history.
        """
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    print(
                        f"warning: {path}:{lineno}: malformed JSON line skipped ({e})",
                        file=sys.stderr,
                    )
                    continue


register_adapter("claude_code", ClaudeCodeAdapter())
