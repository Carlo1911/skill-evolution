---
title: Host-agnostic read side — HostAdapter protocol + Claude Code adapter
type: feat
date: 2026-07-29
topic: host-agnostic-multi-agent
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: pending-work-2026-07-28 P2-2
execution: code
deepened: 2026-07-29
---

# Host-agnostic read side: name the contracts that already exist, then add a second host

> **Scope decision up front: this plan does the read side only.** It makes the pipeline
> ingest sessions and skills from a second host (Claude Code) and produce proposals from
> them, without touching the write side. Applying a proposal stays Hermes-only. That split
> is the whole reason this is tractable — see "Why the write side is a separate plan".

## Goal Capsule

- **Objective:** Let the pipeline read sessions and skills from a host other than Hermes,
  behind one selector, without any host-specific knowledge leaking outside the adapter that
  owns it.
- **Product authority:** `docs/pending-work-2026-07-28.md` P2-2, itself carrying the owner's
  standing principle recorded in `CLAUDE.md`: *"the more agnostic the design, the better"*,
  with multi-agent support (Codex, Claude Code, future hosts) an explicit project goal.
- **Definition of success:** `SKILL_EVOLUTION_HOST=claude_code python3 scripts/fetch_sessions.py`
  emits the same NDJSON shape as the Hermes path, `analyze.py` consumes it unchanged, and
  the entire evaluation half — registry, gate, size guards, history, provider layer — runs
  without knowing which host produced the text.
- **Open blockers:** None. Both host layouts were inspected on disk while writing this plan
  (see Context).

## Context

### What is actually coupled, verified against the code

The pending-work entry claims exactly four seams. That is correct, and one finding
strengthens the case: **the seam boundary is already respected by every other module.**
`evaluate.py` and `optimize_skill.py` never hardcode a Hermes path — they call
`skill_index.scan_skills()` and `fetch_sessions`'s resolvers. So this refactor is contained
to four files plus their callers' import lines, not spread across the codebase.

| Seam | Module | Hermes-specific today |
|---|---|---|
| Session source | `fetch_sessions.py` | `~/.hermes/state.db`, and the `sessions`/`messages` SQLite schema |
| Skill source | `skill_index.py` | `~/.hermes/skills/<category>/<skill>/SKILL.md` |
| Processed state | `state.py` | `~/.hermes/skill_evolution_state.json` (already env-overridable) |
| Mutation | `proposal.py` | emits `skill_manage` instruction dicts |

### The two host layouts, as they exist on this machine

| | Hermes | Claude Code |
|---|---|---|
| Sessions | one SQLite DB, `~/.hermes/state.db`, tables `sessions` + `messages` | one JSONL file per session: `~/.claude/projects/<cwd-slug>/<session-uuid>.jsonl` |
| Session record | rows with `id`, `started_at`, `title`, `model`, `source` | one JSON object per line; `type` ∈ {`user`, `assistant`, `attachment`, `custom-title`, `last-prompt`, `queue-operation`, `system`, `mode`}; keys include `sessionId`, `timestamp`, `message`, `cwd`, `gitBranch`, `version` |
| Skills | **nested**: `~/.hermes/skills/<category>/<skill>/SKILL.md` | **flat**: `~/.claude/skills/<skill>/SKILL.md` — no category level |
| Mutation | `skill_manage` tool | none — skills are plain files |

Two consequences the design has to absorb, and they are the whole substance of this plan:

1. **The difference is the *shape*, not the path.** An env var pointing at a different
   directory cannot bridge SQLite rows and JSONL lines. This is why the plan introduces an
   adapter rather than more `SKILL_EVOLUTION_*_PATH` variables.
2. **Claude Code's skill tree is one level shallower.** `scan_skills()` iterates
   category-dir → skill-dir; a flat tree walked by that loop yields nothing. The adapter
   must synthesize a category (see KTD3).

