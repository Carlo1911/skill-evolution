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
    SELECT id, created_at, model, title, source, total_tokens
    FROM sessions
    WHERE source != 'cron'
    ORDER BY created_at DESC
    LIMIT ?
"""

MESSAGE_QUERY = """
    SELECT role, content, created_at
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

        created_at = session["created_at"]
        if isinstance(created_at, str):
            try:
                ts = datetime.fromisoformat(created_at).timestamp()
            except ValueError:
                ts = 0
        else:
            ts = created_at or 0

        if ts < cutoff:
            continue

        title = session.get("title", "")
        model = session.get("model", "")
        source = session.get("source", "")
        total_tokens = session.get("total_tokens", 0)

        # Fetch messages
        messages = conn.execute(MESSAGE_QUERY, (session_id,)).fetchall()
        msg_summary = []
        user_msgs = 0
        asst_msgs = 0
        tool_calls = 0

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "") or ""
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

        results.append({
            "session_id": session_id,
            "created_at": created_at,
            "title": title,
            "model": model,
            "source": source,
            "total_tokens": total_tokens,
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
