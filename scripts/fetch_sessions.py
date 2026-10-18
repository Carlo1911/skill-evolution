#!/usr/bin/env python3
"""Fetch recent, unprocessed sessions from Hermes state.db.

Outputs NDJSON to stdout — one session per line.
Designed to be piped into an LLM agent's context via Hermes cron jobs.

Usage:
    python fetch_sessions.py
    python fetch_sessions.py --dry-run
    python fetch_sessions.py --lookback-hours 72
"""

import argparse
import json
import math
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


DEFAULT_DB_PATH = os.path.expanduser("~/.hermes/state.db")
STATE_FILE = os.path.expanduser("~/.hermes/skill_evolution_state.json")
STATE_FILE_ENV_VAR = "SKILL_EVOLUTION_STATE_FILE"
STATE_RETENTION_ENV_VAR = "SKILL_EVOLUTION_STATE_RETENTION"
DB_PATH_ENV_VAR = "SKILL_EVOLUTION_DB_PATH"

# Host-prefix support (KTD5). Mirrors host.py's HOST_ENV_VAR/DEFAULT_HOST exactly, but is
# duplicated here rather than imported: host.py imports this module (U1's HermesAdapter
# delegates to fetch_sessions()), so importing host.py back from here would be a cycle.
# state.py duplicates this same pair on purpose too -- if you change one, change the other.
HOST_ENV_VAR = "SKILL_EVOLUTION_HOST"
DEFAULT_HOST = "hermes"

# ── Secret patterns — NEVER include these in datasets ──────────────────
SECRET_PATTERNS = [
    "sk-ant-api", "sk-or-v1-", "ghp_", "ghu_", "xoxb-", "xapp-",
    "ntn_", "AKIA", "-----BEGIN", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
    "OPENROUTER_API_KEY", "SLACK_BOT_TOKEN", "GITHUB_TOKEN",
    "AWS_SECRET_ACCESS_KEY", "DATABASE_URL",
]

# Shape-based detection, because the fixed list above only matches secrets whose
# vendor prefix or exact env-var name was known in advance. Scanning the real
# ~/.hermes/state.db showed that gap is not theoretical: it missed 27 password
# assignments, 23 generic *_TOKEN=/_SECRET=/_KEY= assignments, 6 generic `sk-` keys,
# 5 `Authorization: Bearer` headers, and a credentialed database URI -- all of which
# would have been forwarded to a provider verbatim.
#
# Length floors (20+ chars for opaque keys, 8+ for assigned values) keep ordinary
# config off the list: MAX_TOKENS=1024 and DEBUG=true do not match. These run against
# a single line at a time (see evaluate.redact_secrets), so anchoring is per line.
SECRET_REGEXES = [
    re.compile(pattern, re.IGNORECASE) for pattern in (
        r"\bsk-[A-Za-z0-9_-]{20,}",                      # OpenAI & compatible
        r"\bsk_live_[A-Za-z0-9]{16,}",                   # Stripe live
        r"\bgithub_pat_[A-Za-z0-9_]{20,}",               # GitHub fine-grained
        r"\bglpat-[A-Za-z0-9_-]{16,}",                   # GitLab
        r"\bAIza[A-Za-z0-9_-]{30,}",                     # Google
        r"\bxoxp-[A-Za-z0-9-]{10,}",                     # Slack user token
        r"\bnpm_[A-Za-z0-9]{30,}",                       # npm
        r"\bhf_[A-Za-z0-9]{30,}",                        # Hugging Face
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}",  # JWT
        r"authorization:\s*bearer\s+\S{16,}",            # auth header
        r"\b[A-Z][A-Z0-9_]*_(?:TOKEN|SECRET|KEY|PASSWORD)\s*[:=]\s*\S{8,}",  # env assignment
        r"\bpass(?:wd|word)\s*[:=]\s*\S{6,}",            # password assignment
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:@/]+:[^\s@/]+@",  # creds in URI
    )
]

# ── PII masking, for text that crosses the external-provider boundary ──
#
# Distinct from SECRET_PATTERNS in both what it catches and what it does. A detected secret
# drops the whole message (see _summarize_messages) because no version of that message is
# worth sending; PII is *masked in place* so an email costs the email, not the paragraph of
# debugging evidence around it.
#
# Money amounts are deliberately absent. Measured against the real session DB, amounts
# appear in 120 of the messages that actually cross, and money-management skills are exactly
# what the analyzer needs to reason about -- masking them removes evidence without
# protecting an identity. The line drawn here is identifiers, not amounts.
#
# Card matching needs Luhn as well as a regex: a bare 13-19 digit probe matched 840 messages
# in the real DB, Luhn cut that to 374, and requiring a card-shaped leading digit and length
# narrows it further. Numeric ids, byte counts and hashes must survive.
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_CARD_LENGTHS = {13, 14, 15, 16, 19}