**Verified against a real installation, not just documentation.** 38 of the 41 entries
under `~/.claude/skills/` on this machine are symlinks into `~/.agents/skills/`, not real
directories — `Path.iterdir()`/`.is_dir()` resolve through them transparently (confirmed: a
glob over `~/.claude/skills/*/SKILL.md` finds all 35 skills that actually carry one), so this
needs no adapter-side handling, only a note so a future implementer doesn't second-guess the
design on sight of a symlinked tree. The JSONL side had a real gap: a live session file on
this machine contains **six** non-message `type` values, not the four the table above
originally listed — `system` (hook/tool-lifecycle metadata, no `message` field) and `mode`
(`{mode, sessionId, type}` only) are both real and both absent from every description of this
format consulted while drafting this plan. Both are now listed above and handled in KTD4/U3.
This is the same lesson `pending-work-2026-07-28.md` already recorded for
`evaluate_tool_calls()` (P0-5) — a documented shape is not evidence until checked against a
real file — landing in a second place in the same repo.

### The contracts are already host-neutral — they just have no names

This is the key insight that makes the work small. Two dicts already cross every seam, and
neither carries Hermes-specific fields:

```python
# session (fetch_sessions.py, emitted per session as one NDJSON line)
{"session_id", "started_at", "title", "model", "source",
 "message_count", "user_messages", "assistant_messages", "messages"}

# skill (skill_index.py scan_skills())
{"name", "category", "description", "path", "size"}
```

`analyze.py` consumes the first, the analyzer prompt consumes both, and the whole evaluation
half consumes skill *text*. **The work is not designing a contract — it is naming the two
that exist, documenting them, and writing a second producer for each.**

### Why the write side is a separate plan

`pending-work-2026-07-28.md` calls the write side "the expensive half". That framing needs
one correction this plan's research turned up: **it is expensive in design, not in
mechanics.** Emitting `skill_manage` dicts is the *harder* target. For a file-based host,
applying a proposal is `write_text()` plus `mkdir` — strictly simpler than Hermes.

What makes it a separate decision is not difficulty but blast radius: applying mutates a
user's installed skills, and every safety property the repo has (the ratchet, the shrink
floor, cumulative drift, the human gate) was designed and validated against Hermes. A
second write path doubles the surface those guarantees have to hold across, and it needs the
owner's sign-off on questions this plan deliberately does not answer — whether a Claude Code
apply writes in place or stages a diff, and what "the installed skill" even means when
Claude Code has both user-level (`~/.claude/skills/`) and project-level (`.claude/skills/`)
trees that can hold the same skill name.

Read-only support has none of that: worst case it produces a proposal nobody applies.

## Key Technical Decisions

### KTD1. One `HostAdapter` protocol + module-level registry, mirroring the two registries that already exist

The repo has settled this pattern twice — `REGISTRY`/`register_evaluator()` for evaluators
and `PROVIDER_CALLERS` for providers. Both are plain dicts populated at import time with no
dynamic discovery, and both resolve through one function that reads one env var with an
explicit-argument override. A third instance of a pattern used twice is the conservative
choice, not a new abstraction.

```python
# scripts/host.py  (new)
class HostAdapter(ABC):
    name: str
    @abstractmethod
    def iter_sessions(self, since=None) -> Iterable[Dict[str, Any]]: ...
    @abstractmethod
    def iter_skills(self) -> List[Dict[str, Any]]: ...
    @abstractmethod
    def read_skill_body(self, skill_name: str) -> Optional[str]: ...

HOST_ADAPTERS: Dict[str, HostAdapter] = {}
HOST_ENV_VAR = "SKILL_EVOLUTION_HOST"
DEFAULT_HOST = "hermes"

def resolve_host(host: Optional[str] = None) -> str:  # explicit arg > env > default
def get_adapter(host: Optional[str] = None) -> HostAdapter:
```

**`resolve_host()`'s resolution order (explicit arg > env > default) mirrors
`resolve_provider()`**; the raise-on-unknown-name behavior mirrors `call_provider()`'s
validation and message — the same fail-loud posture, for the same reason: a typo'd host that
quietly reads the wrong tree is worse than a crash. Governs R1, R2.

### KTD2. `read_skill_body()` is a third method, not a caller's job

`evaluate.installed_skill_body()` (`scripts/evaluate.py:1103-1106`) and
`optimize_skill._installed_body()` both resolve a skill
name to its body text by filtering `scan_skills()` and reading `path`. That works only
because both hosts happen to expose a filesystem path. A host that stores skills in a
database or over an API would have a `path` field with nothing behind it, and the leak would
surface as a confusing read failure inside the *evaluator*, far from the adapter that owns
the storage decision.

