---
title: Skill Evolution Pipeline Hardening - Plan
type: fix
date: 2026-07-25
topic: skill-evolution-hardening
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
---

> **Status as of 2026-07-26 — the checkboxes below were never maintained.** All four units
> shipped. Verified in [`../pending-work-2026-07-26.md`](../pending-work-2026-07-26.md).
>
> Two of this plan's own notes have since been overtaken. Its assumption A2 — that production
> still ran `ai-memory-stack`, so the column fix had no live blast radius — was true when
> written and is no longer: this repo is now the live pipeline (backlog P0-1). And its
> accepted convention of "no malformed-value guarding beyond `int()`'s natural exception" was
> reversed once that became true, since an unattended nightly run should not die on a typo'd
> env var (backlog P0-3).
>
> Still open from here: the three optimizer tunables it made configurable (3 / 20 / 20) have
> never been tuned against real usage.


# Skill Evolution Pipeline Hardening - Plan

## Goal Capsule

- **Objective:** Three targeted hardening fixes discovered while building the real GEPA optimizer (`docs/plans/2026-07-24-001-feat-real-gepa-optimizer-plan.md`): repair `fetch_sessions()`'s broken schema column references, close the one controllable channel for prompt-injection risk in GEPA's reflection step, and make the optimizer's tuning constants configurable via environment variables.
- **Product authority:** Session-settled through direct dialogue with the repo owner on 2026-07-25, scoped from `docs/pending-work.md`'s "Discovered while building GEPA, not fixed" section. The other six areas in that inventory (evaluation coverage, provider coverage, human-in-the-loop evaluator, skill quality tracking, production cron migration, heavier evaluation techniques) are not active scope here.
- **Open blockers:** None.

---

## Product Contract

### Summary

Fix `fetch_sessions()`'s column mismatch against the live `~/.hermes/state.db` schema so it actually runs; close the one controllable channel for prompt-injection risk in GEPA's reflective-mutation step (the judge's feedback text, plus a pre-draft structural check); and make the optimizer's three tuning constants overridable by environment variable, keeping their current values as defaults.

### Problem Frame

All three gaps surfaced during the real GEPA optimizer's implementation and code review (2026-07-24), and were deliberately left out of that work's scope rather than expanded into.

`fetch_sessions()`'s `SESSION_QUERY`/`MESSAGE_QUERY` select `created_at`/`total_tokens`, but the live schema has `sessions.started_at` (no `created_at`, no `total_tokens` at all) and `messages.timestamp` (no `created_at`) — verified directly against the real database. `sessions_for_skill()` (GEPA's own session query, added in the same work) was written against the correct columns from the start and doesn't share this bug.

GEPA's reflective-mutation step calls a reflection LM with a prompt `gepa` constructs internally; that prompt is forwarded to `evaluate.call_provider()` with no untrusted-content framing, unlike every other LLM-judge prompt in this codebase. The prompt itself can't be wrapped without risking breaking `gepa`'s own legitimate instructions embedded in it — but the judge's `feedback` field (which `gepa` embeds into that prompt as ASI) is fully within this codebase's control.

The optimizer's `MIN_SESSIONS`, `DEFAULT_MAX_METRIC_CALLS`, and `DEFAULT_MAX_SESSIONS_FOR_SKILL` were judgment calls made during planning, with no real-usage data behind them yet, and unlike nearly every other tunable value in this repo, none is overridable via a `SKILL_EVOLUTION_*` environment variable today.

### Requirements

**`fetch_sessions()` schema fix**

- R1. `fetch_sessions()`'s session and message queries select the live schema's actual columns (`sessions.started_at`, `messages.timestamp`) instead of the nonexistent `created_at`/`total_tokens`, so the function succeeds against the real database instead of raising `sqlite3.OperationalError`.
- R2. The lookback-hours cutoff filtering continues to work correctly, sourced from the corrected timestamp column.

**GEPA reflection-injection mitigation**