PII_REGEXES = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"\+\d{1,3}[\s.-]?\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
    # Keyword-anchored so an arbitrary 8- or 11-digit number is not swept up.
    ("tax-id", re.compile(r"\bRUC[\s:#]*\d{11}\b", re.IGNORECASE)),
    ("national-id", re.compile(r"\bDNI[\s:#]*\d{8}\b", re.IGNORECASE)),
]


def _luhn_ok(digits: str) -> bool:
    """Luhn checksum — necessary, not sufficient, for a payment card number."""
    total, alternate = 0, False
    for char in reversed(digits):
        value = int(char)
        if alternate:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        alternate = not alternate
    return total % 10 == 0


def _mask_cards(text: str) -> str:
    """Mask card-shaped, Luhn-valid digit runs, leaving other long numbers alone."""
    def replace(match):
        digits = re.sub(r"\D", "", match.group())
        if len(digits) in _CARD_LENGTHS and digits[0] in "3456" and _luhn_ok(digits):
            return "[PII:card]"
        return match.group()

    return _CARD_CANDIDATE.sub(replace, text)


def redact_pii(text: str) -> str:
    """Mask personal identifiers in `text`, preserving everything around them.

    Applied to message content before it is previewed and sent to a provider. The mask names
    the kind of identifier removed (`[PII:email]`) so a reviewer reading a proposal can tell
    what was dropped rather than just that something was.
    """
    if not text:
        return text
    for label, regex in PII_REGEXES:
        text = regex.sub(f"[PII:{label}]", text)
    return _mask_cards(text)


SESSION_QUERY = """
    SELECT id, started_at, model, title, source
    FROM sessions
    WHERE source != 'cron'
    ORDER BY started_at DESC
    LIMIT ?
"""

MESSAGE_QUERY = """
    SELECT role, content, timestamp
    FROM messages
    WHERE session_id = ?
    ORDER BY id ASC
"""

MAX_MESSAGE_CHARS = 2000
DEFAULT_LOOKBACK_HOURS = 48
DEFAULT_MAX_SESSIONS = 20


# ── Numeric env-var reading, shared by every tunable ────────────────────
# Lives here, the lowest-level module, so evaluate.py (which already imports from this
# file) and optimize_skill.py can share one implementation instead of three copies.

def _env_number(name: str, default, cast):
    """Read a numeric env var, falling back to `default` when absent or malformed.

    Every tunable used to be read with a bare int()/float() over os.environ.get, across 11
    call sites, so a typo raised ValueError out of whichever component read it first:
    `SKILL_EVOLUTION_MAX_GROWTH_PCT=abc` took down the deterministic evaluator with a stack
    trace instead of producing a gate decision. That was survivable while this code was not
    the live pipeline; it now runs unattended nightly, where one typo'd variable costs the
    whole run.

    The fallback is announced on stderr rather than applied silently -- a misconfiguration
    that degrades quietly persists until somebody happens to notice. stderr specifically,
    because stdout is the NDJSON channel.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return cast(raw.strip())
    except (ValueError, AttributeError):
        print(
            f"warning: {name}={raw!r} is not a valid {cast.__name__}; "
            f"falling back to default {default}",
            file=sys.stderr,
        )
        return default


def env_float(name: str, default: float) -> float:
    """Float-valued env var with a reported fallback. See _env_number."""
    return _env_number(name, default, float)


def env_int(name: str, default: int) -> int:
    """Int-valued env var with a reported fallback. See _env_number.

    A float-looking value like "1.5" is malformed for an int setting and falls back rather
    than truncating -- silently turning 1.5 into 1 is the worse surprise.
    """
    return _env_number(name, default, int)


def get_state_file() -> str:
    """Resolve the processed-session state file, honouring SKILL_EVOLUTION_STATE_FILE.

    Read at call time rather than baked into STATE_FILE at import, matching how every other
    tunable in this pipeline is resolved -- and so a test or an isolated run can redirect
    state without monkeypatching a module constant, which was previously the only way.
    Falls back to the module global (not the literal path) so existing monkeypatching of
    STATE_FILE keeps working.

    state.py duplicates this deliberately; tests/test_state_schema_compat.py parametrizes
    over both modules to catch drift. Change one, change the other.
    """
    return os.environ.get(STATE_FILE_ENV_VAR, "").strip() or STATE_FILE


def get_state_db_path() -> str:
    """Resolve the session database, honouring SKILL_EVOLUTION_DB_PATH.

    Deliberately the same shape as get_state_file() above -- env read at call time, blank
    ignored, fallback to the module global rather than the literal path so monkeypatching
    DEFAULT_DB_PATH keeps working.

    This existed only as an import in evaluate._fetch_session_messages() until 2026-07-29,
    which meant evaluate_tool_calls(session_id) and evaluate_analyzer_prompt() raised
    ImportError on every call that reached the DB. Their tests all passed message lists
    instead of session ids, so nothing exercised the branch. Note the asymmetry with
    fetch_sessions()/sessions_for_skill(), which take `db_path` as an argument defaulting to
    DEFAULT_DB_PATH: those are called from a CLI that owns a --db-path flag, while the
    evaluation targets are called in-process with no path to thread through.
    """
    return os.environ.get(DB_PATH_ENV_VAR, "").strip() or DEFAULT_DB_PATH


def _read_state(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the raw state dict, or None when absent/unreadable.

    `path` overrides the resolved state file when given (per-host files). When None,
    falls back to get_state_file() (the shared default).
    """
    target = path or get_state_file()
    if not os.path.exists(target):
        return None
    try:
        with open(target) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None
    return data if isinstance(data, dict) else None