Putting the read behind the adapter keeps `path` advisory (useful for a human reading a
report) rather than load-bearing. The two existing helpers become thin calls into it and
keep their current best-effort contract — **return `None` on no match, ambiguous match, or
read failure, never raise** — because `evaluate.py` deliberately degrades rather than failing
a gate decision on a lookup miss.

`optimize_skill._find_skill_matches()` also routes through `get_adapter().iter_skills()`
instead of calling `skill_index.scan_skills()` directly, for the same reason: it is the
lookup `_installed_body()` itself calls to detect an ambiguous duplicate name (the
`.archive/` twin case), so it constructs the same kind of host-specific knowledge R3 exists
to contain. Governs R3.

### KTD3. The Claude Code adapter synthesizes `category` rather than the index dropping the field

`category` is not decoration: `skill_index`'s dot-prefix filter uses it to skip `.archive/`,
`.curator_backups/` and `.hub/`, and `apply_proposal()` passes it through on `create`.
Making it `Optional` would push a `None` case into every consumer to serve one host.

The adapter reports a constant instead — `category: "user"` for `~/.claude/skills/`,
`category: "project"` for a `.claude/skills/` tree — which preserves the field's meaning
(*where this skill came from*) and, as a bonus, is exactly the distinction a future write
side needs to disambiguate a duplicated skill name. Governs R2, R6.

### KTD4. Message normalization is the adapter's job; redaction stays shared

`_summarize_messages()` — truncation, `contains_secret()` message-dropping, `redact_pii()`
masking — is host-neutral and **must not be duplicated per adapter.** It is the single
chokepoint the repo's whole privacy posture rests on, and a second copy is precisely the
failure mode that produced three divergent `fetch_sessions.py` files (backlog P0-1).

So the split is: the adapter converts host records into a plain `[{"role", "content"}]` list
and hands it to the shared summarizer. Claude
Code's `type` field maps to `role` (`user`/`assistant`), and the **six** non-message record
types (`attachment`, `custom-title`, `last-prompt`, `queue-operation`, `system`, `mode`) are
dropped by an explicit allowlist (map `user`/`assistant`, else drop) rather than a denylist —
`system` and `mode` were absent from every description of this format consulted while
drafting this plan and were only found by inspecting a real session file, so the drop list is
not trusted to be exhaustive going forward. `custom-title` is first harvested for the session
`title`, since Hermes has a real title column and the JSONL has no other source for one.
**The candidate title text runs through `contains_secret()`/`redact_pii()` before it is placed
in the session dict** — falling back to the first user message when there is no
`custom-title` record means `title` can otherwise carry the same class of live secret or PII
the message-body path is built to catch, but bypassing that exact chokepoint on its way in,
since only the messages list (not `title`) was ever named as passing through the shared
summarizer.

**Real records are not plain strings — content is a block list.** Verified against 60 real
session files on this machine: assistant `message.content` is a list of content blocks
(`text`, `thinking`, `tool_use`) in 100% of sampled records, and user `message.content` is a
list (chiefly `tool_result` blocks) in 92.6% of records whenever a message follows a tool
call — not the plain string the `[{"role", "content"}]` shape above implies.
Handing a block list straight to `_summarize_messages()` breaks immediately:
`contains_secret()`'s first step is `text.lower()`, which raises `AttributeError` on a list,
on nearly every real session that used a tool. The adapter must flatten *before* the handoff:
concatenate `text`-type block text into `content`; drop `tool_use`, `tool_result`, and
`thinking` block content entirely rather than folding it in — `_summarize_messages()` itself
only ever reads `msg["role"]`/`msg["content"]` (verified: `scripts/fetch_sessions.py`'s real
implementation has no `tool_calls` handling at all), so a per-message `tool_calls` key would
be dead weight the summarizer silently discards, not a field worth inventing. `thinking` is
excluded rather than included on the same reasoning driving the drop: it restates the model's
own reasoning about user content, and the shared redaction chokepoint exists to filter the raw
record, not to be trusted against a paraphrase of it. This flattening happens per-message,
inside the adapter, reducing each record to the plain `{"role", "content"}` shape
`_summarize_messages()` actually consumes, before the handoff — the shared summarizer's
contract (host-neutral, do-not-duplicate) is unchanged; only the adapter's job grows to
include this step. (Tool-call-level analysis is `evaluate_tool_calls()`'s separate, DB-backed
path — already out of scope per Scope Boundaries — not this one.) Governs R4, R5.

