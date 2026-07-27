---
title: History and State Pruning - Plan
type: fix
date: 2026-07-26
topic: skill-evolution-history-state-pruning
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
---

# History and State Pruning: archive-and-auto-run, not delete-and-manual

> **Status as of 2026-07-27 — implemented as designed, with one deviation the plan itself
> flagged as a risk.** Both items shipped: `prune_history()` archives instead of deleting
> and runs automatically via `evaluate_and_record()`; `prune_processed()` prunes the
> flat-dict state shape with the `keep_ids` floor. All six verification scenarios in this
> plan's "Verification" section were re-run against real behavior (not just the test
> suite) and passed. Test suite: 443 passed with `gepa` (up from 404), 439 passed + 1
> skipped without.
>
> **The one deviation:** the plan said the pre-existing "never drops the most recent entry
> even at limit zero" test in `tests/test_evaluate_history.py` "must keep passing
> unmodified." It could not — that test's exact-count assertions describe the *old*,
> narrower invariant, and the new three-anchor rule (deliberately) retains more than one
> entry per target whenever the earliest and most-recent entries differ. Three existing
> tests were updated to assert the new, intentionally broader retention contract, with
> docstrings explaining why the counts changed, rather than forcing the old assertions to
> hold against a rule designed to supersede them.
## Context

Two "Smaller open items" in `docs/pending-work-2026-07-26.md`:

1. **`prune_history()` only deletes.** R22 (the eval plan) says pruned entries "may be
   archived or compacted"; today `prune_history()` rewrites the primary JSONL file keeping
   only survivors — dropped entries are gone, nowhere. It is also **only ever called
   manually**, via `evaluate.py --prune`; nothing in `apply_proposal()` or
   `retroactive_reevaluate()` calls it, so an unattended deployment that never runs
   `--prune` grows the file forever even with a retention policy configured.
2. **`processed_sessions` (the state file) grows unbounded.** No pruning exists at all.

Both fixes are opt-in (gated on env vars that default to "unbounded," matching every other
retention-shaped tunable in this repo) and are gate-side/state-side only — item 2 touches
the nightly cron path (it runs `fetch_sessions.py`), but item 1 never does, since the
evaluation gate only runs when a human calls `apply_proposal()` or `--retroactive`.

Two real correctness holes were found while designing this, both worth stating up front
since they shape the design:

- **Deleting entries can silently disable `RegressionEvaluator`.** It baselines against a
  target's last entry with `passed == True` (`evaluate.py:723`). `prune_history()`'s
  existing "always keep the most recent entry" guarantee keeps the chronologically *last*
  entry regardless of pass/fail — so a target with a passing baseline followed by several
  failing retries could have its one passing entry pruned while the failures survive,
  silently falling back to "no baseline yet" for exactly the targets that most need
  regression coverage.
- **`processed_sessions` pruning is self-defeating without a floor.** `mark_processed()`
  stamps every session in one call with the *same* `now()` value, so "keep the N most
  recent by timestamp" has no defined tiebreak among the batch just written — a naive
  recency-sort at a small retention count could drop most of the very entries a run just
  added, making those sessions look unprocessed again on the next run.

## Item 1: `scripts/evaluate.py` — archive on prune, auto-run per meaningful evaluation

### Archive, not compact

Compaction is deferred, not built: R22's wording is permissive ("archived or compacted"),
archiving already satisfies it and keeps the primary file bounded (the actual goal), and a
lossy "compacted summary" record would need to independently satisfy three different
readers (`original_size_for_target()`, `RegressionEvaluator`, `optimize_skill`'s
`find_low_scoring_targets()`) for no demonstrated need beyond what archiving already
provides.

`prune_history(path=None, retention=None)` gains an archive step:

- New env var `SKILL_EVOLUTION_HISTORY_ARCHIVE_PATH`. Default: **derived from the primary
  path's basename**, not a fixed filename — `eval_history.jsonl` → sibling
  `eval_history.archive.jsonl` in the same directory as whatever `path` actually resolves
  to (the parameter if given, else `get_history_path()`). Deriving from the basename rather
  than hardcoding matters because the primary path is itself overridable
  (`SKILL_EVOLUTION_HISTORY_PATH`) and the 8 existing tests pass a `tmp_path`-scoped custom
  `path=` — a fixed archive filename would still work today (there's exactly one canonical
  history file) but would silently collide if that ever changes.
- Only create/touch the archive file when something is actually pruned — an unbounded or
  no-op prune must not create a stray empty archive.
- **Write to the archive before rewriting the primary file**, not after — if the process
  dies mid-prune, "still in the primary, archive write pending" is recoverable on the next
  prune; "removed from primary, archive write failed" loses data permanently.