def _resolve_host(host: Optional[str] = None) -> str:
    """Resolve the active host name: explicit arg > SKILL_EVOLUTION_HOST > default.

    Duplicates host.resolve_host()'s exact resolution order rather than importing it --
    see the HOST_ENV_VAR/DEFAULT_HOST comment above for why. state.py duplicates this
    same helper; if you change one, change the other.
    """
    if host:
        return host
    return os.environ.get(HOST_ENV_VAR, DEFAULT_HOST)


def _host_key(session_id: str, host: str) -> str:
    """The on-disk key a *new* entry for `session_id` gets under `host`."""
    return f"{host}:{session_id}"


def _bare_id_for_host(key: str, host: str) -> Optional[str]:
    """If `key` belongs to `host`, return its bare session id; otherwise None.

    Two forms count as belonging to a host: an exactly-prefixed "<host>:<id>" key, and --
    only for the default "hermes" host -- a legacy key with no recognised prefix at all.
    The flat state file deployed today has 101 such legacy entries with no host prefix,
    written before any host concept existed; treating them as implicitly "hermes:<id>"
    is what lets load_processed(host="hermes") keep working against that file with zero
    migration. A key prefixed for some other host (e.g. "claude_code:<id>") must NOT
    resolve for host="hermes" -- only exactly-prefixed keys resolve for a non-default host.

    Kept byte-for-byte equivalent to state.py's copy -- if you change one, change the other.
    """
    prefix = f"{host}:"
    if key.startswith(prefix):
        return key.removeprefix(prefix)
    if host == DEFAULT_HOST and ":" not in key:
        return key
    return None


def load_processed(host: Optional[str] = None, path: Optional[str] = None) -> List[str]:
    """Load the set of already-processed session IDs for `host` (default: resolved host).

    `path` overrides the resolved state file when given (per-host files). When None,
    falls back to get_state_file() (the shared default).

    Kept byte-for-byte equivalent to state.py's copy (this script is standalone by
    convention) -- if you change one, change the other.

    Two on-disk shapes exist. This repo and the deployed SKILL.md document
    {"processed_sessions": [...], "last_analyzed_at": ..., "version": 1}, but the file
    actually deployed today is a flat {session_id: iso_timestamp} map. Reading only the
    documented shape silently returned [] against the real file, which disabled dedup
    entirely -- every session looked unprocessed on every run.

    The documented shape predates the host concept and is not host-scoped -- it is only
    ever produced fresh by mark_processed() (see below), so there is nothing to
    disambiguate there yet. Host scoping applies to the flat-map shape, which is the one
    actually deployed and growing.
    """
    resolved = _resolve_host(host)
    data = _read_state(path)
    if data is None:
        return []
    if "processed_sessions" in data:
        return data.get("processed_sessions") or []
    # Flat {session_id: timestamp} map: filter+strip to this host's bare ids.
    ids = []
    for k in data:
        if k in ("last_analyzed_at", "version"):
            continue
        bare = _bare_id_for_host(k, resolved)
        if bare is not None:
            ids.append(bare)
    return ids


