# Plan: Evaluation coverage expansion (R5, R6)

> Created: 2026-07-27
> Status: **Shipped 2026-07-27** (commit `593c999`)
> Origin: `docs/pending-work-2026-07-26.md` P3 #1 ("Evaluation coverage expansion")

> **Status as of 2026-07-28.** All five units shipped. `evaluate_proposal()`,
> `evaluate_tool_calls()` and `evaluate_analyzer_prompt()` exist as sibling functions to
> `evaluate_skill_text()`, each with its own history namespace (`proposal:<uuid>`,
> `tool_calls:<session_id>`, `analyzer_prompt:<session_id>`), covered by
> `tests/test_evaluate_proposal_target.py`, `tests/test_evaluate_tool_calls_target.py` and
> `tests/test_evaluate_analyzer_prompt_target.py`. KTD1's seam constraint held: neither
> `evaluate_skill_text()` nor `apply_proposal()`'s call site was modified.
>
> **Two deviations from the text below, both deliberate.** U4 named the flag `--target
> <key>`; it shipped as **`--eval-target`** because `--target` was already taken by
> `--retroactive`'s filter, and reusing it would have made one flag mean two things.
> OQ3 resolved to a separate `--target skill|all` on `optimize_skill.py --list-candidates`
> rather than a shared vocabulary between the two scripts.
>
> **OQ1 resolved as "no".** The three new targets are observability-first: they write to
> history but do not gate auto-apply (KTD3). Wiring them into the gate remains an open
> decision — see `docs/pending-work-2026-07-28.md`.

## Goal

Add the three "fast-follow" evaluation targets originally scoped in the evaluation plan
(R5) but deferred until the v1 skill-text target proved the shared-core architecture.
Confirm KD2's "reuse without redesign" claim by extending the registry without
modifying `evaluate_skill_text()` or `apply_proposal()`'s call site.

## Problem frame

Today, only **one** target gets evaluated: skill text. `evaluate_skill_text()`
extracts the body/description change from a proposal, runs it through the 3-evaluator
registry, and returns results. The seam at `scripts/evaluate.py:941-945` says:

> A fast-follow target (e.g. `evaluate_tool_calls(session)`) can be added as a
> sibling function reusing the same registry and gate (run_evaluators) without
> modifying this function or apply_proposal()'s call site.

The seam exists. No targets use it. Three are scoped but unbuilt:

1. **proposal-quality** — rate the proposal as a whole (summary, rationale,
   proposed_changes), independent of the skill text it would change.
2. **tool-call-quality** — rate the quality of `tool_calls` recorded in the
   session messages that *produced* this proposal.
3. **analyzer-prompt-quality** — rate the analyzer's *generation step* itself:
   did the analyzer's LLM call produce a proposal that's well-grounded in the
   sessions it was given?

KD2 (eval plan line 109) says: **"One `Evaluator` + one provider-adapter layer, four
thin per-target integrations."** This plan implements the missing three.

R6 (line 70) says these read **already-recorded session transcripts**, not a live
re-run — so the input pipeline is `fetch_sessions.py` + `state.db`, already built.

## Non-goals

- **Heavier evaluation techniques** (Item 6: pairwise, debate/ensemble, embedding
  similarity, fine-tuned judges) — orthogonal; would produce different *scoring*,
  not different *targets*. Confirmed in conversation: "they sit at two independent
  axes" (skill text × technique). Defer until at least 2 targets are in production
  to validate the technique generalization.
- **Provider expansion** (Item 2: openai, gemini) — orthogonal; the new targets
  reuse the existing provider layer.
- **Multi-agent support** (Item 7) — orthogonal.
- **Auto-apply of the new targets** — the new evaluators write to history, but the
  v1 gate is wired only for skill text. Adding them to the gate is a separate
  decision (see Open Questions).

## Key Technical Decisions

### KTD1. Each target is a sibling function to `evaluate_skill_text()`, not a new branch in it.

- `evaluate_proposal(proposal) -> List[EvalResult]`
- `evaluate_tool_calls(session_id_or_messages) -> List[EvalResult]`
- `evaluate_analyzer_prompt(session_id, proposal_id) -> List[EvalResult]`

All three have the same signature shape and reuse `run_evaluators()` exactly the
way `evaluate_skill_text()` does. The `target` string differentiates the history
key (`proposal:<uuid>`, `tool_calls:<session_id>`, `analyzer_prompt:<session_id>`).

### KTD2. Each target needs its own extraction step, but the evaluators themselves are reused.

The 3 evaluators (deterministic, llm_judge, regression) are *content-agnostic* —
they accept any `(content, target, context)` triple. What's missing is what to
hand them:

| Target | Content | Baseline | target key |
|--------|---------|----------|------------|
| skill_text (v1) | body/description change | old body or installed file | `skill:<name>` |
| proposal | full proposal markdown | n/a (no baseline — first write) | `proposal:<uuid>` |
| tool_calls | JSON of tool_calls from session | n/a | `tool_calls:<session_id>` |
| analyzer_prompt | session message flow + the proposal it produced | n/a | `analyzer_prompt:<session_id>` |

`deterministic` is **disabled by default** for the new targets (per the env var
mechanism `SKILL_EVOLUTION_EVALUATORS` already provides) — its size/growth checks
are meaningless for non-skill-text content. A proposal that is 50KB isn't broken;
it's verbose. The judge + regression evaluators are the ones that should run.

### KTD3. New evaluators write to history but do **not** gate auto-apply.

The v1 auto-apply gate (`apply_proposal()` → `evaluate_and_record()` →
`combine_gate()`) is wired only for skill text. Adding the new targets to the
gate would either (a) widen what blocks apply without owner sign-off, or
(b) require re-tuning the gate strictness for content the owner hasn't seen fail
yet.

Instead, the new targets are **observability-first**: they write to history
(per `append_history()`'s existing behavior) and the existing
`find_low_scoring_targets()` / `optimize_skill.py --list-candidates` machinery
reads them out. An owner can spot-check by running `--list-candidates` and
decide later whether to wire them into the gate. **The plan to wire them into
the gate is explicitly a follow-up plan** (see Open Questions).

This avoids the evaluation-coverage expansion silently changing auto-apply
behavior — a non-trivial design choice worth a separate plan, not a default
fallout of this work.

### KTD4. Source-of-truth reads for tool_calls and analyzer_prompt come from `state.db`.

R6 mandates already-recorded session transcripts, not live re-run. The read
path is `fetch_sessions.py`'s `SESSION_QUERY` / `MESSAGE_QUERY` — they already
select `messages.tool_calls` (`fetch_sessions.py:289`) and the schema is
confirmed present. The new evaluation functions reuse these reads, not a new
DB connection.

To keep `evaluate.py` from acquiring a new `state.db` dependency (it has none
today), a thin pass-through helper lives in `proposal.py` or a new
`scripts/fetch_proposal_context.py`. The proposal already carries a
`session_ids` list, so the wiring is "given a proposal, fetch the session
messages that produced it, hand the relevant slice to the evaluator."

## Implementation Units

### U1. `evaluate_proposal(proposal)` — proposal-quality target

**Goal:** Score the proposal as a document — is the summary clear, is the
rationale grounded, are the proposed changes coherent with each other?

**Files:**
- `scripts/evaluate.py` (add `evaluate_proposal(proposal) -> List[EvalResult]`)
- `tests/test_evaluate_proposal_target.py` (new)

**Approach:**
- Extract content: `summary` + `rationale` + serialized `proposed_changes` as
  one combined markdown block. No baseline (no previous proposal to diff against
  in any meaningful sense).
- Target key: `proposal:<uuid>` (matches the existing `target_key_for_proposal()`
  fallback path — same string, so the new function can call it directly).
- Evaluators enabled by default for this target: `llm_judge`, `regression`
  (deterministic is opt-in only — see KTD2).
- Judge prompt: rate the proposal on a 3-rubric scale
  (`clarity`, `groundedness`, `coherence`), reusing
  `LLMJudgeEvaluator.parse_and_score()` to avoid reimplementing the parsing
  strictness.

**Test scenarios:**
- Happy path: a well-formed proposal (clear summary, evidence-backed rationale,
  coherent changes) returns a passing result.
- Edge: empty summary + empty rationale evaluates to the summary+rationale
  fallback content without raising.
- Edge: `regression` evaluator is run last with `new_score` set to the
  llm_judge's mean, exactly like `evaluate_skill_text()`.
- Edge: deterministic is skipped (not in the default `SKILL_EVOLUTION_EVALUATORS`
  for this target, so its absence is verified by the test).
- Wiring: the entry point is reachable without modifying
  `evaluate_skill_text()` or `apply_proposal()`.

**Verification:** A fixture proposal's `evaluate_proposal()` result is identical
in shape to a `evaluate_skill_text()` result (same `EvalResult` dataclass,
same `evaluator_name` values) so a single `run_evaluators()` works for both.

### U2. `evaluate_tool_calls(session_id_or_messages)` — tool-call-quality target

**Goal:** Score the tool-call quality of the session that produced this
proposal — did the agent select appropriate tools, sequence them sensibly, and
extract useful results?

**Files:**
- `scripts/evaluate.py` (add `evaluate_tool_calls(...)` + thin DB-read pass-through)
- `tests/test_evaluate_tool_calls_target.py` (new)
- `tests/conftest.py` (add a fixture for a small in-memory `state.db` if
  `tests/test_fetch_sessions.py` doesn't already provide one — verify before
  adding)

**Approach:**
- Input: a list of messages (caller decides whether to pass a session_id and
  have the helper query, or pass messages directly for tests). The helper
  function lives in `evaluate.py` to keep the DB-isolation story in one place.
- Extract content: JSON-serialized list of `tool_calls` from each message in
  the session, plus per-call result snippets (first 200 chars each, total
  cap 5KB) so the judge has context, not just a list of names.
- Target key: `tool_calls:<session_id>` — new key namespace, but
  `target_key_for_proposal()` doesn't produce this, so the function takes
  the session_id directly and builds the key itself.
- Evaluators enabled by default: `llm_judge`, `regression`.
- Judge prompt: rate tool-call quality on 3 rubrics
  (`selection`, `sequencing`, `result_extraction`).
- Reuse `fetch_sessions.py`'s DB read pattern (`SESSION_QUERY`,
  `MESSAGE_QUERY`); do not add a second sqlite3.connect site.