- R3. The GEPA candidate-scoring judge's prompt instructs it to produce `feedback` as its own quality assessment in its own words, never a verbatim quote of session content — reducing (not eliminating) the one channel through which session-derived text reaches `gepa`'s internal reflection prompt: the instruction is a soft, model-compliance-based control, not a hard technical boundary like the random-boundary wrapping used elsewhere in this prompt, so a paraphrased or judge-synthesized instruction-like `feedback` string is not blocked by it.
- R4. Before a GEPA run's winning candidate is drafted into a proposal, it passes the existing `DeterministicEvaluator`'s structural checks (size, growth-vs-baseline, valid YAML frontmatter), surfacing a clear error if the candidate is structurally broken, rather than only catching this later when a human reviews the proposal.

**Optimizer default configurability**

- R5. `MIN_SESSIONS`, `DEFAULT_MAX_METRIC_CALLS`, and `DEFAULT_MAX_SESSIONS_FOR_SKILL` are each overridable via a `SKILL_EVOLUTION_OPTIMIZER_<NOUN>` environment variable, following this repo's existing configuration convention, with their current values as defaults.
- R6. No numeric default value changes as part of this work — tuning those numbers is explicitly deferred until real usage data exists.

### Key Decisions

- KD1. **Fix `fetch_sessions()`'s column mismatch directly, not defer it.** Production's cron job currently points at a different repo (`ai-memory-stack`), not this one, so this fix carries no risk of unexpectedly activating a live pipeline. *(session-settled: user-directed — grouped explicitly with the other two items in this plan.)* Governs R1, R2.
- KD2. **Reflection-injection mitigation stops at what this codebase actually controls** (the judge's feedback framing, plus a pre-draft structural check) rather than attempting to wrap `gepa`'s own internal reflection-prompt construction, which isn't reachable without risking breaking `gepa`'s reflective-mutation mechanism. *(session-settled: user-directed — chosen over the more expansive "wrap the whole prompt" idea during dialogue, and over leaving this as a pure accepted risk with no code change.)* Governs R3, R4.
- KD3. **Optimizer defaults become configurable without changing their numeric values.** Actually tuning the numbers needs real usage data this plan doesn't produce. *(session-settled: user-directed — chosen over leaving this item out of scope entirely.)* Governs R5, R6.

### Acceptance Examples

- AE1. **Covers R1.** Given a real `~/.hermes/state.db`, When `fetch_sessions()` runs, Then it returns results without raising `sqlite3.OperationalError`.
- AE2. **Covers R4.** Given a GEPA run's winning candidate lacks valid YAML frontmatter, When the proposal-drafting step runs, Then it fails with a clear structural-validation message instead of drafting a broken proposal.
- AE3. **Covers R3.** Given session content containing an embedded instruction-like string, When the judge produces feedback, Then the feedback text does not contain a verbatim quote of that string.

### Scope Boundaries

**Deferred for later**

- The other six areas in `docs/pending-work.md` (evaluation coverage, provider coverage, human-in-the-loop evaluator, skill quality tracking, production cron migration, heavier evaluation techniques).
- Actually running the optimizer against real data to validate or tune the defaults R5 makes configurable — a separate operational follow-up, not a code deliverable here.
- Any further hardening of `gepa`'s reflection step beyond what's controllable from this codebase (see KD2). This includes a residual risk R3 does not close: the anti-quote instruction is a soft, model-compliance-based control on the judge's `feedback` field, not a hard technical boundary — a paraphrased or judge-synthesized instruction-like `feedback` string (not a verbatim quote) still reaches `gepa`'s internal reflection prompt unwrapped.

### Dependencies / Assumptions

- Assumes `scripts/analyze.py`'s existing `session.get("total_tokens", 0)` default (confirmed present) means dropping that key from `fetch_sessions()`'s output — since the live schema has no `total_tokens` column at all — degrades gracefully (prints "0") rather than breaking `analyze.py`.
- Assumes production's cron job still points at `ai-memory-stack`, not this repo (per `docs/pending-work.md`), so R1/R2's fix has no live-system blast radius.

### Sources / Research

- `scripts/fetch_sessions.py` — `SESSION_QUERY`/`MESSAGE_QUERY` column mismatch, verified directly against the live `~/.hermes/state.db` schema during the GEPA optimizer's code review (2026-07-24); `sessions_for_skill()` in the same file already uses the correct columns.
- `scripts/analyze.py:22` — confirmed `session.get("total_tokens", 0)` already defaults gracefully, so dropping that key from `fetch_sessions()`'s output is non-breaking.
- `scripts/optimize_skill.py` — `_reflection_lm_adapter()`, `_build_gepa_prompt()`, `score_candidate()`, `MIN_SESSIONS`, `DEFAULT_MAX_METRIC_CALLS`.
- `scripts/fetch_sessions.py` — `DEFAULT_MAX_SESSIONS_FOR_SKILL`.
- `scripts/evaluate.py` — `DeterministicEvaluator` (size/growth/frontmatter checks), reused for R4's pre-draft check.
- `docs/pending-work.md` — the source inventory this brainstorm scoped from.
- `docs/plans/2026-07-24-001-feat-real-gepa-optimizer-plan.md` — the plan whose implementation and code review surfaced all three items here; its U1 approach already flagged the `created_at`/`total_tokens` mismatch as a pre-existing defect in `fetch_sessions()`, out of that plan's scope.
- `~/.hermes/state.db` — live schema confirmed directly (`sessions.started_at REAL NOT NULL`, no `total_tokens` column; `messages.timestamp REAL NOT NULL`) during this plan's research.
- `tests/test_optimize_skill.py`, `tests/test_optimize_skill_evaluator.py`, `tests/test_fetch_sessions.py`, `tests/test_fetch_sessions_for_skill.py` — existing coverage and fixture patterns this plan's units extend.

---

## Planning Contract

**Product Contract preservation:** Unchanged — no R/KD IDs added, removed, or renumbered. The `Outstanding Questions` section is removed: both of its "Deferred to Planning" items are resolved below (KTD1, KTD2). This section, Implementation Units, Verification Contract, and Definition of Done are additive.

### Key Technical Decisions

- KTD1. **`fetch_sessions()`'s corrected output dict renames `created_at` to `started_at` (matching the corrected source column) and drops `total_tokens` entirely**, rather than keeping `created_at` for a smaller diff. `analyze.py` consumes neither key today, so the rename is non-breaking and avoids leaving a key name that no longer matches its source. *(session-settled: user-directed — chosen over keeping `created_at` for a smaller diff, which would leave the key name permanently mismatched with its source column.)* Governs R1.
- KTD2. **R5's environment variables are `SKILL_EVOLUTION_OPTIMIZER_MIN_SESSIONS`, `SKILL_EVOLUTION_OPTIMIZER_MAX_METRIC_CALLS`, and `SKILL_EVOLUTION_OPTIMIZER_MAX_SESSIONS_FOR_SKILL`** — the existing `SKILL_EVOLUTION_<NOUN>` convention (`evaluate.py`'s `SKILL_EVOLUTION_MAX_SKILL_SIZE_KB`, `SKILL_EVOLUTION_LLM_JUDGE_THRESHOLD`, etc.) with an `OPTIMIZER_` segment distinguishing optimizer-only tunables from the shared evaluation-gate ones. Governs R5.
- KTD3. **R4's structural check runs inside `draft_proposal_from_gepa_result()` itself**, so it applies to any caller of the proposal-drafting step and not only the CLI path, rather than only in `_run_skill()`. It re-resolves the skill's currently installed baseline size via `skill_index.scan_skills()` for the growth-vs-baseline comparison, skipping that part gracefully when the skill can't be resolved, rather than threading a new required parameter through `run_gepa_optimization()`'s return value — preserving that function's existing "returns the raw `gepa.GEPAResult`" contract the current test suite already depends on. *(session-settled: user-directed — chosen over gating the check behind the CLI path only, which would leave a direct/library caller of `draft_proposal_from_gepa_result()` unprotected.)* Governs R4.
- KTD4. **R3's anti-quoting instruction is added inside `_build_gepa_prompt()`'s own prompt text**, not `evaluate.py`'s shared `RUBRIC_JSON_RESPONSE_FOOTER`/`untrusted_content_framing()` helpers — those are also used by the unrelated proposal-review judge, and this instruction is specific to the channel through which GEPA's reflective-mutation step embeds judge feedback into its own internal prompt. Governs R3.