### KTD5. Processed-state keys get a host prefix

The state file tracks session ids. Hermes ids look like `20260728_080025_e2bf84c6`; Claude
Code's are UUIDs. They will not collide today, but sharing one namespace means switching
hosts silently pollutes the other's dedup record, and `prune_processed()`'s retention would
count both toward one limit.

Store `"<host>:<session_id>"`. Reading tolerates un-prefixed keys as `hermes:` so **the 101
existing entries in the deployed flat-dict state file keep working with no migration** —
which matters more than tidiness here, because `CLAUDE.md` warns that another producer
maintains that file's timestamps and it must not be rewritten.

**This must land in both copies, not just `state.py`.** `CLAUDE.md` already documents that
`fetch_sessions.py` duplicates `load_processed()`/`mark_processed()`/`prune_processed()`
inline rather than importing `state.py` — and `fetch_sessions()`, the function the deployed
cron path actually calls, uses only its own local copies, never `state.py`'s. Prefixing
`state.py` alone would leave the cron path writing un-prefixed keys forever, silently
reproducing the exact "dedup disabled" failure this repo has already hit once. The prefix
change lands in both copies, and `tests/test_state_schema_compat.py`'s existing
parametrization over both modules (built for exactly this drift risk) extends to cover it.

The exact API shape: `load_processed(host)` strips the `"<host>:"` prefix internally and
returns bare ids for that host only (un-prefixed legacy keys read as `hermes:`);
`mark_processed(session_ids, host)` adds the prefix before writing. Callers pass a host and
get bare ids back — no call site outside `state.py`/`fetch_sessions.py` builds or strips a
prefix itself. Governs R7.

### KTD6. `--db-path` becomes `--source`, keeping the old flag as an alias

`fetch_sessions.py --db-path` presumes a database. Renaming to `--source` (a DB path, a
directory, whatever the adapter wants) generalizes it, but the flag is **in the deployed cron
path** via `skill-evolution-fetch.sh`'s argument pass-through, so removing it would break
the nightly run. Keep `--db-path` as a hidden alias that warns on stderr. Governs R8.

## Requirements

- **R1.** A `HostAdapter` ABC plus a registry in a new `scripts/host.py`, with `resolve_host()`
  reading `SKILL_EVOLUTION_HOST` (default `hermes`) and raising on an unknown name.
- **R2.** A `HermesAdapter` reproducing today's behaviour byte-for-byte, and a
  `ClaudeCodeAdapter` reading `~/.claude/projects/**/*.jsonl` and `~/.claude/skills/*/SKILL.md`.
- **R3.** `iter_skills()` and `read_skill_body()` cover every skill read in the codebase;
  no module outside an adapter constructs a host path.
- **R4.** `iter_sessions()` output is byte-identical between hosts in *shape* — same keys,
  same types — verified by one test parametrized over both adapters.
- **R5.** Redaction and PII masking apply identically on every host, through the existing
  shared `_summarize_messages()`.
- **R6.** `scan_skills()`'s dot-prefix exclusion holds on any host layout.
- **R7.** Processed state is namespaced per host, reading un-prefixed legacy keys as `hermes:`.
- **R8.** The deployed cron path keeps working unchanged: `skill-evolution-fetch.sh` and
  `--db-path` continue to behave as they do today.
- **R9.** The write side is untouched. `apply_proposal()` still emits `skill_manage` dicts and
  is still Hermes-only, and says so when the resolved host is not `hermes`.

## Implementation Units

### U1. `scripts/host.py` — protocol, registry, resolver

**Goal:** The seam exists and is selectable, with Hermes behind it and nothing else changed.

**Files:** `scripts/host.py` (new), `tests/test_host_registry.py` (new).

**Approach:** ABC + `HOST_ADAPTERS` dict + `register_adapter()` + `resolve_host()` /
`get_adapter()`. Then `HermesAdapter`, whose three methods delegate to the *existing*
`fetch_sessions.fetch_sessions()`, `skill_index.scan_skills()` and the body-read logic
currently inline in `evaluate.installed_skill_body()`.