**Test scenarios:**
- Happy path: a session with 3 sensible tool calls returns a passing
  llm_judge result.
- Edge: a session with no `tool_calls` (the schema column is null) returns
  a deterministic "no tool calls to evaluate" passed=True result, so the
  history entry exists but doesn't penalize.
- Edge: a session with a malformed `tool_calls` JSON blob is handled
  defensively (per the same posture as `fetch_sessions.py`'s redaction
  paths — swallow and log, never crash the evaluator).
- Edge: the judge prompt receives redacted content
  (`redact_secrets()` applied first, matching the skill-text evaluator's
  posture at `evaluate.py:redact_secrets`).
- Wiring: the function is reachable without touching `evaluate_skill_text()`.

**Verification:** A fixture session's `evaluate_tool_calls()` runs against a
small in-memory `state.db` (or the existing fetch_sessions test fixture, if
present) and produces a result that the `--list-candidates` path can find.

### U3. `evaluate_analyzer_prompt(session_id, proposal_id)` — analyzer-prompt-quality target

**Goal:** Score the analyzer's generation step — given the session messages it
was handed, did it produce a well-grounded proposal (relevant, specific,
evidence-backed)?

**Files:**
- `scripts/evaluate.py` (add `evaluate_analyzer_prompt(...)`)
- `tests/test_evaluate_analyzer_prompt_target.py` (new)

**Approach:**
- Inputs: `session_id` and `proposal_id`. Read the session messages
  (truncated to first 300 chars per message, total cap 10KB) and the saved
  proposal markdown.
- Extract content: a structured block
  `[SESSION MESSAGES]\n...\n[PROPOSAL]\n...` so the judge sees both
  sides of the generation step.
- Target key: `analyzer_prompt:<session_id>` — keyed by session so multiple
  proposals from the same session share a lineage.
- Evaluators enabled by default: `llm_judge`, `regression`.
- Judge prompt: rate on 3 rubrics
  (`relevance`, `specificity`, `evidence_grounding`). Anti-verbatim-quote
  instruction (same as `optimize_skill._build_gepa_prompt` already does)
  to avoid the residual paraphrased-injection risk.
- Reuse `redact_secrets()` on the content before it leaves the machine.
- Reuse `fetch_sessions.py`'s DB-read pattern.

**Test scenarios:**
- Happy path: a session + a proposal grounded in it returns a passing result.
- Edge: a session + a proposal with no `session_ids` reference
  (proposal that the analyzer wrote without grounding) returns a low
  relevance score — this is the "spot the ungrounded proposal" use case.
