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

import json
import os
import re
import shutil
import sys
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import fetch_sessions
import skill_index
import state


# ── Adapter interface ────────────────────────────────────────────────

class HostAdapter(ABC):
    """Base interface every host adapter implements.

    The read side is three methods — `iter_sessions()` for the analyzer's session
    evidence, `iter_skills()` for "what skills currently exist", and `read_skill_body()`
    for a single skill's installed text (used by the evaluation gate's baseline-size
    lookup, see evaluate.installed_skill_body()). The write side is two more attributes
    (P2-2): `supports_write` says whether this host can mutate skills at all, and
    `apply_skill_write(plan)` performs (or refuses) one proposal's mutation. The fourth
    seam is processed-session state: `iter_processed()`, `mark_processed()`, and
    `prune_processed()`, with `_state_file()` for path resolution. Each adapter owns its
    own state file; the concrete defaults delegate to state.py with host=self.name.
    No module outside an adapter should construct a host-specific path once every caller
    is wired through here.
    """

    name: str = ""

    # Whether this host can mutate installed skills. A host that can't (or whose write
    # side isn't implemented) leaves this False and inherits apply_skill_write()'s
    # fail-closed default, so apply_proposal() can refuse before spending a provider
    # call on a proposal it could never apply.
    supports_write: bool = False

    @abstractmethod
    def iter_sessions(self, since: Optional[datetime] = None) -> Iterable[Dict[str, Any]]:
        """Yield/return this host's session dicts, optionally bounded to `since`.

        `since=None` means "no lower bound" -- as much history as the host can supply,
        not "use whatever this host's own default window is".
        """
        raise NotImplementedError

    @abstractmethod
    def iter_skills(self, extra_roots: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Return this host's installed-skill index: name/category/description/path/size.

        `extra_roots` optionally adds external skill roots (e.g. a Hermes
        `skills.external_dirs` entry like `~/.agents/skills/`) so the index can cover
        skills beyond the host's own tree. Hosts that have no concept of extra roots
        ignore the argument. Passing nothing must behave exactly like the historical
        single-root scan.
        """
        raise NotImplementedError

    def read_skill_body(self, skill_name: str) -> Optional[str]:
        """Best-effort text of `skill_name`'s currently installed body, or None.

        None on no match, an ambiguous match, or a read failure -- never raise. Callers
        (e.g. the evaluation gate's baseline-size check) degrade to whatever baseline
        they already had rather than failing a gate decision on an unrelated lookup.

        Concrete (not abstract): every host stores skills as a name/path pair from
        iter_skills(), so "find the one match, open it, return None on any failure" is
        the same lookup for any host. A subclass overrides this only if its storage
        can't answer "read this path as text" (e.g. a future host backed by a database
        or an API rather than a filesystem).
        """
        matches = [s for s in self.iter_skills() if s["name"] == skill_name]
        if len(matches) != 1:
            return None
        try:
            with open(matches[0]["path"], encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def apply_skill_write(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Apply (or refuse) one proposal's mutation on this host.

        `plan` is the normalized mutation contract apply_proposal() builds (see
        proposal.py): `type` in {improve_existing, create_new, merge_skills,
        deprecate_skill}, `target_skill`, `proposal_id`, `changes` (a list of
        {field, old_value, new_value, description}), and `body` (the resolved full
        SKILL.md for create_new, else None).

        The contract: return a dict with `can_apply: True` and whatever host-specific
        evidence makes sense (`instructions` for Hermes, `writes` for Claude Code), or
        `can_apply: False` with a `reason` string. Never raise — fail closed on anything
        unexpected. A successful return means the mutation is *performed or scheduled by
        this host*; apply_proposal() flips the proposal to applied only after a
        `can_apply: True` here.

        Concrete (not abstract), like read_skill_body(): a host that cannot write
        skills answers "no" here, which is the correct behavior for every existing
        host until one implements writes.
        """
        return {
            "can_apply": False,
            "reason": (
                f"host '{self.name}' does not support skill writes; "
                "no proposal can be applied on it"
            ),
        }

    # ── Processed-session state (the fourth seam) ──────────────────────
    #
    # Each adapter owns its own state file. The concrete defaults below delegate to
    # state.py with host=self.name, using _state_file() for path resolution. HermesAdapter
    # inherits the defaults (state file at ~/.hermes/skill_evolution_state.json). Other
    # adapters override _state_file() to point at their own location.

    def iter_processed(self) -> List[str]:
        """This host's already-processed session IDs (dedup baseline).

        Concrete, delegating to state.py with host=self.name and path=self._state_file().
        """
        return state.load_processed(host=self.name, path=self._state_file())

    def mark_processed(self, session_ids: List[str]) -> None:
        """Mark sessions as processed for this host."""
        state.mark_processed(session_ids, host=self.name, path=self._state_file())

    def prune_processed(self, retention: Optional[str] = None,
                        keep_ids: Optional[List[str]] = None) -> None:
        """Prune old entries from this host's state file per retention config."""
        state.prune_processed(retention=retention, keep_ids=keep_ids,
                              host=self.name, path=self._state_file())

    def _state_file(self) -> str:
        """Path of this host's processed-session state file.

        Default: the shared file (Hermes's deployed location). Override for per-host files.
        """
        return state.get_state_file()


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

    # Hermes "applies" by emitting skill_manage instruction dicts for the agent to run
    # later -- this script has no Hermes runtime dependency and cannot call skill_manage
    # itself (see CLAUDE.md). The instructions are the mutation contract the cron agent
    # executes after apply_proposal() returns, so they must match what apply_proposal()
    # emitted historically, byte-for-byte (the create instruction is the one deliberate
    # exception: it gains `name` and `body`, see proposal.py's _resolve_create_body()).
    supports_write = True

    def apply_skill_write(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        proposal_type = plan["type"]
        instructions: List[Dict[str, Any]] = []
        target = plan.get("target_skill")

        if proposal_type == "improve_existing":
            for change in plan.get("changes", []):
                instruction = {
                    "action": "patch",
                    "target_skill": target,
                    "field": change["field"],
                    "description": change.get("description"),
                }
                if change.get("old_value"):
                    instruction["old_value"] = change["old_value"]
                if change.get("new_value"):
                    instruction["new_value"] = change["new_value"]
                instructions.append(instruction)

        elif proposal_type == "create_new":
            instructions.append({
                "action": "create",
                "name": plan["body_name"],
                "target_skill": target or "",
                "description": plan.get("description", ""),
                "category": plan.get("category", ""),
                "body": plan["body"],
            })

        elif proposal_type == "deprecate_skill":
            instructions.append({
                "action": "delete",
                "name": target,
            })

        elif proposal_type == "merge_skills":
            for change in plan.get("changes", []):
                if change["field"].startswith("source_"):
                    instructions.append({
                        "action": "delete",
                        "name": change.get("new_value") or change["field"].replace("source_", ""),
                        "absorbed_into": target,
                    })

        else:
            return {
                "can_apply": False,
                "reason": f"unknown proposal type '{proposal_type}'",
            }

        return {
            "can_apply": True,
            "applied_by": "agent",
            "instructions": instructions,
        }

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
            # Pass this adapter's own identity explicitly rather than letting
            # fetch_sessions() re-derive it from the ambient env var: get_adapter("hermes")
            # can be requested explicitly while SKILL_EVOLUTION_HOST names a different
            # host, and the processed-state namespace must follow the resolved adapter,
            # not whatever the env var says right now.
            host=self.name,
        )

    def iter_skills(self, extra_roots: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        # Only pass extra_roots when non-empty: old code called scan_skills() with no
        # arguments, and tests monkeypatch skill_index.scan_skills with a zero-arg
        # lambda (e.g. test_host_registry.py), so an unconditional kwarg would break
        # them. Same byte-for-byte behavior with defaults.
        if extra_roots:
            return skill_index.scan_skills(extra_roots=extra_roots)
        return skill_index.scan_skills()

    # read_skill_body() uses HostAdapter's concrete implementation: it calls
    # self.iter_skills() above, which calls skill_index.scan_skills() as an attribute,
    # so a test that monkeypatches skill_index.scan_skills is still observed.


register_adapter("hermes", HermesAdapter())


# ── Claude Code adapter (U3) ─────────────────────────────────────────
# Reads sessions/skills from Claude Code's on-disk layout instead of Hermes's SQLite
# state.db + ~/.hermes/skills/<category>/<skill>/ tree:
#   - skills:  ~/.claude/skills/*/SKILL.md          (flat -- no category level)
#   - sessions: ~/.claude/projects/*/*.jsonl         (one JSONL file per session)
#
# The JSONL record format is undocumented and versioned, so every field extraction below
# is best-effort and defensive by construction (allowlist role mapping, missing-field
# fallbacks, never raising on a malformed line) rather than assuming a fixed schema.

CLAUDE_CODE_HOME_ENV_VAR = "SKILL_EVOLUTION_CLAUDE_CODE_HOME"

# How much of a fallback-derived title (first user message, no custom-title record) to
# keep after redaction -- mirrors fetch_sessions._summarize_messages()'s content_preview
# cap in spirit, just much shorter since a title is a one-line label, not evidence text.
TITLE_FALLBACK_MAX_CHARS = 100


def _default_claude_code_home() -> str:
    """`~/.claude`, resolved at call time (not baked into a module constant at import) so
    a test's monkeypatched HOME env var is honoured on every call, the same way
    fetch_sessions.get_state_file()/get_state_db_path() re-read their env var at call
    time rather than freezing it at import."""
    return os.path.expanduser("~/.claude")


def _resolve_claude_code_home() -> str:
    """Resolve the Claude Code home directory: SKILL_EVOLUTION_CLAUDE_CODE_HOME > `~/.claude`.

    Same call-time-env-read shape as fetch_sessions.get_state_file(): read at call time,
    blank ignored, so a test or an isolated run can redirect this adapter's root without
    monkeypatching HOME itself.
    """
    return os.environ.get(CLAUDE_CODE_HOME_ENV_VAR, "").strip() or _default_claude_code_home()


def _parse_claude_code_timestamp(value: Any) -> Optional[float]:
    """Best-effort epoch-seconds parse of a Claude Code record's `timestamp` field.

    Accepts a numeric epoch or an ISO-8601 string (normalizing a trailing `Z`, which
    datetime.fromisoformat() does not accept on Python versions before 3.11). Returns
    None on anything unparseable -- callers must treat that as "unknown", not "too old":
    excluding a session because its timestamp failed to parse would silently drop real
    history, which is worse than occasionally not pre-filtering it.
    """
    if isinstance(value, bool):
        return None  # bool is an int subclass; not a plausible timestamp
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(v).timestamp()
        except ValueError:
            return None
    return None


def _flatten_claude_code_content(content: Any) -> str:
    """Flatten a Claude Code message's `content` into a plain string.

    `content` is very often a list of typed blocks (`text`, `thinking`, `tool_use`,
    `tool_result`), not a plain string -- this is the common case in real transcripts,
    not an edge case. Only `text` blocks contribute to the result; `thinking`, `tool_use`,
    and `tool_result` block content is dropped entirely -- never folded into the
    concatenated text, never stashed anywhere else in the output either. A `content` that
    is already a plain string passes through unchanged.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if text:
                    parts.append(text)
        return "".join(parts)
    return ""


def _role_for_claude_code_type(record_type: Any) -> Optional[str]:
    """Map a record's `type` to a message role, by ALLOWLIST.

    Only "user" and "assistant" map to a message. Every other type seen in practice
    (`attachment`, `custom-title`, `last-prompt`, `queue-operation`, `system`, `mode`) --
    and any future/undocumented type this format grows -- maps to None (not a message) by
    simply not appearing on this list. A denylist of known-bad types would misparse any
    new type as a message the moment the format adds one; an allowlist cannot.
    """
    if record_type == "user":
        return "user"
    if record_type == "assistant":
        return "assistant"
    return None


# ── Write-side helpers (Claude Code applies by writing files directly) ──

def _fail_write(reason: str) -> Dict[str, Any]:
    """The standard refusal shape every write path returns -- can_apply False + reason."""
    return {"can_apply": False, "reason": reason}


def _atomic_write_text(path: Path, content: str) -> None:
    """Write `content` to `path` atomically: mkstemp in the same directory + os.replace.

    Mirrors Hermes's skill_manager_tool._atomic_write_text: a temp file in the target's
    own directory (not /tmp, so no cross-filesystem rename), fsync'd, then replaced. A
    failure cleans up the temp file and re-raises -- the caller turns that into a
    can_apply: False, so a mid-write crash never leaves a half-written SKILL.md behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _refuse_if_symlinked(skill_dir: Path, skill_md: Path) -> Optional[str]:
    """Reason string if this skill is (or its SKILL.md is) a symlink, else None.

    38 of 41 real ~/.claude/skills/ entries are symlinks into ~/.agents/skills/ (owner
    decision 3). Writing through them would mutate a tree this adapter does not own, so
    patch/edit/delete refuse -- a human copies the real tree in, or the proposal retargets.
    """
    if skill_dir.is_symlink() or skill_md.is_symlink():
        return (
            f"skill '{skill_dir.name}' is a symlink into another tree "
            "(refusing to write through it)"
        )
    return None


def _rewrite_frontmatter_description(content: str, new_value: str) -> Optional[str]:
    """Replace the single-line `description:` frontmatter value, or None if impossible.

    Single-line values only -- matches skill_index.parse_name_description_frontmatter()'s
    understanding of the hand-rolled format (no PyYAML). Returns None when the new value
    contains a newline or the frontmatter has no `description:` line, so the caller fails
    closed instead of corrupting the file.
    """
    if "\n" in new_value:
        return None
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("description:"):
            lines[i] = f"description: {new_value}"
            return "\n".join(lines)
    return None


def _apply_body_change(body: str, change: Dict[str, Any]) -> Optional[str]:
    """Apply one `body` change to an in-memory copy of the skill text, or None to refuse.

    old_value non-empty -> exact substring replace (first occurrence), allowing an empty
    new_value (a deletion patch). old_value empty -> full replacement, requiring a
    non-empty new_value. Anything that cannot be applied exactly returns None -- the
    proposal is refused, never partially applied.
    """
    old_value = change.get("old_value") or ""
    new_value = change.get("new_value") or ""
    if old_value:
        if old_value not in body:
            return None
        return body.replace(old_value, new_value, 1)
    if not new_value:
        return None
    return new_value


class ClaudeCodeAdapter(HostAdapter):
    """Reads sessions/skills from Claude Code's on-disk layout (`~/.claude/`).

    Both iter_sessions() and iter_skills() route through the shared redaction chokepoint
    (fetch_sessions.contains_secret()/redact_pii()/_summarize_messages()) rather than
    reimplementing any of it -- see CLAUDE.md's "Secrets and PII are handled differently,
    on purpose" for why a duplicate implementation here would be a correctness risk, not
    just style debt.
    """

    name = "claude_code"

    # Claude Code "applies" by writing skill files directly (owner decision 1) -- there
    # is no equivalent of Hermes's skill_manage tool, and this host's apply happens
    # right here, in-process, not via instructions an agent runs later.
    supports_write = True

    # ── Processed-session state: per-host file under ~/.claude/ ──

    def _state_file(self) -> str:
        """Claude Code's state file lives under its home directory.

        SKILL_EVOLUTION_STATE_FILE remains the universal override — when set, it redirects
        whichever host is active (including Claude Code). When not set, the default is
        <CLAUDE_CODE_HOME>/skill_evolution_state.json.
        """
        override = os.environ.get(state.STATE_FILE_ENV_VAR, "").strip()
        if override:
            return override
        return str(Path(_resolve_claude_code_home()) / "skill_evolution_state.json")

    # ── skills: ~/.claude/skills/*/SKILL.md (flat, no category level) ──

    def iter_skills(self, extra_roots: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        # `extra_roots` is accepted for signature parity with HostAdapter but ignored:
        # this host's frame of reference is a single flat tree under
        # <CLAUDE_CODE_HOME>/skills, and skills.external_dirs is a Hermes concept.
        base = Path(_resolve_claude_code_home()) / "skills"
        if not base.exists():
            return []

        results = []
        for skill_dir in sorted(base.iterdir()):
            if not skill_dir.is_dir():
                continue
            # Path.iterdir()/glob() do not exclude dot-prefixed entries the way a shell
            # glob does. skill_index.scan_skills() filters dot-prefixed dirs at the
            # *category* level for Hermes (.archive/, .curator_backups/, .hub/); this
            # host has no category level, so the equivalent filter is re-applied one
            # level down, at the skill-directory level.
            if skill_dir.name.startswith("."):
                continue

            # skill_index.build_skill_record() covers "no SKILL.md here"
            # (FileNotFoundError, an OSError subclass) and any other read failure by
            # returning None -- the same record shape scan_skills() builds for Hermes,
            # just with a constant category instead of a derived one: this host has no
            # real category level today. "user" distinguishes ~/.claude/skills/ from a
            # future project-level .claude/skills/ tree, out of scope for this unit.
            record = skill_index.build_skill_record(skill_dir / "SKILL.md", category="user")
            if record is not None:
                results.append(record)

        return results

    # read_skill_body() uses HostAdapter's concrete implementation.

    # ── writes: direct in-place skill mutations ──────────────────────

    def apply_skill_write(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Apply (or refuse) one proposal's mutation by writing skill files directly.

        Never raises: any exception -- including a bug in this method -- becomes
        can_apply: False with the exception named in `reason` (the fail-closed contract
        is absolute, matching the eval plan's R21 posture).
        """
        try:
            proposal_type = plan["type"]
            if proposal_type == "create_new":
                return self._apply_create(plan)
            if proposal_type == "improve_existing":
                return self._apply_improve(plan)
            if proposal_type == "deprecate_skill":
                return self._apply_deprecate(plan, absorbed_into=None)
            if proposal_type == "merge_skills":
                return self._apply_merge(plan)
            return _fail_write(f"unknown proposal type '{proposal_type}'")
        except Exception as e:
            return _fail_write(f"apply failed: {type(e).__name__}: {e}")

    def _resolve_skill_path(self, skill_name: str) -> Optional[Path]:
        """Single installed SKILL.md path for `skill_name`, or None (no/ambiguous match)."""
        matches = [s for s in self.iter_skills() if s["name"] == skill_name]
        if len(matches) != 1:
            return None
        return Path(matches[0]["path"])

    def _apply_create(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        name = (plan.get("body_name") or plan.get("name") or "").strip()
        if not name:
            return _fail_write("create_new requires a skill name")
        if name.startswith("."):
            return _fail_write(f"invalid skill name '{name}': must not start with '.'")
        if "/" in name or "\\" in name:
            return _fail_write(f"invalid skill name '{name}': must not contain path separators")
        body = plan.get("body")
        if not body:
            return _fail_write("create_new requires a non-empty body")

        skill_dir = Path(_resolve_claude_code_home()) / "skills" / name
        if skill_dir.exists():
            return _fail_write(f"skill '{name}' already exists at {skill_dir}")
        skill_md = skill_dir / "SKILL.md"
        try:
            _atomic_write_text(skill_md, body)
        except OSError as e:
            return _fail_write(f"could not create {skill_md}: {e}")
        return {"can_apply": True, "applied_by": "direct", "writes": [str(skill_md)]}

    def _apply_improve(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        target = plan.get("target_skill")
        if not target:
            return _fail_write("improve_existing requires a target_skill")
        skill_md = self._resolve_skill_path(target)
        if skill_md is None:
            return _fail_write(f"skill '{target}' not found (or ambiguous) among installed skills")
        refused = _refuse_if_symlinked(skill_md.parent, skill_md)
        if refused:
            return _fail_write(refused)
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError as e:
            return _fail_write(f"could not read {skill_md}: {e}")

        changed_fields = []
        for change in plan.get("changes", []):
            field = change.get("field")
            if field == "description":
                new_value = change.get("new_value")
                if not new_value:
                    return _fail_write("description change has an empty new_value")
                updated = _rewrite_frontmatter_description(content, new_value)
                if updated is None:
                    return _fail_write(
                        f"description change for '{target}': no single-line description: line in frontmatter"
                    )
                content = updated
                changed_fields.append("description")
            elif field == "body":
                updated = _apply_body_change(content, change)
                if updated is None:
                    return _fail_write(
                        f"body change for '{target}': old_value not found in installed body "
                        "(or empty replacement with no old_value)"
                    )
                content = updated
                changed_fields.append("body")
            else:
                return _fail_write(f"unsupported change field '{field}' for claude_code writes")

        try:
            _atomic_write_text(skill_md, content)
        except OSError as e:
            return _fail_write(f"could not write {skill_md}: {e}")
        return {
            "can_apply": True,
            "applied_by": "direct",
            "writes": [str(skill_md)],
            "changed_fields": changed_fields,
        }

    def _archive_skill(
        self, skill_name: str, absorbed_into: Optional[str] = None
    ) -> Dict[str, Any]:
        """Move one skill's directory into ~/.claude/skills/.archive/ (owner decision 2).

        The read side already skips dot-prefixed dirs, so an archived skill vanishes
        from the live index automatically. On a name collision the archive dir is
        timestamp-suffixed. `absorbed_into`, when given, must name a real, different,
        installed skill -- mirroring Hermes's _delete_skill.
        """
        skill_md = self._resolve_skill_path(skill_name)
        if skill_md is None:
            return _fail_write(f"skill '{skill_name}' not found (or ambiguous) among installed skills")
        if absorbed_into is not None:
            if absorbed_into == skill_name:
                return _fail_write("absorbed_into must differ from the skill being archived")
            if self._resolve_skill_path(absorbed_into) is None:
                return _fail_write(
                    f"absorbed_into skill '{absorbed_into}' does not exist (or is ambiguous)"
                )
        refused = _refuse_if_symlinked(skill_md.parent, skill_md)
        if refused:
            return _fail_write(refused)

        archive_base = Path(_resolve_claude_code_home()) / "skills" / ".archive"
        dest = archive_base / skill_md.parent.name
        if dest.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = archive_base / f"{skill_md.parent.name}-{stamp}"
        try:
            archive_base.mkdir(parents=True, exist_ok=True)
            shutil.move(str(skill_md.parent), str(dest))
        except OSError as e:
            return _fail_write(f"could not archive '{skill_name}': {e}")
        return {"can_apply": True, "applied_by": "direct", "writes": [str(dest)]}

    def _apply_deprecate(self, plan: Dict[str, Any], absorbed_into: Optional[str]) -> Dict[str, Any]:
        return self._archive_skill(plan.get("target_skill") or "", absorbed_into)

    def _apply_merge(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        umbrella = plan.get("target_skill")
        if not umbrella:
            return _fail_write("merge_skills requires a target_skill (the absorbed_into umbrella)")
        written = []
        for change in plan.get("changes", []):
            field = change.get("field")
            if not field.startswith("source_"):
                continue
            source_name = change.get("new_value") or field.replace("source_", "")
            result = self._archive_skill(source_name, absorbed_into=umbrella)
            if not result.get("can_apply"):
                return result
            written.extend(result.get("writes", []))
        if not written:
            return _fail_write("merge_skills proposal had no source_* changes to apply")
        return {"can_apply": True, "applied_by": "direct", "writes": written}

    # ── sessions: ~/.claude/projects/*/*.jsonl (one file per session) ──

    def iter_sessions(self, since: Optional[datetime] = None) -> Iterable[Dict[str, Any]]:
        since_epoch = since.timestamp() if since is not None else None
        projects_dir = Path(_resolve_claude_code_home()) / "projects"
        if not projects_dir.exists():
            return []

        results = []
        for jsonl_path in sorted(projects_dir.glob("*/*.jsonl")):
            if since_epoch is not None:
                # Cheapest possible pre-filter, before opening the file at all: a
                # session's JSONL file is only ever appended to as the session
                # progresses, so its mtime is always >= every record's timestamp inside
                # it. If the file hasn't been touched since before the cutoff, nothing
                # inside it can be newer than the cutoff either.
                try:
                    if jsonl_path.stat().st_mtime < since_epoch:
                        continue
                except OSError:
                    pass  # can't stat it; fall through and let the real parse decide

            try:
                session = self._parse_session_file(jsonl_path, since_epoch)
            except OSError as e:
                print(f"warning: could not read {jsonl_path}: {e}", file=sys.stderr)
                continue
            if session is not None:
                results.append(session)

        return results

    def _parse_session_file(
        self, path: Path, since_epoch: Optional[float]
    ) -> Optional[Dict[str, Any]]:
        """Parse one session's JSONL file into a session dict, or None to signal "skip
        this file entirely" -- either it had zero parseable records (a genuinely empty
        file corresponds to no session ever having happened, not a zero-message one), or
        its resolved started_at fell outside `since_epoch` (the second, finer-grained
        pre-filter layer -- see iter_sessions()'s mtime check for the first).
        """
        session_id = None
        started_at = None
        title_from_custom = None
        model = ""
        source = ""
        messages: List[Dict[str, str]] = []
        first_user_content = None
        saw_any_record = False
        since_check_done = False

        for record in self._iter_jsonl_records(path):
            saw_any_record = True
            if not isinstance(record, dict):
                continue

            rtype = record.get("type")
            role = _role_for_claude_code_type(rtype)

            if session_id is None:
                sid = record.get("sessionId")
                if sid:
                    session_id = sid

            if started_at is None and "timestamp" in record:
                # Scan forward past leading records that lack a timestamp (small
                # records like custom-title/mode commonly come first) -- this is the
                # first record that actually HAS one, not literally record zero.
                # Stored as an epoch float, not the raw ISO string: R4 requires
                # iter_sessions() output to match Hermes in *type*, not just key set, and
                # Hermes's started_at is a SQLite REAL (a Python float). A timestamp that
                # fails to parse falls back to 0.0 rather than leaving started_at as a
                # differently-typed string.
                started_at = _parse_claude_code_timestamp(record["timestamp"]) or 0.0

            # The since-window decision is gated on the first MESSAGE-bearing record's
            # own timestamp, deliberately independent of `started_at` above. A non-message
            # record (system/mode/etc.) can carry an unrelated timestamp -- e.g. a hook
            # logged before the conversation actually started -- and real session files
            # show `system` records with a `timestamp` field. Gating the early-return on
            # that timestamp would silently drop a real, in-window session because of a
            # housekeeping record's date, not the conversation's.
            if not since_check_done and role is not None and "timestamp" in record:
                since_check_done = True
                epoch = _parse_claude_code_timestamp(record["timestamp"])
                # Mirrors the SQL path filtering by started_at before ever fetching
                # message bodies: stop reading the rest of this file as soon as we know
                # it's out of window, rather than parsing every remaining message first.
                if since_epoch is not None and epoch is not None and epoch < since_epoch:
                    return None

            if rtype == "custom-title" and title_from_custom is None:
                candidate = (
                    record.get("title")
                    or record.get("customTitle")
                    or record.get("value")
                )
                if candidate:
                    title_from_custom = str(candidate)

            if not source:
                entrypoint = record.get("entrypoint")
                if entrypoint:
                    source = str(entrypoint)

            if role is None:
                continue  # not a message: attachment/custom-title/system/mode/etc.

            message = record.get("message")
            if not isinstance(message, dict):
                message = {}

            if role == "assistant" and not model:
                m = message.get("model")
                if m:
                    model = str(m)

            content = _flatten_claude_code_content(message.get("content", ""))
            if role == "user" and first_user_content is None:
                first_user_content = content

            messages.append({"role": role, "content": content})

        if not saw_any_record:
            return None  # genuinely empty file: no session ever happened here

        if session_id is None:
            session_id = path.stem

        title_candidate = title_from_custom if title_from_custom is not None else first_user_content
        title = self._sanitize_title(title_candidate)

        # Reuse the shared summarizer directly rather than reimplementing truncation,
        # secret-dropping, or PII-masking -- it only ever reads msg["role"]/msg["content"],
        # which is exactly the flattened shape built above.
        msg_summary, user_msgs, asst_msgs = fetch_sessions._summarize_messages(messages)

        return {
            "session_id": session_id,
            "started_at": started_at,
            "title": title,
            "model": model,
            "source": source,
            "message_count": len(messages),
            "user_messages": user_msgs,
            "assistant_messages": asst_msgs,
            "messages": msg_summary,
        }

    @staticmethod
    def _sanitize_title(candidate: Optional[str]) -> str:
        """Redact/mask a candidate title the same way every message body gets --
        applies to BOTH title sources: a `custom-title` record's text and the
        first-user-message fallback. Neither source passes through
        `_summarize_messages()` (a title isn't a message), so without this either one
        would leak a secret or PII straight into the session dict's `title` field,
        unredacted, in a way no other code path checks.
        """
        if not candidate:
            return ""
        if fetch_sessions.contains_secret(candidate):
            return "[redacted: title source contained a secret]"
        masked = fetch_sessions.redact_pii(candidate)
        return masked[:TITLE_FALLBACK_MAX_CHARS]

    @staticmethod
    def _iter_jsonl_records(path: Path):
        """Yield parsed JSON records from a JSONL file, one per line.

        A malformed JSON line is skipped with a stderr warning -- it must never abort
        the rest of the file's walk, since one bad line from an interrupted write
        shouldn't cost an entire session's worth of otherwise-valid history.
        """
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as e:
                    print(
                        f"warning: {path}:{lineno}: malformed JSON line skipped ({e})",
                        file=sys.stderr,
                    )
                    continue


register_adapter("claude_code", ClaudeCodeAdapter())
