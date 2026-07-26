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


def load_processed() -> List[str]:
    """Load the set of already-processed session IDs."""
    if not os.path.exists(STATE_FILE):
        return []
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return data.get("processed_sessions", [])
    except (json.JSONDecodeError, IOError):
        return []


def mark_processed(session_ids: List[str]) -> None:
    """Mark sessions as processed in the state file."""
    state = {"processed_sessions": session_ids, "last_analyzed_at": datetime.now(timezone.utc).isoformat(), "version": 1}
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def contains_secret(text: str) -> bool:
    """Check if text contains potential API keys or tokens."""
    text_lower = text.lower()
    return any(pattern.lower() in text_lower for pattern in SECRET_PATTERNS)


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
        max_sessions = int(os.environ.get(MAX_SESSIONS_FOR_SKILL_ENV_VAR, DEFAULT_MAX_SESSIONS_FOR_SKILL))

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