---

## Implementation Units

### U1. Fix `fetch_sessions()`'s schema mismatch

**Goal:** Make `fetch_sessions()`'s session and message queries match the live `~/.hermes/state.db` schema so the function runs without raising, and rename its output shape to match (KTD1).

**Requirements:** R1, R2 (per KTD1)

**Dependencies:** None

**Files:**
- `scripts/fetch_sessions.py` (`SESSION_QUERY`, `MESSAGE_QUERY`, `fetch_sessions()`)
- `tests/test_fetch_sessions.py` (fixture schema + assertions)

**Approach:**
- Point `SESSION_QUERY` at `sessions.started_at` (drop the nonexistent `total_tokens` column) and order/filter by it; point `MESSAGE_QUERY` at `messages.timestamp` instead of `created_at` — mirroring `sessions_for_skill()`'s own `SKILL_SESSION_QUERY`/`SKILL_MESSAGE_QUERY`, which already target the correct live columns.
- In `fetch_sessions()`, read the cutoff timestamp from `started_at` instead of `created_at`; keep the existing string/numeric dual-type handling as defensive code.
- Rename the per-session output dict's `created_at` key to `started_at` and drop `total_tokens` entirely, rather than defaulting it to `0` (KTD1) — `analyze.py` already tolerates a missing `total_tokens` key via `.get(..., 0)`.
- Update `test_fetch_sessions.py`'s fixture DB schema to match the live schema (`started_at REAL` and no `total_tokens` column on `sessions`; `timestamp REAL` on `messages`), and remove its docstring note describing the mismatch as a known, out-of-scope bug now that it's fixed.

