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

import os
from abc import ABC, abstractmethod
from datetime import datetime
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

    @abstractmethod
    def read_skill_body(self, skill_name: str) -> Optional[str]:
        """Best-effort text of `skill_name`'s currently installed body, or None.

        None on no match, an ambiguous match, or a read failure -- never raise. Callers
        (e.g. the evaluation gate's baseline-size check) degrade to whatever baseline
        they already had rather than failing a gate decision on an unrelated lookup.
        """
        raise NotImplementedError


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
        )

    def iter_skills(self) -> List[Dict[str, Any]]:
        return skill_index.scan_skills()

    def read_skill_body(self, skill_name: str) -> Optional[str]:
        # Mirrors evaluate.installed_skill_body() exactly: scan_skills() called as an
        # attribute (not `from skill_index import scan_skills`) so a test that
        # monkeypatches skill_index.scan_skills is observed here too.
        matches = [s for s in skill_index.scan_skills() if s["name"] == skill_name]
        if len(matches) != 1:
            return None
        try:
            with open(matches[0]["path"], encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None


register_adapter("hermes", HermesAdapter())