- Edge: redaction is applied (verifiable by injecting a known secret
  into the fixture session and confirming the redacted version reaches
  the judge's prompt).
- Wiring: function is reachable without modifying `evaluate_skill_text()`
  or `apply_proposal()`.

**Verification:** A fixture session + proposal pair produces a result that
`--list-candidates` finds.

### U4. CLI: `--target <key>` flag for one-off evaluation

**Goal:** Let a human invoke any of the 4 targets on demand without going
through `apply_proposal()`.

**Files:**
- `scripts/evaluate.py` (extend `main()` argparse: add `--target skill|proposal|tool_calls|analyzer_prompt`,
  `--proposal-id`, `--session-id`)
- `tests/test_evaluate_cli.py` (extend existing CLI tests, or new file)

**Approach:**
- Today `scripts/evaluate.py` has `--list-evaluators`, `--retroactive`,
  `--prune`. Add `--target` (default: `skill`) plus
  `--proposal-id` / `--session-id` for the non-skill targets.
- No changes to `apply_proposal()` or `evaluate_skill_text()` — KTD1's
  hard constraint.
- Reuse the same `SKILL_EVOLUTION_EVALUATORS` env var; the per-target
  skip of `deterministic` is the user's responsibility via
  `SKILL_EVOLUTION_EVALUATORS=llm_judge,regression` (documented in
  `SKILL.md`'s env table).

**Test scenarios:**
- `--target proposal --proposal-id <uuid>` runs `evaluate_proposal()`
  against the saved proposal file and prints results.
- `--target tool_calls --session-id <id>` runs `evaluate_tool_calls()`
  against the session and prints results.
- `--target analyzer_prompt --session-id <id> --proposal-id <uuid>`
  runs `evaluate_analyzer_prompt()` and prints results.
- Invalid `--target` value raises (per the existing `--list-evaluators`
  posture: strict, never silent).

**Verification:** Running each `--target` value against a fixture produces
a non-empty JSONL history entry with the matching `target` key.

### U5. Documentation: update `SKILL.md` env table + CLAUDE.md

**Files:**
- `SKILL.md` (add 3 rows for the new env vars if any are added, or a
  paragraph under the "Evaluation" section describing the 4 targets)
- `docs/pending-work-2026-07-26.md` (mark P3 #1 as RESOLVED 2026-07-27,
  link to this plan)
- `CLAUDE.md` (update the "Evaluation framework" section with the 4
  targets and the new function names)
- `docs/pending-work.md` (add the evaluator's Fase 0-style evidence
  record — current real-evaluator pass stats per target)

**Approach:**
- `SKILL.md` env table already documents `SKILL_EVOLUTION_EVALUATORS` and
  `SKILL_EVOLUTION_<EVALUATOR>_PROVIDER` — no new env vars are needed
  for v1 of this plan (KTD2 keeps `deterministic` opt-in via the
  existing env var). One paragraph addition only.
- `CLAUDE.md` already documents the v1 evaluator + regression evaluator.
  Add 3 new bullet points describing the 3 new functions and their
  target keys, plus a "wiring posture" note that they write to history
  but do not gate auto-apply (KTD3).

**Test scenarios:** N/A (documentation only — verified by the agent
reviewing the diff).

## Files

- `scripts/evaluate.py` — add 3 new functions + thin DB-read pass-through
  for tool_calls; extend `main()` argparse
- `scripts/evaluate.py` — update the comment at line 941-945 to reflect
  that the 3 fast-follow targets now exist (so the next reader knows
  they're no longer just a comment)
- `tests/test_evaluate_proposal_target.py` (new)
- `tests/test_evaluate_tool_calls_target.py` (new)
- `tests/test_evaluate_analyzer_prompt_target.py` (new)
- `tests/test_evaluate_cli.py` (extend or new)
- `SKILL.md` — add 1 paragraph
- `docs/pending-work-2026-07-26.md` — mark P3 #1 resolved
- `CLAUDE.md` — add 3 bullets + 1 wiring-posture paragraph

## Open Questions

### OQ1. Should the new targets gate auto-apply?

**Default per KTD3: No — observability-first, wire to gate in a follow-up plan.**

This is the most consequential design choice. Wiring them to the gate would
either tighten what blocks apply (without owner sign-off on what "good
proposal-quality" means numerically) or relax today's strict-AND (which
silently weakens the existing guarantees). Either is reversible — a follow-up
plan can change the wiring — but the default should be conservative.

**Resolve before planning: Yes, this needs owner sign-off.** The plan to wire
them to the gate should be a separate document (likely a follow-up plan in
`docs/plans/`) that can be reviewed against actual evaluator data, not
proposed upfront.

### OQ2. Should `evaluate_tool_calls()` and `evaluate_analyzer_prompt()` read the DB directly, or go through `proposal.py`?

**Default per KTD4: Through a thin pass-through helper in `evaluate.py`,
reusing `fetch_sessions.py`'s query pattern, but not adding a sqlite3
dependency to `evaluate.py` proper.**

Cleaner alternative: route the read through `proposal.py` (which already
imports `fetch_sessions.py` indirectly via the proposal -> session_ids
flow). Cost: `proposal.py` is already a wide module; this would make
`evaluate.py` depend on `proposal.py` for reads, inverting the current
direction.

**Resolve during planning:** Defer the question — the pass-through helper
is small enough to extract later if needed. Document the seam in the code.

### OQ3. How should `--list-candidates` filter the 4 targets?

Today `optimize_skill.find_low_scoring_targets()` reads from history and
returns by `target` key. With 4 target namespaces now in the same JSONL
file, the default filter would return proposals, tool_calls, and
analyzer_prompt entries as if they were skill scores. That would mislead
the optimizer.

**Default:** add a `target_namespace` filter to `find_low_scoring_targets()`,
default `skill`, with the CLI exposed via `--list-candidates --target skill|all`.

**Resolve during planning:** Yes, this is small enough to fold into U4.

## Dependencies and sequencing

```
U1 (proposal) ─┐
                ├── can land in parallel — no shared state
U2 (tool_calls)─┤
                │
U3 (analyzer) ──┘
                │
                ▼
U4 (CLI + filter for --list-candidates) ← depends on U1-U3 to know
                │                            the 4 target keys
                ▼
U5 (docs)       ← depends on U1-U4
```

U1, U2, U3 are independent and can land in any order. U4 unblocks the CLI
+ filter. U5 is last.

**Test ordering:** Each U has a matching test file with at least 3 tests
(scenarios from the unit's "Test scenarios" section). U1, U2, U3 should
each pass `pytest` in isolation before moving to the next.

## Test coverage expectations

After this plan lands, `pytest` should report **~470 passed, 1 skipped** (current
456 + ~12-14 new tests across U1-U4). No new skips; the gepa-contract
module still skips when the optional extra is absent.

## Risks and mitigations

### R1. Heavier content → more tokens → provider cost

The new targets feed more text (proposal markdown, tool-call JSON,
session message flow) to the LLM judge. Each call is bounded (U1 ~2KB,
U2 ~5KB, U3 ~10KB caps) but cumulative cost is higher than the v1
skill-text evaluator.

**Mitigation:** All 3 new functions apply the same `redact_secrets()` step
that v1 already does (smaller, not larger blast radius). The caps are
chosen so a single evaluation costs roughly the same as one v1 call.
Document the per-target size caps in `SKILL.md` so a user can reason
about cost.

### R2. The `--list-candidates` filter change could break existing users

If a user has been running `--list-candidates` and getting a flat list of
low-scoring skills, narrowing the default to `target_namespace=skill` is
the right behavior — but it is a behavior change.

**Mitigation:** Make `--list-candidates` default to `skill` namespace
(matches today's effective output) but expose `--target all` for
exploration. Document the change in `SKILL.md`. The CLI is non-cron
in this repo, so the blast radius is the owner and any future
installs.

### R3. Adding 3 targets writes 3x more history entries per proposal

`append_history()` is append-only and per-target-key, so the primary file
grows faster. Pruning already handles this (R22, archive-before-rewrite
per `pending-work-2026-07-26.md`), so no new mechanism is needed —
but the recommended retention might want to come down.

**Mitigation:** Document the new growth rate. The `auto_prune=True`
default in `evaluate_and_record()` already prunes after every
skill-text evaluation; the new evaluators will inherit this only if
called through `evaluate_and_record()`. For v1 of this plan they
write directly via `append_history()` and don't auto-prune — that's
fine because the existing prune runs on the next skill-text
evaluation. No code change needed; just document the behavior.

## Acceptance examples (AE)

**AE1 (U1).** Given a fixture `improve_existing` proposal with a clear
summary and a grounded rationale, When `evaluate_proposal(proposal)`
runs with `SKILL_EVOLUTION_EVALUATORS=llm_judge`, Then the result has
`evaluator_name="llm_judge"` and a passing `score` in the rubric range
(0.7+).

**AE2 (U2).** Given a fixture session with 3 tool calls in
`state.db`'s `messages.tool_calls`, When `evaluate_tool_calls(session_id)`
runs with the same env, Then the result's `feedback` field contains a
judge verdict mentioning each tool by name.

**AE3 (U3).** Given a fixture session with a session_id and a saved
proposal that references it in `session_ids`, When
`evaluate_analyzer_prompt(session_id, proposal_id)` runs, Then the
result's `feedback` distinguishes grounded from ungrounded proposals
across the 3 rubrics.

**AE4 (U4).** Given the same fixture from AE1, When the user runs
`python3 scripts/evaluate.py --target proposal --proposal-id <uuid>`,
Then the printed output contains a JSONL entry with
`target="proposal:<uuid>"` and the entry appears in the history file.

## Verification

A fixture-driven pytest run that lands all 4 units and U5. The seam
constraint (KTD1) is verified by `git diff scripts/evaluate.py` showing
no modifications to `evaluate_skill_text()` or `apply_proposal()`'s call
site — both should appear unchanged except for the comment update at
line 941-945.