def mark_processed(session_ids: List[str], host: Optional[str] = None,
                   path: Optional[str] = None) -> None:
    """Mark sessions as processed for `host`, preserving whichever shape the file uses.

    `path` overrides the resolved state file when given (per-host files). When None,
    falls back to get_state_file() (the shared default).

    Rewriting a flat-dict file into the documented shape would discard the timestamps
    another producer maintains, so the existing layout wins; only a fresh file gets the
    documented shape (which stays host-agnostic -- see load_processed()).

    On the flat-map shape, a genuinely new entry is written under the "<host>:<id>" key
    (KTD5) -- but an id already covered by an existing entry for this host (a legacy bare
    key when host == "hermes", or an already-prefixed key) is left exactly as-is; this
    never touches or reformats a pre-existing entry, it only adds ones that are missing.
    """
    now = datetime.now(timezone.utc).isoformat()
    existing = _read_state(path)

    if existing is not None and "processed_sessions" not in existing:
        resolved = _resolve_host(host)
        state: Dict[str, Any] = dict(existing)  # keep prior timestamps untouched
        for sid in session_ids:
            key = _host_key(sid, resolved)
            if key in state:
                continue  # already present under this host's prefixed key
            if resolved == DEFAULT_HOST and sid in state:
                continue  # already present as a legacy bare key
            state.setdefault(key, now)
    else:
        state = {
            "processed_sessions": list(session_ids),
            "last_analyzed_at": now,
            "version": 1,
        }

    target = path or get_state_file()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as f:
        json.dump(state, f, indent=2)


def _parse_state_retention(raw: Optional[str]):
    """Parse SKILL_EVOLUTION_STATE_RETENTION into ('count', int) or ('age_days', float).

    Same bare-int/'Nd'/'Nmo' syntax as evaluate._parse_retention(), duplicated rather than
    imported: this module sits below evaluate.py in this repo's import direction and must
    not invert it.
    """
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    try:
        if value.endswith("mo"):
            rule = ("age_days", float(value[:-2]) * 30)
        elif value.endswith("d"):
            rule = ("age_days", float(value[:-1]))
        else:
            rule = ("count", int(value))
    except ValueError:
        rule = None
    # A typo must not take the run down: this is read after mark_processed() on the fetch
    # path, so raising there marked sessions processed and then exited before emitting
    # them -- they were never analyzed. Warn and keep everything, like _env_number().
    if rule is None or not math.isfinite(rule[1]):
        print(f"warning: {STATE_RETENTION_ENV_VAR}={raw!r} is not a valid retention (N, Nd or Nmo); "
              f"treating it as unbounded", file=sys.stderr)
        return None
    return rule


