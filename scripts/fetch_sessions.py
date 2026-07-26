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
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


DEFAULT_DB_PATH = os.path.expanduser("~/.hermes/state.db")
STATE_FILE = os.path.expanduser("~/.hermes/skill_evolution_state.json")

# ── Secret patterns — NEVER include these in datasets ──────────────────
SECRET_PATTERNS = [
    "sk-ant-api", "sk-or-v1-", "ghp_", "ghu_", "xoxb-", "xapp-",
    "ntn_", "AKIA", "-----BEGIN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
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


def _read_state() -> Optional[Dict[str, Any]]:
    """Return the raw state dict, or None when absent/unreadable."""
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None
    return data if isinstance(data, dict) else None


def load_processed() -> List[str]:
    """Load the set of already-processed session IDs.

    Kept byte-for-byte equivalent to state.py's copy (this script is standalone by
    convention) -- if you change one, change the other.

    Two on-disk shapes exist. This repo and the deployed SKILL.md document
    {"processed_sessions": [...], "last_analyzed_at": ..., "version": 1}, but the file
    actually deployed today is a flat {session_id: iso_timestamp} map. Reading only the
    documented shape silently returned [] against the real file, which disabled dedup
    entirely -- every session looked unprocessed on every run.
    """
    data = _read_state()
    if data is None:
        return []
    if "processed_sessions" in data:
        return data.get("processed_sessions") or []
    # Flat {session_id: timestamp} map: the keys *are* the processed ids.
    return [k for k in data if k != "last_analyzed_at" and k != "version"]


def mark_processed(session_ids: List[str]) -> None:
    """Mark sessions as processed, preserving whichever shape the file already uses.

    Rewriting a flat-dict file into the documented shape would discard the timestamps
    another producer maintains, so the existing layout wins; only a fresh file gets the
    documented shape.
    """
    now = datetime.now(timezone.utc).isoformat()
    existing = _read_state()

    if existing is not None and "processed_sessions" not in existing:
        state: Dict[str, Any] = dict(existing)  # keep prior timestamps untouched
        for sid in session_ids:
            state.setdefault(sid, now)
    else:
        state = {
            "processed_sessions": list(session_ids),
            "last_analyzed_at": now,
            "version": 1,
        }

    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


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
) -> List[Dict[str, Any]]:
    """Fetch recent unprocessed sessions from state.db."""
    processed = set(load_processed())
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
    new_processed = list(processed | {s["session_id"] for s in results})
    mark_processed(new_processed)

    return results


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


def main():
    parser = argparse.ArgumentParser(description="Fetch unprocessed Hermes sessions")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--lookback-hours", type=int, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--max-sessions", type=int, default=DEFAULT_MAX_SESSIONS)
    parser.add_argument("--dry-run", action="store_true", help="Print counts without marking processed")
    args = parser.parse_args()

    sessions = fetch_sessions(
        db_path=args.db_path,
        lookback_hours=args.lookback_hours,
        max_sessions=args.max_sessions,
        dry_run=args.dry_run,
    )

    for session in sessions:
        sys.stdout.write(json.dumps(session) + "\n")

    if args.dry_run:
        print(f"Found {len(sessions)} unprocessed sessions", file=sys.stderr)


if __name__ == "__main__":
    main()
