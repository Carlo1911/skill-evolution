#!/usr/bin/env python3
"""Track processed sessions across skill evolution runs.

Usage:
    from state import load_processed, mark_processed
"""

import json
import os
from datetime import datetime, timezone

STATE_FILE = os.path.expanduser("~/.hermes/skill_evolution_state.json")


def load_processed():
    """Load the set of already-processed session IDs."""
    if not os.path.exists(STATE_FILE):
        return []
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return data.get("processed_sessions", [])
    except (json.JSONDecodeError, IOError):
        return []


def mark_processed(session_ids):
    """Mark sessions as processed."""
    state = {
        "processed_sessions": session_ids,
        "last_analyzed_at": datetime.now(timezone.utc).isoformat(),
        "version": 1,
    }
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