**Execution note:** Characterize first. Before moving any logic, pin today's observable
behaviour — one test asserting the exact key set and types `fetch_sessions()` emits against a
fixture DB, and one asserting `scan_skills()`'s output on a fixture tree including a
dot-prefixed category. These become the cross-host conformance tests in U4, so write them as
parametrized-over-adapter from the start with only `hermes` in the parameter list.

**Verification:** `SKILL_EVOLUTION_HOST` unset behaves exactly as before (the U1
characterization tests pass unchanged); an unknown host raises with the available names
listed, mirroring `call_provider()`'s message.

### U2. Route the four seams through the adapter

**Goal:** No module outside `scripts/host.py` names a Hermes path.

**Files:** `scripts/fetch_sessions.py`, `scripts/skill_index.py`, `scripts/state.py`,
`scripts/evaluate.py`, `scripts/optimize_skill.py`.

**Approach:** `fetch_sessions()` and `sessions_for_skill()` obtain rows via the adapter;
`scan_skills()` delegates to `iter_skills()`; `installed_skill_body()` and
`_installed_body()` become `get_adapter().read_skill_body(name)`; `state.py` applies the KTD5
prefix. Keep every public signature — these are imported across modules, including the 11 of
41 test files that monkeypatch a seam directly (see Risks for the exact list).

**Patterns to follow:** the local-import-then-attribute-call convention at
`evaluate.py:1103-1106` (inside `installed_skill_body()`), documented there precisely so
tests that monkeypatch `skill_index.scan_skills` still observe the patch. A module-scope
`from host import get_adapter` would bind past those patches. Follow the comment, don't
rediscover it.

**Execution note:** This unit's risk is not the new code, it is the 11 test files that
monkeypatch the old seams directly (plus any indirect breakage in the other 30). Run the full
suite after each file, not at the end.

**Verification:** Full suite green with `SKILL_EVOLUTION_HOST` unset. `grep -rn "\.hermes"
scripts/` returns hits only in `host.py`.

### U3. `ClaudeCodeAdapter`

**Goal:** A second host produces proposal-ready sessions and skills.

**Files:** `scripts/host.py`, `tests/test_host_claude_code.py` (new), fixtures under
`tests/fixtures/claude_code/`.

**Approach:** `iter_skills()` globs `~/.claude/skills/*/SKILL.md`, reusing
`parse_name_description_frontmatter()`, stamping `category: "user"` (KTD3), and skipping any
dot-prefixed skill-directory name — the flat tree has no category level to filter at, so R6's
dot-prefix exclusion must be re-applied one level down here instead (`Path.glob()` does not
exclude dot-prefixed entries on its own; verified live). `iter_sessions(since=...)` walks
`~/.claude/projects/*/*.jsonl`, groups by file (one file = one session), and per file:
`session_id` from `sessionId` (fall back to the filename stem); `started_at` from the first
record that actually carries a `timestamp` field — scanning forward past leading records that
don't (`custom-title`, `mode`, and similar small records commonly come first) rather than
trusting literally the first record, since two of three real files sampled would otherwise
read as epoch-zero and get silently dropped by any lookback filter; `title` from a
`custom-title` record else the first user message truncated; `model` from an assistant
record's `message.model`; `source` from `entrypoint`. Apply `since` as a cheap pre-filter
before the full per-file parse — peek the resolved `started_at` first and skip the file
entirely when it falls outside the window — mirroring the two-phase cheap/expensive split the
existing SQL path already has (`SESSION_QUERY` before `MESSAGE_QUERY`), since at this
machine's real scale (557 files, 257MB, up to 8MB each) parsing every message before filtering
would be a measurable regression from the SQL path this is meant to match. Map `type` → `role`
by allowlist (`user`/`assistant` only), drop everything else — including `system` and `mode`,
which carry no `message` field and would misparse under a denylist approach. Per KTD4, each
message's `content` is flattened before it reaches the shared summarizer: concatenate `text`
block text into `content`, and drop `tool_use`/`tool_result`/`thinking` block content entirely
rather than folding it in — `_summarize_messages()` has no use for anything beyond
`role`/`content`.

