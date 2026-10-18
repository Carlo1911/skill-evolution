#!/usr/bin/env python3
"""Track processed sessions across skill evolution runs.

Usage:
    from state import load_processed, mark_processed
"""

import json
import math
import os
import sys
from datetime import datetime, timezone

STATE_FILE = os.path.expanduser("~/.hermes/skill_evolution_state.json")
STATE_FILE_ENV_VAR = "SKILL_EVOLUTION_STATE_FILE"
STATE_RETENTION_ENV_VAR = "SKILL_EVOLUTION_STATE_RETENTION"

# Host-prefix support (KTD5). Mirrors host.py's HOST_ENV_VAR/DEFAULT_HOST exactly, but is
# duplicated here rather than imported: host.py imports fetch_sessions.py (U1's
# HermesAdapter delegates to it), and fetch_sessions.py duplicates this same module
# on purpose (see module docstring), so importing host.py from either would be a cycle.
# This module sits below host.py in the import direction and must not invert it.
HOST_ENV_VAR = "SKILL_EVOLUTION_HOST"
DEFAULT_HOST = "hermes"


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


def _read_state(path=None):
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


def _resolve_host(host=None):
    """Resolve the active host name: explicit arg > SKILL_EVOLUTION_HOST > default.

    Duplicates host.resolve_host()'s exact resolution order rather than importing it --
    see the HOST_ENV_VAR/DEFAULT_HOST comment above for why.
    """
    if host:
        return host
    return os.environ.get(HOST_ENV_VAR, DEFAULT_HOST)


def _host_key(session_id, host):
    """The on-disk key a *new* entry for `session_id` gets under `host`."""
    return f"{host}:{session_id}"


def _bare_id_for_host(key, host):
    """If `key` belongs to `host`, return its bare session id; otherwise None.

    Two forms count as belonging to a host: an exactly-prefixed "<host>:<id>" key, and --
    only for the default "hermes" host -- a legacy key with no recognised prefix at all.
    The flat state file deployed today has 101 such legacy entries with no host prefix,
    written before any host concept existed; treating them as implicitly "hermes:<id>"
    is what lets load_processed(host="hermes") keep working against that file with zero
    migration. A key prefixed for some other host (e.g. "claude_code:<id>") must NOT
    resolve for host="hermes" -- only exactly-prefixed keys resolve for a non-default host.
    """
    prefix = f"{host}:"
    if key.startswith(prefix):
        return key.removeprefix(prefix)
    if host == DEFAULT_HOST and ":" not in key:
        return key
    return None


def load_processed(host=None, path=None):
    """Load the set of already-processed session IDs for `host` (default: resolved host).

    `path` overrides the resolved state file when given (per-host files). When None,
    falls back to get_state_file() (the shared default).

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


def mark_processed(session_ids, host=None, path=None):
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
        state = dict(existing)  # keep prior timestamps untouched
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


def _parse_state_retention(raw):
    """Parse SKILL_EVOLUTION_STATE_RETENTION into ('count', int) or ('age_days', float).

    Same bare-int/'Nd'/'Nmo' syntax as evaluate._parse_retention(), duplicated rather than
    imported: state.py/fetch_sessions.py sit below evaluate.py in this repo's import
    direction and must not invert it.
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


def prune_processed(retention=None, keep_ids=None, host=None, path=None):
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