**Patterns to follow:** `sessions_for_skill()`'s `SKILL_SESSION_QUERY`/`SKILL_MESSAGE_QUERY` (`fetch_sessions.py`) — already written against the correct live-schema columns.

**Test scenarios:**
- Happy path (Covers AE1): fixture DB matches the live schema; a session with messages returns without raising, with `started_at` (not `created_at`) and no `total_tokens` key in the result.
- Covers R2: a session whose `started_at` falls outside `--lookback-hours` is excluded; one inside the window is included.
- Regression: secret-containing message content is still redacted (existing test, ported to the corrected schema).
- Regression: a session with multiple messages still returns correct user/assistant counts and message ordering (existing "does not crash" test, ported to the corrected schema).

**Verification:** `pytest tests/test_fetch_sessions.py` passes against the corrected fixture schema. Recommended manual check (Covers AE1 against the real database, not just the fixture): `python3 scripts/fetch_sessions.py --dry-run` against the real `~/.hermes/state.db` completes with no `sqlite3.OperationalError`.

### U2. GEPA judge feedback: instruct against verbatim session quotes

**Goal:** Close the one controllable channel through which session-derived text could reach `gepa`'s internal reflection prompt via the judge's `feedback` field.

**Requirements:** R3 (per KTD4)

**Dependencies:** None

**Files:**
- `scripts/optimize_skill.py` (`_build_gepa_prompt()`)
- `tests/test_optimize_skill_evaluator.py`

**Approach:**
- Add a sentence to `_build_gepa_prompt()`'s own prompt text (KTD4) instructing the judge to write `feedback` as its own assessment in its own words, never a verbatim quote of the session excerpts — naming why (the feedback text becomes input to `gepa`'s own reflective-mutation prompt) so the instruction is self-explanatory to a future reader.

**Patterns to follow:** `_build_gepa_prompt()`'s existing use of `evaluate.untrusted_content_framing()`/`wrap_untrusted_block()` for the session-excerpts framing — this is a second, GEPA-specific framing sentence alongside it, not a replacement.

**Test scenarios:**
- Covers AE3 (prompt-construction proxy): `_build_gepa_prompt()`'s returned text instructs the judge not to quote or reproduce session content verbatim in its feedback. A unit test cannot compel actual model compliance, so this is the deterministic proxy — confirming the instruction reaches the prompt the judge sees.
- Regression: the existing boundary/framing tests (`test_prompt_framing_delimits_session_content_with_unpredictable_boundary`, `test_prompt_boundary_is_unpredictable_per_call`) still pass unmodified.

**Verification:** `pytest tests/test_optimize_skill_evaluator.py` passes, including the new prompt-instruction assertion.

### U3. Pre-draft structural validation for the winning GEPA candidate

**Goal:** Reject a structurally broken GEPA winning candidate before it becomes a proposal, instead of only catching it when a human reviews the saved proposal.