**Test scenarios:** happy path over a multi-record fixture; a file with no `custom-title`
(title falls back); a malformed JSON line (skipped, warned on stderr — never aborts the
walk); an empty file; a session whose only records are non-message types (yields nothing,
not a zero-message session); **a fixture containing `system` and `mode` records interleaved
with real messages** (both dropped, no `KeyError` on the missing `message` field, message
count reflects only `user`/`assistant` records — this is the scenario the original drop-list
would have failed); **a fixture whose `message.content` is a block list** (`text`, `thinking`,
`tool_use`, `tool_result` blocks, matching real record shape) — asserts no `AttributeError`
from `contains_secret()`, `text` blocks concatenate into `content`, and
`tool_use`/`tool_result`/`thinking` block content is dropped entirely rather than passed
through; **a session whose first several records have no `timestamp` field** (`started_at` resolves
from the first record that has one, not literally the first record); a secret and an email in
a message body (dropped / masked by the shared path, proving KTD4's wiring rather than
re-testing the redaction itself).

**Execution note:** Start from a failing test built on a *copied, redacted* fixture, not the
live `~/.claude/projects` tree — those transcripts are real user data and this repo's own
privacy posture (drop-on-secret, mask-on-PII) applies to its fixtures too.

**Verification:** `SKILL_EVOLUTION_HOST=claude_code python3 scripts/fetch_sessions.py --dry-run
| python3 scripts/analyze.py` produces formatted text with `analyze.py` unmodified.

### U4. Cross-host conformance test

**Goal:** "Same shape on every host" is enforced, not asserted in prose.

**Files:** `tests/test_host_conformance.py` (new).

**Approach:** Parametrize U1's characterization tests over both adapters against fixture
trees: identical key sets, identical value types, `category` always a non-empty string,
`messages` always a list of dicts, dot-prefixed directories always excluded.

**Execution note:** This is the unit that earns the plan. `CLAUDE.md`'s standing warning is
that *stubbed-dependency tests assert code against its own assumptions* — a per-adapter test
suite would let the two drift into different shapes while both stayed green. One test, both
adapters, one parameter list. A third host adds a line, not a file.

**Verification:** Deliberately break the Claude Code adapter's key set; the conformance test
fails naming the missing key.

### U5. Write-side guard and docs

**Goal:** The unbuilt half fails loudly, and the seam is documented for the next host.

**Files:** `scripts/proposal.py`, `CLAUDE.md`, `SKILL.md`, `README.md`,
`docs/pending-work-2026-07-28.md`.

**Approach:** `apply_proposal()` returns `can_apply: False` with an explicit reason when
`resolve_host() != "hermes"` — no host outside Hermes has an apply strategy yet, and silently
emitting `skill_manage` instructions no runtime can execute is the worse failure. Document
the two normalized contracts (session dict, skill dict) as the host-neutral interface, and
`SKILL_EVOLUTION_HOST` in `SKILL.md`'s env table. Update P2-2 to "read side done, write side
open" rather than closing it.

**Verification:** `SKILL_EVOLUTION_HOST=claude_code` + an approved proposal → `can_apply:
False` with the host named. Every `SKILL_EVOLUTION_*` var still documented (the sweep from
2026-07-28 stays green).

## Files

| File | Change |
|---|---|
| `scripts/host.py` | **new** — protocol, registry, resolver, both adapters |
| `scripts/fetch_sessions.py` | sessions via adapter; `--source` with `--db-path` alias |
| `scripts/skill_index.py` | `scan_skills()` delegates to `iter_skills()` |
| `scripts/state.py` | host-prefixed keys, legacy keys read as `hermes:` |
| `scripts/evaluate.py` | `installed_skill_body()` → adapter |
| `scripts/optimize_skill.py` | `_installed_body()`, `_find_skill_matches()` → adapter |
| `scripts/proposal.py` | non-Hermes host → `can_apply: False` |
| `tests/test_host_registry.py` | **new** — U1 |
| `tests/test_host_claude_code.py` | **new** — U3 |
| `tests/test_host_conformance.py` | **new** — U4, parametrized over both |
| `tests/fixtures/claude_code/` | **new** — redacted JSONL + flat skill tree |
| `CLAUDE.md`, `SKILL.md`, `README.md` | the seam, the two contracts, the new env var |

## Dependencies and sequencing