- Dropped entries are appended in their original relative order (readable, not shuffled).

### Two anchors closes both correctness holes, no archive-read fallback needed

`prune_history()`'s survivor rule extends from one always-kept entry per target to **up to
three, deduplicated when they coincide**:

1. The most recent entry (existing rule — needed by `optimize_skill.find_low_scoring_targets()`,
   which is structurally immune to everything else here since it already wants exactly
   this).
2. **The most recent *passing* entry** (new) — closes the `RegressionEvaluator` hole above
   directly, without `RegressionEvaluator` ever needing to read anything but the primary
   file.
3. **The earliest entry** (new) — closes the matching hole in `original_size_for_target()`:
   without it, a target's cumulative-drift baseline silently shifts forward every time
   pruning trims its oldest entries, quietly forgiving prior drift. Keeping the true first
   entry forever means the baseline never moves.

This was chosen over giving `original_size_for_target()`/`RegressionEvaluator` a fallback
read into the archive file: same safety guarantee, no second data-access path to build,
test, and maintain in two separate functions. Worst case this keeps up to 3 entries instead
of 1 per target regardless of the configured limit — negligible given per-target entry
counts are already small (single digits to tens, not thousands).

### Auto-run, but batched for `--retroactive`

`evaluate_and_record()` gains `auto_prune: bool = True`, calling `prune_history()` right
after its existing `append_history()` call. Since `prune_history()` early-returns when
`SKILL_EVOLUTION_HISTORY_RETENTION` is unset, this is a no-op by default — the same env var
now controls both *whether* pruning happens and that it happens automatically from now on;
`--prune` stays available for one-off backfill of pre-existing bloat.

`apply_proposal()` calls `evaluate_and_record(proposal)` with no extra kwargs today, so it
needs **zero changes** — it gets auto-prune-per-call for free via the new default.

`retroactive_reevaluate()`'s loop must **not** auto-prune per iteration: `prune_history()`
does a full read + rewrite of the *shared* JSONL (every target, not just the one being
re-evaluated), so N proposals would mean N full-file passes over a file that's shrinking
but starts large — real, avoidable I/O for a result identical to pruning once. Change its
loop to pass `auto_prune=False` and call `prune_history()` once after the loop.

### Files

- `scripts/evaluate.py`: `prune_history()` (archive step, three-anchor survivor rule, new
  env var + path derivation), `evaluate_and_record()` (`auto_prune` param), `main()`'s
  `--prune` branch (report the archived count too), `retroactive_reevaluate()` (batch the
  prune call).

### Tests

Extend `tests/test_evaluate_history.py`: archiving basics (dropped entries land in the
archive in original order; no archive file created when nothing was pruned; archive path
derives from a custom `path=`'s directory, not cwd; env var override; archive accumulates
across separate prune calls rather than truncating); the existing "never drops the most
recent entry even at limit zero" test must keep passing unmodified as the regression guard
that archiving didn't change that invariant; two new tests for the anchor extension — the
earliest entry survives aggressive count pruning, and the most recent *passing* entry
survives when it's followed by several pruned-into-oblivion failing retries (write this one
against `RegressionEvaluator.evaluate()` directly, not just against survivor counts, so it
actually pins the behavior that motivated the anchor).

New `tests/test_evaluate_history_autoprune.py`: `evaluate_and_record()` prunes
automatically once retention is configured, and is a no-op when it isn't (explicit
regression lock, since none of the 8 existing history tests set the env var);
`retroactive_reevaluate()` over several proposals calls `prune_history()` once, not once
per proposal (spy/count); `apply_proposal()`'s single-call path still prunes every call.

### Docs

`CLAUDE.md`'s evaluation-framework section (`prune_history()` paragraph) and
`docs/pending-work-2026-07-26.md`'s "prune_history() deletes only" item both need the
resolution recorded: archive chosen over compaction and why, the three-anchor rule and the
regression-baseline hole it closes, and that `SKILL_EVOLUTION_HISTORY_RETENTION` now
controls automatic pruning too. `docs/plans/2026-07-23-001-feat-skill-evolution-evaluation-plan.md`'s
status banner currently lists R22's archive path under "Not shipped, deliberately." Move
it to "Shipped."

## Item 2: `scripts/state.py` / `scripts/fetch_sessions.py` — prune the flat-dict shape only

### Why only the flat-dict shape

