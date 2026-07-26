#!/usr/bin/env python3
"""Track processed sessions across skill evolution runs.

Usage:
    from state import load_processed, mark_processed
"""

import json
import os
from datetime import datetime, timezone

STATE_FILE = os.path.expanduser("~/.hermes/skill_evolution_state.json")


def _read_state():
    """Return the raw state dict, or None when absent/unreadable."""
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None
    return data if isinstance(data, dict) else None


def load_processed():
    """Load the set of already-processed session IDs.

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


def mark_processed(session_ids):
    """Mark sessions as processed, preserving whichever shape the file already uses.

    Rewriting a flat-dict file into the documented shape would discard the timestamps
    another producer maintains, so the existing layout wins; only a fresh file gets the
    documented shape.
    """
    now = datetime.now(timezone.utc).isoformat()
    existing = _read_state()

    if existing is not None and "processed_sessions" not in existing:
        state = dict(existing)  # keep prior timestamps untouched
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