U1 → U2 → U3 → U4 → U5, strictly serial. U2 touches the modules U1 defines, U3 needs the
protocol, U4 needs two adapters to compare, U5 documents the result. **Do not parallelize:**
U2 alone rewrites the seam that 11 of 41 test files monkeypatch directly, so a concurrent
worker would be rebasing onto a moving target for no wall-clock gain on a five-unit plan.

## Scope Boundaries

**In scope:** the read side, one second host (Claude Code), the conformance test, the
write-side guard, docs.

**Not in scope:**
- **Any write path for a non-Hermes host** — the separate decision described in Context.
- **A Codex adapter.** The protocol should make it additive; proving that needs its session
  format inspected, which nothing here has done. Speculating a third adapter now would
  design against a guess.
- **Project-level Claude Code skills** (`.claude/skills/`). KTD3 reserves `category:
  "project"` for them, but resolving precedence when both trees hold the same name is a
  write-side question.
- **Merging sessions across hosts in one run.** One host per invocation; the env var is
  singular by design.
- **Changing anything downstream of the two contracts.** The evaluator registry, gate, size
  guards, history and provider layer are already host-neutral and this plan must not touch
  them — if a change there seems necessary, the abstraction is wrong.

## Verification Contract

```bash
# 1. No regression: the Hermes path is untouched
python3 -m pytest -q                                    # expect 533+ passed, 1 skipped
python3 scripts/fetch_sessions.py --dry-run             # same output as before the change

# 2. The seam is real
grep -rn "\.hermes" scripts/ | grep -v "^scripts/host.py"   # expect no matches

# 3. A second host reaches proposal-ready text with analyze.py unmodified
SKILL_EVOLUTION_HOST=claude_code python3 scripts/skill_index.py | head
SKILL_EVOLUTION_HOST=claude_code python3 scripts/fetch_sessions.py --dry-run \
  | python3 scripts/analyze.py | head -40

# 4. Fail-loud on a typo, listing what is available
SKILL_EVOLUTION_HOST=claud_code python3 scripts/skill_index.py   # expect a raise

# 5. The write side refuses rather than emitting unexecutable instructions
SKILL_EVOLUTION_HOST=claude_code python3 -c "..."       # can_apply: False, host named
```

## Definition of Done

1. `scripts/host.py` exists with the ABC, registry, `resolve_host()`, `HermesAdapter`, and
   `ClaudeCodeAdapter`.
2. No module outside `scripts/host.py` names a Hermes path or the SQLite schema.
3. One conformance test parametrized over both adapters asserts one shape.
4. Redaction and PII masking run through the single shared path on both hosts.
5. Processed state is host-namespaced and reads the deployed file's existing entries with no
   migration.
6. `apply_proposal()` refuses, with the host named, on a non-Hermes host.
7. `--db-path` still works from the deployed cron wrapper.
8. The full suite passes; every `SKILL_EVOLUTION_*` var is documented.
9. P2-2 reads "read side done, write side open".

## Risks

**The 11 test files that monkeypatch a seam directly** (`test_evaluate_baseline_trust.py`,
`test_evaluate_session_db_path.py`, `test_fetch_sessions_for_skill.py`,
`test_fetch_sessions_pii.py`, `test_fetch_sessions.py`, `test_optimize_skill_objective.py`,
`test_optimize_skill.py`, `test_robustness_gaps.py`, `test_skill_index.py`,
`test_state_pruning.py`, `test_state_schema_compat.py`) plus whatever indirect breakage
surfaces in the other 30. The likeliest way this plan breaks is not a design error, it is one
of these patching `fetch_sessions.STATE_FILE` or `skill_index.scan_skills` and no longer being
observed. Mitigation: keep public signatures, follow the local-import convention already
documented at `evaluate.py:1103-1106`, and run the full suite per file in U2 rather than per
unit.

**Claude Code's transcript format is undocumented and versioned** (records carry a `version`
key). It can change without notice. Mitigation: tolerate unknown record types and malformed
lines by skipping with a stderr warning, and derive only the six fields the adapter itself
must produce (`session_id`, `started_at`, `title`, `model`, `source`, `messages` — the shared
summarizer computes the remaining three) —
the narrower the read, the less there is to break.

**Over-abstraction.** Three methods is the whole protocol. If a fourth is needed to make
Claude Code work, that is evidence the two normalized contracts are not as neutral as the
code suggests — stop and re-examine rather than widening the interface.
