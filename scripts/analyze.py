#!/usr/bin/env python3
"""Analyze and produce the analysis prompt text for the cron agent.

Reads NDJSON sessions from stdin, combines with skill index,
and outputs a formatted prompt ready for LLM analysis.

Usage:
    python fetch_sessions.py | python analyze.py
    python fetch_sessions.py --dry-run | python analyze.py --dry-run
"""

import json
import sys


def format_session(session: dict) -> str:
    """Format a session for LLM consumption."""
    sid = session.get("session_id", "unknown")
    title = session.get("title", "Untitled")
    model = session.get("model", "unknown")
    source = session.get("source", "unknown")
    total_tokens = session.get("total_tokens", 0)
    msg_count = session.get("message_count", 0)
    user_msgs = session.get("user_messages", 0)
    asst_msgs = session.get("assistant_messages", 0)

    lines = [
        f"### Session: {sid}",
        f"- **Title:** {title}",
        f"- **Model:** {model}",
        f"- **Source:** {source}",
        f"- **Messages:** {msg_count} ({user_msgs} user, {asst_msgs} assistant)",
        f"- **Total tokens:** {total_tokens}",
        "",
        "**Message flow:**",
    ]

    for msg in session.get("messages", []):
        role = msg["role"]
        preview = msg.get("content_preview", "")[:300]
        lines.append(f"- [{role}]: {preview}")

    lines.append("")
    return "\n".join(lines)


def main():
    dry_run = "--dry-run" in sys.argv
    sessions = [json.loads(line) for line in sys.stdin if line.strip()]

    if not sessions:
        print("No sessions to analyze.")
        sys.exit(0)

    output = []
    output.append(f"# Sessions to Analyze ({len(sessions)} total)")
    output.append("")

    for session in sessions:
        output.append(format_session(session))

    print("\n".join(output))

    if dry_run:
        print(f"\n--- Dry run: {len(sessions)} sessions formatted ---", file=sys.stderr)


if __name__ == "__main__":
    main()