def prune_processed(retention: Optional[str] = None, keep_ids: Optional[List[str]] = None,
                    host: Optional[str] = None, path: Optional[str] = None) -> None:
    """Prune old entries from the flat {id: timestamp} state-file shape per
    SKILL_EVOLUTION_STATE_RETENTION, scoped to `host`'s own entries.

    `path` overrides the resolved state file when given (per-host files). When None,
    falls back to get_state_file() (the shared default).

    A no-op, with a stderr warning, for the documented {"processed_sessions": [...]} shape:
    that shape carries no per-session timestamp, and its list order isn't chronological
    either (fetch_sessions() builds it from a Python set union, whose iteration order is
    hash-based, not insertion-based) -- there is no temporal signal to prune by without a
    schema change, which this deliberately does not attempt (see CLAUDE.md's caution
    against reshaping the deployed file's semantics without confirming who else depends on
    it). Pruning is real only for the shape that is actually growing in production.

    Only entries belonging to the resolved host (bare legacy keys for "hermes", or
    exactly-prefixed "<host>:<id>" keys) are eligible for pruning -- entries belonging to
    a different host pass through untouched, exactly like last_analyzed_at/version already
    do. `keep_ids` is matched against each entry's *bare* id, not its on-disk key, since
    mark_processed() may have written it under a "<host>:<id>" key.

    `keep_ids` is always retained regardless of the configured limit. This is not a
    defensive nicety -- it is structurally required: mark_processed() computes now() once
    per call and stamps every session in that batch with the identical value, so "keep the
    N most recent by timestamp" has no defined tiebreak among everything written in the
    same run. Without this floor, a small retention count could drop most of the very batch
    just written, making those sessions look unprocessed again on the very next run.
    fetch_sessions() (the only real orchestrator of this function) passes the IDs genuinely
    new to the current run, since the full accumulated processed set carries no such
    distinction.

    Malformed (unparseable) timestamp values are treated as expired -- pruned -- rather
    than kept indefinitely. Up to the first 5 offending keys are named on stderr, plus a
    count of any more, so this stays audible without being spammy on a large file.

    state.py duplicates this deliberately; tests/test_state_pruning.py parametrizes over
    both modules to catch drift. Change one, change the other.
    """
    keep_ids = set(keep_ids or [])
    raw_retention = retention if retention is not None else os.environ.get(STATE_RETENTION_ENV_VAR)
    rule = _parse_state_retention(raw_retention)
    if rule is None:
        return  # unbounded

    existing = _read_state(path)
    if existing is None:
        return

    if "processed_sessions" in existing:
        print(
            "warning: SKILL_EVOLUTION_STATE_RETENTION is set, but this state file uses the "
            'documented {"processed_sessions": [...]} shape, which carries no per-session '
            "timestamp -- there is nothing to prune by. Pruning only applies to the flat "
            "{session_id: timestamp} shape.",
            file=sys.stderr,
        )
        return

    resolved = _resolve_host(host)
    passthrough = {k: v for k, v in existing.items() if k in ("last_analyzed_at", "version")}
    other_host_items = []  # (key, value) pairs belonging to a different host -- untouched
    session_items = []  # (key, value, bare_id) pairs belonging to the resolved host
    for k, v in existing.items():
        if k in ("last_analyzed_at", "version"):
            continue
        bare = _bare_id_for_host(k, resolved)
        if bare is None:
            other_host_items.append((k, v))
        else:
            session_items.append((k, v, bare))

    kind, limit = rule
    if kind == "count":
        # No defined tiebreak among entries sharing a timestamp (an entire run's batch
        # does), so this only meaningfully orders entries across *different* runs --
        # keep_ids below is what actually protects a single run's own batch.
        ordered = sorted(session_items, key=lambda kvb: kvb[1], reverse=True)
        survivors = ordered[:max(int(limit), 0)]
    else:
        cutoff = datetime.now(timezone.utc).timestamp() - (limit * 86400)
        survivors = []
        malformed = []
        for k, v, bare in session_items:
            try:
                ts = datetime.fromisoformat(v).timestamp()
            except (ValueError, TypeError):
                ts = 0
                malformed.append(k)
            if ts >= cutoff:
                survivors.append((k, v, bare))
        if malformed:
            shown = ", ".join(malformed[:5])
            tail = f" ...and {len(malformed) - 5} more" if len(malformed) > 5 else ""
            print(
                f"warning: {len(malformed)} state entries had unparseable timestamps and "
                f"were treated as expired: {shown}{tail}",
                file=sys.stderr,
            )

    survivor_keys = {k for k, v, b in survivors}
    for k, v, bare in session_items:
        if bare in keep_ids and k not in survivor_keys:
            survivors.append((k, v, bare))
            survivor_keys.add(k)

    if len(survivors) == len(session_items):
        return  # nothing actually pruned for this host -- don't touch the file

    new_state = {
        **passthrough,
        **{k: v for k, v in other_host_items},
        **{k: v for k, v, b in survivors},
    }
    target = path or get_state_file()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as f:
        json.dump(new_state, f, indent=2)


def contains_secret(text: str) -> bool:
    """Check if text contains potential API keys, tokens, passwords or credentialed URIs.

    Two layers: the known-marker substrings in SECRET_PATTERNS, plus the shape-based
    SECRET_REGEXES that catch secrets whose vendor prefix or env-var name wasn't known
    in advance. Both redaction points depend on this predicate, so it must err toward
    over-detection -- a false positive costs one redacted line of session evidence, a
    false negative forwards a live credential to a third-party provider.
    """
    text_lower = text.lower()
    if any(pattern.lower() in text_lower for pattern in SECRET_PATTERNS):
        return True
    return any(regex.search(text) for regex in SECRET_REGEXES)


def _summarize_messages(messages) -> tuple:
    """Redact, truncate, and shape a session's message rows.

    Shared by fetch_sessions() and sessions_for_skill(): both need the same
    per-message secret redaction (contains_secret()), MAX_MESSAGE_CHARS
    truncation, and {role, content_preview, content_length} shape, and the
    same user/assistant role counts. Returns (msg_summary, user_msgs, asst_msgs).
    """
    msg_summary = []
    user_msgs = 0
    asst_msgs = 0

    for msg in messages:
        role = msg["role"]
        content = msg["content"] or ""
        if contains_secret(content):
            continue
        # Secrets drop the message; identifiers are masked so the surrounding evidence
        # survives. Applied before truncation so the mask cannot be split in half.
        content = redact_pii(content)

        if role == "user":
            user_msgs += 1
        elif role == "assistant":
            asst_msgs += 1

        if len(content) > MAX_MESSAGE_CHARS:
            content = content[:MAX_MESSAGE_CHARS] + "\n[...truncated]"

        msg_summary.append({
            "role": role,
            "content_preview": content[:500],
            "content_length": len(content),
        })

    return msg_summary, user_msgs, asst_msgs