Two on-disk shapes exist (`CLAUDE.md`'s "Two on-disk shapes exist and both must be read").
The deployed, live-in-production shape is the flat `{session_id: iso_timestamp}` map — it
already carries the data pruning needs. The documented shape
(`{"processed_sessions": [...], "last_analyzed_at": ..., "version": 1}`) carries **no
per-session timestamp**, and — confirmed by reading `fetch_sessions()`'s only call site —
its list order isn't chronological either: `mark_processed(list(processed | new_ids))`
builds the list from a Python `set` union, whose iteration order is hash-based, not
insertion-based. So age- or recency-based pruning is structurally impossible for the
documented shape without a schema change, and this plan does not attempt one — CLAUDE.md
already cautions against reshaping the deployed file's semantics without confirming who
else depends on it, and the documented shape isn't observed live anywhere today (only
produced fresh). Pruning is real and immediate for the shape that's actually growing in
production; for the other, it's a clearly-warned no-op, not a silent gap.

### The `keep_ids` floor is structurally required, not just cautious

`prune_processed(retention=None, keep_ids=None)` (duplicated in both modules, matching the
existing convention — `get_state_file()`/`load_processed()`/`mark_processed()` are already
byte-for-byte duplicated between them) always retains every ID in `keep_ids` regardless of
the configured limit.

This isn't a defensive nicety by analogy to `prune_history()`'s "always keep the most
recent" — it's required by a specific mechanism: `mark_processed()` computes `now()` once
per call and stamps every session in the batch with that identical value
(`state.setdefault(sid, now)` in a loop). A "keep the N most recent by timestamp" sort has
**no tiebreak** among everything written in the same run, so without a floor, a small
retention count could drop most of the very batch just written. `fetch_sessions()` (the
only real orchestrator — `state.py` has none of its own, it's imported only by tests) is
the one place that knows which IDs are genuinely new this run
(`just_processed = {s["session_id"] for s in results}`, distinct from `new_processed`,
which is the full accumulated union) and calls `prune_processed(keep_ids=just_processed)`
immediately after `mark_processed(new_processed)`.

`prune_processed()` follows this module's own convention for path resolution — via
`get_state_file()` internally, no explicit `path=` parameter — rather than importing
`prune_history()`'s pattern from a module that doesn't use it elsewhere.

Confirmed placement is correct for `--dry-run`: `fetch_sessions()` returns before
`mark_processed()` is ever called when `dry_run=True`, so hooking the prune call
immediately after `mark_processed()` (not inside it, not inside `main()`) means it
structurally cannot fire during a dry run — no separate guard needed.

### The cross-run hole this doesn't close, and the one cheap mitigation that's in scope

`keep_ids` protects a run's own writes from itself. It does **not** protect against
retention being configured tighter than the effective lookback window across *separate*
runs: if a session's state entry is pruned after (say) 3 days, but a later run's
`--lookback-hours` still reaches that far back (a manual backfill, or a misconfiguration),
the session is simultaneously "looks unprocessed" and "still in scope" — it gets
re-fetched, re-marked, and re-analyzed, indefinitely, every time this recurs. There is no
clean structural fix for this without coupling retention to lookback (which would be a
larger, more opinionated change than this item calls for), so the mitigation in scope is a
warning, not a fix: when age-based retention (`"Nd"`/`"Nmo"`) is configured and converts to
fewer hours than the current run's `--lookback-hours`, `fetch_sessions()` prints one stderr
line naming the risk before proceeding. Count-based retention has no clean unit match to
compare against hours, so this check only applies to the age-based form — and count-based
retention has its own related, separately-documented limitation: `keep_ids` overriding the
limit means a busy day's batch can floor the file above the configured count, so count-based
retention is a poor fit for this file specifically; the docs steer toward the `"Nd"` form.

### Malformed timestamps: capped, not silent, not spammy

Age-based pruning treats an unparseable timestamp value as expired (pruned). Rather than
either extreme — one stderr line per malformed key (noisy on a large file) or one silent
summary (repeats `list_proposals()`'s exact mistake, which was fixed specifically because
"a file that errors gets looked at while a file that vanishes does not" — `CLAUDE.md:133`)
— name up to the first 5 offending IDs plus a "...and N more" tail in one line.

### The documented-shape warning fires every run, on purpose, from the orchestrator

Once retention is configured against a documented-shape file, `fetch_sessions()`'s hook
warns on stderr **every** run, not once — this is a persistent daily cron-cadence signal
that the operator's file genuinely cannot be bounded, not a hot-path concern. It fires from
the same `fetch_sessions()` hook (not only from the manual `--prune-state` handler), so the
unattended nightly cron path — not just a manual invocation — surfaces it.

### Manual escape hatch

`--prune-state` on `fetch_sessions.py`'s `main()`, printing a before/after count and
returning early without calling `fetch_sessions()` or emitting NDJSON — structurally like
`evaluate.py`'s `--prune`, but its report goes to **stderr, not stdout**: unlike
`evaluate.py`, `fetch_sessions.py`'s stdout is the NDJSON channel other tooling parses
(`CLAUDE.md`: "Nothing but `fetch_sessions.py` may write to stdout"). Worth stating
precisely so the stream choice isn't later "fixed" to match `evaluate.py`'s literal pattern.

### Files

- `scripts/state.py` and `scripts/fetch_sessions.py`: new `STATE_RETENTION_ENV_VAR`, a
  locally-duplicated retention parser (bare-int/`"Nd"`/`"Nmo"`, matching
  `evaluate._parse_retention()`'s syntax but not importing it — `state.py`/
  `fetch_sessions.py` sit below `evaluate.py` in this repo's import direction and must not
  invert it), and `prune_processed()` in both, byte-for-byte duplicated per the existing
  convention.
- `scripts/fetch_sessions.py`: the `fetch_sessions()` orchestration hook (`just_processed`,
  the `prune_processed(keep_ids=...)` call, the retention-vs-lookback-hours warning), and
  `main()`'s `--prune-state` flag.

### Tests

New `tests/test_state_pruning.py`, parametrized over `[state, fetch_sessions]` like the
existing `test_state_schema_compat.py` drift test: count- and age-based pruning on the
flat-dict shape; `keep_ids` survives even below the configured limit; malformed timestamps
are treated as expired with the capped-list warning; no-op when retention is unset; no-op
plus a per-call stderr warning on the documented shape (assert the file is byte-identical
before/after, and that calling twice warns twice); pass-through of `last_analyzed_at`/
`version` keys when present on a flat-dict file (per `load_processed()`'s existing
exclusion list, which implies this is possible); the shared retention-parsing syntax
matches `evaluate.py`'s; a defensive case for a pathological negative retention value still
respecting `keep_ids`.

Extend `tests/test_fetch_sessions.py`: pruning fires automatically after marking when
retention is configured, and is a no-op when it isn't; the `keep_ids` regression test
itself — seed several old already-processed sessions, run once with new sessions arriving
and a tight count-based retention, assert the just-fetched IDs survive even though a naive
recency-tiebreak would drop them; `--dry-run` never prunes (spy on `prune_processed`,
assert zero calls); the retention-vs-lookback-hours warning fires when configured smaller;
the cross-run reprocessing gap explicitly demonstrated as a documented limitation (not
silently left undiscovered) with a comment pointing at "retention should be ≥
`--lookback-hours`"; `--prune-state` reports to stderr and returns before touching the DB
or emitting NDJSON.

### Docs

`CLAUDE.md`'s `state.py`/`fetch_sessions.py` bullets and `SKILL.md`'s env var table get
`SKILL_EVOLUTION_STATE_RETENTION`, with the flat-dict-only scope stated plainly (not just
implied), the `keep_ids` mechanism's *reason* (the shared-timestamp tiebreak problem, not
just "mirrors eval history"), and the retention-should-be-≥-lookback-hours constraint.
`docs/pending-work-2026-07-26.md`'s "processed_sessions grows unbounded" item gets marked
resolved for the flat-dict shape, with the documented-shape limitation recorded as an
accepted, deliberate scope boundary rather than an oversight.

