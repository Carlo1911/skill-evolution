#!/usr/bin/env python3
"""Track processed sessions across skill evolution runs.

Usage:
    from state import load_processed, mark_processed
"""

import json
import os
import sys
from datetime import datetime, timezone

STATE_FILE = os.path.expanduser("~/.hermes/skill_evolution_state.json")
STATE_FILE_ENV_VAR = "SKILL_EVOLUTION_STATE_FILE"
STATE_RETENTION_ENV_VAR = "SKILL_EVOLUTION_STATE_RETENTION"


def get_state_file():
    """Resolve the processed-session state file, honouring SKILL_EVOLUTION_STATE_FILE.

    Read at call time rather than baked into STATE_FILE at import, matching how every other
    tunable in this pipeline is resolved -- and so a test or an isolated run can redirect
    state without monkeypatching a module constant, which was previously the only way.
    Falls back to the module global (not the literal path) so existing monkeypatching of
    STATE_FILE keeps working.

    fetch_sessions.py duplicates this deliberately; tests/test_state_schema_compat.py
    parametrizes over both modules to catch drift. Change one, change the other.
    """
    return os.environ.get(STATE_FILE_ENV_VAR, "").strip() or STATE_FILE


def _read_state():
    """Return the raw state dict, or None when absent/unreadable."""
    if not os.path.exists(get_state_file()):
        return None
    try:
        with open(get_state_file()) as f:
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

    path = get_state_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def _parse_state_retention(raw):
    """Parse SKILL_EVOLUTION_STATE_RETENTION into ('count', int) or ('age_days', float).

    Same bare-int/'Nd'/'Nmo' syntax as evaluate._parse_retention(), duplicated rather than
    imported: state.py/fetch_sessions.py sit below evaluate.py in this repo's import
    direction and must not invert it.
    """
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    if value.endswith("mo"):
        return ("age_days", float(value[:-2]) * 30)
    if value.endswith("d"):
        return ("age_days", float(value[:-1]))
    return ("count", int(value))


def prune_processed(retention=None, keep_ids=None):
    """Prune old entries from the flat {id: timestamp} state-file shape per
    SKILL_EVOLUTION_STATE_RETENTION.

    A no-op, with a stderr warning, for the documented {"processed_sessions": [...]} shape:
    that shape carries no per-session timestamp, and its list order isn't chronological
    either (fetch_sessions() builds it from a Python set union, whose iteration order is
    hash-based, not insertion-based) -- there is no temporal signal to prune by without a
    schema change, which this deliberately does not attempt (see CLAUDE.md's caution
    against reshaping the deployed file's semantics without confirming who else depends on
    it). Pruning is real only for the shape that is actually growing in production.

    `keep_ids` is always retained regardless of the configured limit. This is not a
    defensive nicety -- it is structurally required: mark_processed() computes now() once
    per call and stamps every session in that batch with the identical value, so "keep the
    N most recent by timestamp" has no defined tiebreak among everything written in the
    same run. Without this floor, a small retention count could drop most of the very batch
    just written, making those sessions look unprocessed again on the very next run.
    fetch_sessions() (the only real orchestrator of this function -- this module has none
    of its own) passes the IDs genuinely new to the current run, since the full accumulated
    processed set carries no such distinction.

    Malformed (unparseable) timestamp values are treated as expired -- pruned -- rather
    than kept indefinitely. Up to the first 5 offending keys are named on stderr, plus a
    count of any more, so this stays audible without being spammy on a large file.
    """
    keep_ids = set(keep_ids or [])
    raw_retention = retention if retention is not None else os.environ.get(STATE_RETENTION_ENV_VAR)
    rule = _parse_state_retention(raw_retention)
    if rule is None:
        return  # unbounded

    existing = _read_state()
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

    passthrough = {k: v for k, v in existing.items() if k in ("last_analyzed_at", "version")}
    session_items = [(k, v) for k, v in existing.items() if k not in ("last_analyzed_at", "version")]

    kind, limit = rule
    if kind == "count":
        # No defined tiebreak among entries sharing a timestamp (an entire run's batch
        # does), so this only meaningfully orders entries across *different* runs --
        # keep_ids below is what actually protects a single run's own batch.
        ordered = sorted(session_items, key=lambda kv: kv[1], reverse=True)
        survivors = ordered[:max(int(limit), 0)]
    else:
        cutoff = datetime.now(timezone.utc).timestamp() - (limit * 86400)
        survivors = []
        malformed = []
        for k, v in session_items:
            try:
                ts = datetime.fromisoformat(v).timestamp()
            except (ValueError, TypeError):
                ts = 0
                malformed.append(k)
            if ts >= cutoff:
                survivors.append((k, v))
        if malformed:
            shown = ", ".join(malformed[:5])
            tail = f" ...and {len(malformed) - 5} more" if len(malformed) > 5 else ""
            print(
                f"warning: {len(malformed)} state entries had unparseable timestamps and "
                f"were treated as expired: {shown}{tail}",
                file=sys.stderr,
            )

    survivor_ids = {k for k, v in survivors}
    for k, v in session_items:
        if k in keep_ids and k not in survivor_ids:
            survivors.append((k, v))
            survivor_ids.add(k)

    if len(survivors) == len(session_items):
        return  # nothing actually pruned -- don't touch the file

    new_state = {**passthrough, **dict(survivors)}
    path = get_state_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(new_state, f, indent=2)