**Requirements:** R4 (per KTD3)

**Dependencies:** None

**Files:**
- `scripts/optimize_skill.py` (`draft_proposal_from_gepa_result()`)
- `tests/test_optimize_skill.py` (existing `draft_proposal_from_gepa_result()` fixtures + new negative-path tests)

**Approach:**
- At the start of `draft_proposal_from_gepa_result()`, before constructing the `SkillEvolutionProposal`, run `evaluate.DeterministicEvaluator().evaluate(result.best_candidate, context={"content_kind": "body", ...})` (KTD3) — the same size/growth/frontmatter checks `apply_proposal()` already runs on approval, applied earlier in the flow.
- Resolve a growth-vs-baseline comparison size by re-looking up the skill's currently installed `SKILL.md` via `skill_index.scan_skills()` (the same lookup `run_gepa_optimization()` already performs) and computing its UTF-8 byte length directly from the file content — matching `DeterministicEvaluator`'s own `size_bytes = len(content.encode("utf-8"))` computation — rather than reusing `scan_skills()`'s `size` field, which is a Python string length (character count, not a byte count) and would silently skew the growth percentage for any skill with non-ASCII content. When the skill can't be resolved, omit `baseline_size` from the context and let the size + frontmatter checks still run (KTD3 — best-effort, not a hard requirement).
- When the check fails, raise a clear, actionable error carrying the evaluator's feedback text (Covers AE2) instead of returning a proposal.
- Update the existing `draft_proposal_from_gepa_result()` tests in `tests/test_optimize_skill.py` to use frontmatter-shaped `best_candidate` fixtures (`"---\nname: ...\ndescription: ...\n---\n\n<body>"`) instead of plain prose, since this new check now runs on every call.

**Patterns to follow:** `evaluate.py`'s `DeterministicEvaluator` (reused, not reimplemented) and the `skill_index.scan_skills()` lookup pattern already used elsewhere in this file (`run_gepa_optimization()`).

**Test scenarios:**
- Covers AE2: a winning candidate with no YAML frontmatter raises a clear structural-validation error; no proposal is constructed.
- Edge: a winning candidate with frontmatter present but missing a required field (`name` or `description`) raises the same error path.
- Edge: a winning candidate whose size exceeds `SKILL_EVOLUTION_MAX_SKILL_SIZE_KB` raises with a size-limit message.
- Edge: a winning candidate whose growth exceeds `SKILL_EVOLUTION_MAX_GROWTH_PCT` over the resolved installed baseline raises with a growth-limit message (`skill_index.scan_skills()` mocked to a known baseline).
- Edge: the skill can't be resolved via `skill_index.scan_skills()` (e.g. renamed/removed) — the growth check is skipped gracefully; size + frontmatter checks still run, no crash.
- Regression: a structurally valid winning candidate still drafts a proposal exactly as before (all existing `draft_proposal_from_gepa_result()` tests, updated to frontmatter-shaped fixtures).

**Verification:** `pytest tests/test_optimize_skill.py -k draft_proposal_from_gepa_result` passes, including the new structural-validation negative paths.

### U4. Optimizer default configurability via environment variables

**Goal:** Make `MIN_SESSIONS`, `DEFAULT_MAX_METRIC_CALLS`, and `DEFAULT_MAX_SESSIONS_FOR_SKILL` overridable by environment variable, with today's values as defaults (KTD2).

**Requirements:** R5, R6 (per KTD2)

**Dependencies:** None

**Files:**
- `scripts/optimize_skill.py` (`run_gepa_optimization()`, `_run_skill()`)
- `scripts/fetch_sessions.py` (`sessions_for_skill()`)
- `tests/test_optimize_skill.py`
- `tests/test_fetch_sessions_for_skill.py`