def fetch_sessions(
    db_path: str = DEFAULT_DB_PATH,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
    dry_run: bool = False,
    host: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch recent unprocessed sessions from state.db.

    `host` identifies which processed-state namespace to read/write (KTD5) --
    defaults to `_resolve_host()`'s own env-var resolution when not given explicitly.
    Callers that already resolved a specific adapter (e.g. `host.HermesAdapter`) should
    pass their own identity here rather than relying on the ambient env var, which may
    have moved on since the adapter was resolved.
    """
    resolved_host = _resolve_host(host)
    processed = set(load_processed(host=resolved_host))
    cutoff = datetime.now(timezone.utc).timestamp() - (lookback_hours * 3600)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    sessions = conn.execute(SESSION_QUERY, (max_sessions,)).fetchall()
    results = []

    for session in sessions:
        session_id = session["id"]

        if session_id in processed:
            continue

        started_at = session["started_at"]
        if isinstance(started_at, str):
            try:
                ts = datetime.fromisoformat(started_at).timestamp()
            except ValueError:
                ts = 0
        else:
            ts = started_at or 0

        if ts < cutoff:
            continue

        title = session["title"] or ""
        model = session["model"] or ""
        source = session["source"] or ""

        # Fetch messages
        messages = conn.execute(MESSAGE_QUERY, (session_id,)).fetchall()
        msg_summary, user_msgs, asst_msgs = _summarize_messages(messages)

        results.append({
            "session_id": session_id,
            "started_at": started_at,
            "title": title,
            "model": model,
            "source": source,
            "message_count": len(messages),
            "user_messages": user_msgs,
            "assistant_messages": asst_msgs,
            "messages": msg_summary,
        })

    conn.close()

    if dry_run:
        return results

    # Mark as processed
    just_processed = {s["session_id"] for s in results}
    new_processed = list(processed | just_processed)
    mark_processed(new_processed, host=resolved_host)

    # Auto-prune, mirroring evaluate.py's SKILL_EVOLUTION_HISTORY_RETENTION: a no-op unless
    # SKILL_EVOLUTION_STATE_RETENTION is configured. `keep_ids` protects this run's own
    # writes from itself -- see prune_processed()'s docstring for why that floor is
    # structurally required, not just cautious. Placed after the dry-run return (like
    # mark_processed() itself) so a dry run never prunes either.
    _housekeep_after_marking(
        lookback_hours, lambda: prune_processed(keep_ids=just_processed, host=resolved_host))

    return results


def _housekeep_after_marking(lookback_hours: int, prune) -> None:
    """Retention warning + pruning, which both fetch paths run *after* mark_processed().

    Nothing here may abort the run: the sessions are already marked processed but not yet
    emitted, so an exception would lose them for good. Failures are reported on stderr
    (stdout is the NDJSON channel) and the run carries on.
    """
    try:
        _warn_if_state_retention_below_lookback(lookback_hours)
        prune()
    except Exception as e:  # noqa: BLE001 -- see docstring
        print(f"warning: state pruning failed and was skipped: {e}", file=sys.stderr)


def _warn_if_state_retention_below_lookback(lookback_hours: int) -> None:
    """Pruning old entries out of state does not, on its own, stop a session from being
    re-fetched: if a later run's --lookback-hours still reaches back far enough, a
    since-pruned session is simultaneously "looks unprocessed" and "still in the query
    window", so it gets re-fetched, re-marked, and re-analyzed every time this recurs.
    There's no clean structural fix without coupling retention to lookback, which would be
    a larger, more opinionated change than this warrants -- so the mitigation here is a
    warning, not a fix. Fires every run it applies to (a persistent cron-cadence signal),
    from the unattended orchestration path, not only from --prune-state.

    Only age-based retention ("Nd"/"Nmo") has units that compare cleanly to lookback_hours;
    count-based retention has no such comparison (and has its own related, separately
    documented limitation: keep_ids overriding the limit means a busy day's batch can floor
    the file above the configured count).
    """
    rule = _parse_state_retention(os.environ.get(STATE_RETENTION_ENV_VAR))
    if rule is None or rule[0] != "age_days":
        return
    retention_hours = rule[1] * 24
    if retention_hours < lookback_hours:
        print(
            f"warning: SKILL_EVOLUTION_STATE_RETENTION ({rule[1]:g}d = {retention_hours:g}h) "
            f"is shorter than this run's --lookback-hours ({lookback_hours}h) -- a session "
            f"pruned from state can still fall inside the lookback window and be "
            f"re-fetched, re-marked, and re-analyzed indefinitely. Set retention >= the "
            f"effective lookback window to avoid this.",
            file=sys.stderr,
        )


SKILL_SESSION_IDS_QUERY = """
    SELECT DISTINCT m.session_id AS session_id
    FROM messages m, json_each(m.tool_calls) AS tc
    WHERE m.tool_calls IS NOT NULL
      AND json_valid(m.tool_calls)
      AND json_extract(tc.value, '$.function.name') IN ('skill_view', 'skill_manage')
      AND json_valid(json_extract(tc.value, '$.function.arguments'))
      AND json_extract(json_extract(tc.value, '$.function.arguments'), '$.name') = ?
"""

SKILL_SESSION_QUERY = """
    SELECT id, started_at, model, title, source
    FROM sessions
    WHERE id = ? AND source != 'cron'
"""

SKILL_MESSAGE_QUERY = """
    SELECT role, content, timestamp
    FROM messages
    WHERE session_id = ?
    ORDER BY id ASC
"""


DEFAULT_MAX_SESSIONS_FOR_SKILL = 20

# U4: overridable via env var, following evaluate.py's SKILL_EVOLUTION_<NOUN>
# convention (an OPTIMIZER_ segment distinguishes this optimizer-only tunable
# from the shared evaluation-gate ones).
MAX_SESSIONS_FOR_SKILL_ENV_VAR = "SKILL_EVOLUTION_OPTIMIZER_MAX_SESSIONS_FOR_SKILL"


def _session_db_is_readable(db_path: str) -> bool:
    """Whether `db_path` is an existing session DB carrying a `sessions` table.

    Checked *before* sqlite3.connect(), because connect() creates an empty file for a
    missing path -- so a typo'd --db-path would both crash with a confusing
    "no such table: sessions" and leave a stray 0-byte DB behind.

    Reports on stderr rather than returning silently: a caller that degrades to "no
    sessions" for a misconfigured path looks identical to one that legitimately found
    none, and a misconfiguration that degrades quietly persists until somebody notices.
    Only sessions_for_skill() uses this. fetch_sessions() deliberately still raises --
    it is the unattended cron path, where an unreadable DB is a failure the operator
    needs surfaced, not a run that quietly reports zero sessions every night.
    """
    if not os.path.exists(db_path):
        print(f"warning: session DB not found at {db_path}; treating as no history",
              file=sys.stderr)
        return False
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as e:
        print(f"warning: session DB at {db_path} is not readable ({e}); "
              f"treating as no history", file=sys.stderr)
        return False
    return True


def sessions_for_skill(skill_name: str, db_path: str = DEFAULT_DB_PATH,
                        max_sessions: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return a skill's most recent recorded session history, most-recent first.

    Unlike fetch_sessions(), this is not gated by the cron pipeline's
    processed-state or lookback window, and has no side effects (it never
    calls mark_processed() or touches the state file). A session is matched
    when one of its messages recorded a `skill_view` or `skill_manage` tool
    call whose arguments name this skill.

    Capped at `max_sessions` (most recent by `started_at`) so a popular skill's
    accumulated history doesn't grow the rendered excerpt text -- and therefore
    every downstream LLM prompt built from it -- without bound. When
    `max_sessions` is not explicitly passed, the cap is resolved from
    MAX_SESSIONS_FOR_SKILL_ENV_VAR at call time (falling back to
    DEFAULT_MAX_SESSIONS_FOR_SKILL), not baked into the default-argument value
    -- which would only be read once, at import time.
    """
    if max_sessions is None:
        max_sessions = env_int(MAX_SESSIONS_FOR_SKILL_ENV_VAR, DEFAULT_MAX_SESSIONS_FOR_SKILL)

    # "No usable DB" and "no sessions for this skill" are the same answer to the optimizer,
    # which exits cleanly below MIN_SESSIONS either way -- so degrade (with a warning)
    # rather than raising sqlite3.OperationalError out of an interactive command.
    if not _session_db_is_readable(db_path):
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        session_ids = [
            row["session_id"]
            for row in conn.execute(SKILL_SESSION_IDS_QUERY, (skill_name,)).fetchall()
        ]

        results = []
        for session_id in session_ids:
            session = conn.execute(SKILL_SESSION_QUERY, (session_id,)).fetchone()
            if session is None:
                # Either the session doesn't exist or it's source='cron'.
                continue

            messages = conn.execute(SKILL_MESSAGE_QUERY, (session_id,)).fetchall()
            msg_summary, user_msgs, asst_msgs = _summarize_messages(messages)

            results.append({
                "session_id": session["id"],
                "started_at": session["started_at"],
                "title": session["title"],
                "model": session["model"],
                "source": session["source"],
                "message_count": len(messages),
                "user_messages": user_msgs,
                "assistant_messages": asst_msgs,
                "messages": msg_summary,
            })

        results.sort(key=lambda r: r["started_at"] or 0, reverse=True)
        return results[:max_sessions]
    finally:
        conn.close()


def fetch_sessions_for_host(
    host_name: str,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Fetch recent unprocessed sessions from a non-Hermes host adapter.

    `HostAdapter.iter_sessions()` is a pure read with no processed-state awareness (see
    host.py's docstring) -- `HermesAdapter` gets dedup/marking for free by delegating to
    `fetch_sessions()` above, which does both internally. This function is what gives
    every *other* adapter the identical guarantee: filter against `adapter.iter_processed()`,
    then (unless `dry_run`) `adapter.mark_processed()` and `adapter.prune_processed()`,
    mirroring `fetch_sessions()`'s own side-effect contract at the orchestration layer
    instead of inside the adapter.

    `host` is imported locally: this module is imported BY `host.py` (`HermesAdapter`
    delegates to `fetch_sessions()`), so a module-scope `import host` here would cycle.
    """
    import host as host_module

    adapter = host_module.get_adapter(host_name)
    processed = set(adapter.iter_processed())
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    results = [
        session for session in adapter.iter_sessions(since=since)
        if session["session_id"] not in processed
    ]

    if dry_run:
        return results

    just_processed = {s["session_id"] for s in results}
    new_processed = list(processed | just_processed)
    adapter.mark_processed(new_processed)

    _housekeep_after_marking(
        lookback_hours, lambda: adapter.prune_processed(keep_ids=list(just_processed)))

    return results


def main():
    parser = argparse.ArgumentParser(description="Fetch unprocessed sessions from the active host")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH,
                        help="Hermes-only: state.db path. Ignored when SKILL_EVOLUTION_HOST "
                             "resolves to a non-hermes adapter.")
    parser.add_argument("--lookback-hours", type=int, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--max-sessions", type=int, default=DEFAULT_MAX_SESSIONS,
                        help="Hermes-only: ignored when SKILL_EVOLUTION_HOST resolves to a "
                             "non-hermes adapter.")
    parser.add_argument("--dry-run", action="store_true", help="Print counts without marking processed")
    parser.add_argument("--prune-state", action="store_true",
                        help="Prune the processed-session state file per "
                             "SKILL_EVOLUTION_STATE_RETENTION and exit")
    args = parser.parse_args()

    resolved_host = _resolve_host()

    if args.prune_state:
        # Structurally like evaluate.py's --prune (before/after count, early return), but
        # reporting to stderr rather than stdout: unlike evaluate.py, this script's stdout
        # is the NDJSON channel other tooling parses (nothing but fetch_sessions.py may
        # write to stdout), so a manual maintenance report cannot share that stream.
        #
        # Routes through the adapter so each host prunes its own state file.
        import host as host_module
        adapter = host_module.get_adapter(resolved_host)
        before = len(adapter.iter_processed())
        adapter.prune_processed()
        after = len(adapter.iter_processed())
        print(f"Pruned state: {before} -> {after} processed session ids", file=sys.stderr)
        return

    if resolved_host == DEFAULT_HOST:
        sessions = fetch_sessions(
            db_path=args.db_path,
            lookback_hours=args.lookback_hours,
            max_sessions=args.max_sessions,
            dry_run=args.dry_run,
            host=resolved_host,
        )
    else:
        sessions = fetch_sessions_for_host(
            resolved_host,
            lookback_hours=args.lookback_hours,
            dry_run=args.dry_run,
        )

    for session in sessions:
        sys.stdout.write(json.dumps(session) + "\n")

    if args.dry_run:
        print(f"Found {len(sessions)} unprocessed sessions", file=sys.stderr)


if __name__ == "__main__":
    main()