## Verification

```bash
pytest                                    # 404 with gepa (.venv), 400+1 skipped without
.venv/bin/python -m pytest -q
```

Then re-run the scenarios that motivated the design, against real behavior:

1. **Archiving works and is durable-ordered** — prune a small history file with a low
   count retention, confirm dropped entries appear in the archive in original order and
   the primary file only has survivors.
2. **The regression hole is closed** — build a target with a passing entry followed by
   several failing retries beyond the retention count, prune, confirm
   `RegressionEvaluator` still finds a real last-passing baseline (not "no baseline yet").
3. **The drift-baseline hole is closed** — same idea for `original_size_for_target()`: its
   earliest entry survives aggressive pruning.
4. **`--retroactive` batches the prune** — spy-count `prune_history` calls across a
   multi-proposal retroactive run with retention configured; exactly one call.
5. **The self-defeating loop is closed** — seed old processed sessions, run
   `fetch_sessions()` with new sessions arriving and a tight count retention; the
   just-fetched IDs are still marked processed afterward.
6. **The cross-run gap is real and warned-about, not fixed** — confirm the
   retention-below-lookback-hours warning actually fires when configured that way.

Both items are opt-in by default (unset env vars => today's exact behavior, byte-for-byte)
— running the full suite with no new env vars set is itself the regression signal that
nothing changed for anyone who hasn't configured either retention knob.