**Approach:**
- Resolve each of the three tunables from its env var at the point of use (matching `evaluate.py`'s `os.environ.get(<VAR>, <default-constant>)` convention), falling back to the existing module-level constant when unset — not baked into a function's default-argument value, which would only be read once, at import time.
- `SKILL_EVOLUTION_OPTIMIZER_MIN_SESSIONS` overrides `MIN_SESSIONS` in `run_gepa_optimization()`'s not-enough-history check (and `_run_skill()`'s message, so the reported threshold matches what actually ran).
- `SKILL_EVOLUTION_OPTIMIZER_MAX_METRIC_CALLS` overrides `DEFAULT_MAX_METRIC_CALLS` as the `max_metric_calls` fallback when `--iterations` is not given; an explicit `--iterations` still takes precedence over both (R6 — no default value itself changes).
- `SKILL_EVOLUTION_OPTIMIZER_MAX_SESSIONS_FOR_SKILL` overrides `DEFAULT_MAX_SESSIONS_FOR_SKILL` as `sessions_for_skill()`'s session cap when no explicit `max_sessions` argument is passed.
- No malformed-value guarding beyond `int()`'s natural exception — matches this repo's existing convention (`evaluate.py`'s `float()` conversions have no try/except either).

**Patterns to follow:** `evaluate.py`'s `MAX_SKILL_SIZE_KB_ENV_VAR`/`MAX_GROWTH_PCT_ENV_VAR`/`LLM_JUDGE_THRESHOLD_ENV_VAR` — same `SKILL_EVOLUTION_<NOUN>` naming convention, same `os.environ.get(..., default)` resolution at point of use.

**Test scenarios:**
- Regression: each env var unset — `run_gepa_optimization()`/`sessions_for_skill()` behave exactly as before (existing `test_run_gepa_optimization_happy_path`, which asserts against `optimize_skill.DEFAULT_MAX_METRIC_CALLS`, continues to pass unmodified).
- `SKILL_EVOLUTION_OPTIMIZER_MIN_SESSIONS=5` set: a skill with 4 recorded sessions now returns `NotEnoughHistoryResult`; a skill with 5 proceeds.
- `SKILL_EVOLUTION_OPTIMIZER_MAX_METRIC_CALLS=50` set, no `--iterations` given: the resolved `max_metric_calls` is 50.
- An explicit `--iterations` argument still overrides `SKILL_EVOLUTION_OPTIMIZER_MAX_METRIC_CALLS` when both are set (precedence: explicit arg > env var > default).
- `SKILL_EVOLUTION_OPTIMIZER_MAX_SESSIONS_FOR_SKILL=5` set, more than 5 matching sessions exist: `sessions_for_skill()` returns only the 5 most recent.

**Verification:** `pytest tests/test_optimize_skill.py tests/test_fetch_sessions_for_skill.py` passes, including the new env-var override and precedence scenarios.

---

## Verification Contract

| Applies to | Command | Done signal |
|---|---|---|
| U1-U4 | `pip install -e ".[dev]"` then `pytest` | Full suite passes |
| U1 (Covers AE1) | `pytest tests/test_fetch_sessions.py` | Corrected-schema fixture scenarios pass; recommended manual `python3 scripts/fetch_sessions.py --dry-run` against the real `~/.hermes/state.db` completes with no `sqlite3.OperationalError` |
| U2 (Covers AE3) | `pytest tests/test_optimize_skill_evaluator.py` | Anti-quote prompt instruction and existing framing regressions pass |
| U3 (Covers AE2) | `pytest tests/test_optimize_skill.py -k draft_proposal_from_gepa_result` | Structural-validation negative paths and updated regression fixtures pass |
| U4 | `pytest tests/test_optimize_skill.py tests/test_fetch_sessions_for_skill.py` | Env-var override and precedence scenarios pass |

---

## Definition of Done

- [x] R1, R2 — U1 implemented and tested; `fetch_sessions()` runs against a schema-correct fixture and (recommended) the real `state.db` without raising.
- [x] R3 — U2's anti-quote instruction confirmed present in the GEPA judge prompt; existing framing regressions still pass.
- [x] R4 — U3's pre-draft structural check confirmed on all four failure paths (missing frontmatter, missing required field, size limit, growth limit) plus the resolvable/unresolvable-baseline paths.
- [x] R5, R6 — U4's three env var overrides confirmed with unset (regression) and set (override + precedence) coverage; no numeric default value changed.
- [x] All Acceptance Examples (AE1-AE3) pass as tests.
- [x] No abandoned-attempt code remains (e.g. leftover plain-prose candidate fixtures in `tests/test_optimize_skill.py` once U3's frontmatter-shaped fixtures replace them).
- [x] `pytest` passes clean end to end.
