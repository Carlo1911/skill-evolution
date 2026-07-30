# Pending Work — what is left to implement, as of 2026-07-28

Built by reading every tracked document (`CLAUDE.md`, `README.md`, `SKILL.md`,
`docs/integration-plan.md`, `docs/session-analyzer-prompt.md`, `docs/cron-job-prompt.md`,
all seven plans under `docs/plans/`, and both prior pending-work files) and cross-checking
each claim against the code at `6cd1d21`.

**Relationship to the two older files.** `pending-work.md` is the **evidence record** — the
Fase 0 validation narrative, the measured numbers, the post-mortems. `pending-work-2026-07-26.md`
was the consolidated backlog; its P0–P2 sections are all closed and its P3 table now has
only three genuinely-open rows. **This file is the live inventory**; those two are history
and are referenced rather than restated.

Verified state at the time of writing:

- Test suite: **511 passed** with `gepa` installed, **507 passed + 1 skipped** without.
- Providers: five registered (`claude`, `ollama`, `opencode`, `openai`, `gemini`).
- Evaluation targets: four (`skill text` gating; `proposal`, `tool_calls`,
  `analyzer_prompt` observability-only).
- Every `SKILL_EVOLUTION_*` variable read by a script is documented in `SKILL.md`.
- No known P0: nothing here is a production defect, security gap, or data-loss risk.

This is a **decision inventory, not a committed roadmap.** Pick an item, then run it through
`ce-brainstorm`/`ce-plan` before building.

> **Amended 2026-07-29.** Reviewing the first two proposals the live cron produced turned up
> four defects in shipped code that no test caught, because every test for the affected paths
> injects its inputs instead of exercising them. The "no known P0" line above was written on
> 2026-07-28 and is **no longer true** — see P0-1 through P0-4. Two of them make claims this
> repo's own documentation had already recorded as working.

---

## P0 — Shipped code that did not work — ALL RESOLVED 2026-07-29

Found by running the documented commands against the real tree, not by reading. **A fifth
defect (P0-5) surfaced only because fixing P0-1 made the broken path reachable** — the DB
read started working and immediately proved the extraction downstream of it was reading a
shape no real row uses.

The pattern connecting all five: every affected path is exercised in tests only through
*injected* inputs, in shapes that documentation described but production never produced. Test
suite went 507 → 533.

### ~~P0-1. `evaluate_tool_calls(session_id)` and `evaluate_analyzer_prompt()` raise `ImportError`~~ — RESOLVED

**Fix:** `fetch_sessions.get_state_db_path()` added, shaped exactly like the existing
`get_state_file()` — env read at call time via the new `SKILL_EVOLUTION_DB_PATH`, blank
ignored, fallback to the module global so monkeypatching `DEFAULT_DB_PATH` still works.
Covered by `tests/test_evaluate_session_db_path.py`, which exercises the DB branch itself
rather than the injection path that hid the bug.

`_fetch_session_messages()` does `from fetch_sessions import get_state_db_path` —
**a function that does not exist.** `fetch_sessions.py` defines `get_state_file()` (the JSON
state path) and `DEFAULT_DB_PATH`, never `get_state_db_path()`. Every call that reaches the
DB dies:

```
ImportError: cannot import name 'get_state_db_path' from 'fetch_sessions'
```

So two of the three evaluation targets that shipped 2026-07-27 have never worked against a
real session. Their tests pass because they take the documented list-of-messages input
instead of a session id — the injection path — leaving the DB branch unexecuted. This is
exactly the blind spot `CLAUDE.md` warns about for stubbed dependencies, in a new place.

Fix: add `get_state_db_path()` to `fetch_sessions.py` (honouring an env override, the way
`get_state_file()` does) and a test that calls `evaluate_tool_calls()` with a session id
against a fixture DB.

### ~~P0-2. `--eval-target proposal --proposal-id <id>` crashes on the id its help text asks for~~ — RESOLVED

**Fix:** `_load_proposal_by_id_or_path()` tries the argument as given, then resolves it
against `get_proposals_dir()` (appending `.md`), and the history key now comes from
`p.proposal_id` rather than the CLI argument — so a path can no longer land in the target
namespace. The first implementation pre-checked with `os.path.isfile()` and broke
`tests/test_evaluate_cli.py`, which stubs `load_proposal`; attempting the load first is both
simpler and keeps that seam testable. Covered by
`tests/test_evaluate_proposal_id_resolution.py`.

`proposal.load_proposal()` takes a **path**; the CLI passes `args.proposal_id` straight into
it, so a bare id raises `FileNotFoundError`. The guard immediately below —
`if not p: print("Proposal not found: ...")` — can never fire, because `load_proposal()`
raises rather than returning `None`.

Passing a path "works" but is worse than it looks: the history key becomes
`proposal:docs/skill-evolution/proposals/20260729-001.md`, so a filesystem path lands in the
target namespace that `RegressionEvaluator` and `find_low_scoring_targets()` both key on.

Fix: resolve the id against `get_proposals_dir()` before loading, and either catch
`FileNotFoundError` or give `load_proposal()` a `None`-returning sibling so the existing
guard means something.

### ~~P0-3. The deterministic size guards silently no-op on real analyzer output~~ — RESOLVED

**Fix:** `_extract_evaluated_content()` now selects by *kind* rather than list position
(`body` beats `description`, so a `create_new` listing them in the other order no longer has
its body ignored), and returns `MALFORMED_CONTENT_KIND` when a body/description change carries
no `new_value` instead of falling through to the summary. `evaluate_skill_text()` short-circuits
on that with a failing `structure` result — fail closed, and no provider call spent scoring
prose that is not the proposed change. The `summary_rationale` fallback stays scoped to
`merge_skills`/`deprecate_skill`, which genuinely have no body field.

**A second-order hazard was found while fixing this, not by the review:**
`evaluate_and_record()` persists `content_size`, and `original_size_for_target()` reads the
earliest one back as the cumulative-drift baseline — so recording the ~110-byte error message
would have become "where this skill started", making any later real body read as
several-thousand-percent growth. A malformed proposal now records no sizes at all. Covered by
`tests/test_evaluate_malformed_changes.py` and `tests/test_evaluate_malformed_history.py`,
including an end-to-end test that the ratchet now actually rejects growth on an oversized skill.

**This is the consequential one.** `_extract_evaluated_content()` returns the first
`proposed_changes` entry whose field is `body` or `description` **and whose `new_value` is
truthy**; failing that it falls back to `(summary + rationale, "summary_rationale", None)`.
Both proposals the cron produced on 2026-07-29 hit that fallback, and the gate passed both:

| Proposal | What the gate scored | What it would actually change |
|---|---|---|
| `20260729-001` | 63 bytes of summary text, `content_kind=summary_rationale` | +~500 B on a **19,018-byte** installed skill — already 3.6 KB over the 15 KB cap |
| `20260729-002` | 238 bytes of `description` | a new skill whose `body` `new_value` is the literal placeholder `"See proposal body for full draft content"` |

Because `content_kind` is not `body`, the frontmatter check is skipped; because
`_resolve_baseline()` only resolves for body changes, `baseline_size` is `None` and **every
growth, shrink, byte-floor and cumulative check is inert.** The ratchet — the guard whose
entire purpose is to stop an already-oversized skill from growing — never ran on a proposal
that grows an already-oversized skill.

Two independent causes, both needing a fix:

1. **The analyzer does not populate `new_value` for body changes.** The prompt asks for it
   (`session-analyzer-prompt.md:80`), the model wrote prose in the markdown body instead, and
   nothing validates the structured field. A proposal whose body change carries no `new_value`
   is not evaluable as a body change and should be **rejected as malformed**, not silently
   downgraded to scoring its own summary.
2. **The fallback is too quiet and order-dependent.** It exists for `merge_skills`, which
   genuinely has no single body field — but it currently also absorbs a *broken*
   `improve_existing`. And because the scan returns the first match, a `create_new` proposal
   listing `description` before `body` gets its description scored and its body never looked
   at (`20260729-002` exactly).

Fix: reject a body/description change with an empty `new_value` rather than falling through;
prefer `body` over `description` when both are present; keep the `summary_rationale` fallback
scoped to proposal types that legitimately lack a body field. Add a test asserting a
`new_value`-less `improve_existing` fails the gate.

### P0-4. Both live proposals are unapplicable as written

Downstream of P0-3, and worth stating separately because it is what a reviewer actually hits:

- **`20260729-001`** — sound, well-evidenced analysis (a real recurring cron failure,
  corroborated by persistent memory), but carries **no `new_value`**, so there is no machine-
  applicable change: `apply_proposal()` would emit a `patch` instruction with nothing to
  patch. It also targets a skill 3.6 KB over the cap, so once P0-3 is fixed the ratchet will
  correctly reject it until the skill is split.
- **`20260729-002`** — its `body` `new_value` is a placeholder string. Applying it would
  create a skill whose entire body reads `See proposal body for full draft content`.

Neither is a *reasoning* failure; both are the structured-field/prose mismatch in P0-3.

**Status:** the gate now rejects both correctly, so they are no longer a *safety* problem —
but they are still **unapplied and unapplicable**, and that is the open part. `20260729-001`'s
analysis is worth keeping: to act on it, the body change needs real `new_value` text, and its
target skill (19,018 B, 3.6 KB over the cap) needs splitting by hand first, since the ratchet
correctly refuses to let an oversized skill grow. `20260729-002` needs its placeholder body
replaced with the draft that currently lives only in the proposal's prose.

### ~~P0-5. `evaluate_tool_calls()` extracted nothing from real sessions~~ — RESOLVED

**Only visible once P0-1 was fixed.** With the DB path working, a real 281-message session
(133 of them carrying tool calls) produced 137 snippets that were *all* empty —
`{"name": "", "arguments": {}, "result": ""}` — and handed the judge **14 bytes**.

Hermes stores OpenAI function-call objects, where the name and arguments are nested under
`function` and `arguments` is itself a JSON string:

```json
{"id": "call_00_...", "type": "function",
 "function": {"name": "cronjob", "arguments": "{\"action\": \"list\"}"}}
```

The extraction read `call["name"]` and `call["arguments"]` at the top level — the *flat* shape
the tests inject and no real row uses. So the target read the DB and scored nothing, which is
worse than failing: it wrote `passed: true` history entries carrying no signal.

**Fix:** `_normalize_tool_call()` handles both shapes, decodes string-encoded `arguments` so
the judge sees a real object rather than escaped noise, and returns `None` for a call with no
recoverable name — padding the payload with empty objects both faked coverage and consumed the
5 KB budget. Verified against the same real session: **14 bytes → 6,069 bytes**, with
`cronjob`, `read_file`, `search_files`, `terminal`, `tool_search` all present. Covered by
`tests/test_evaluate_tool_calls_real_shape.py`.

**The generalizable lesson**, worth carrying into the host-agnostic work: a documented data
shape is not evidence. `analyzer_prompt` reads the same messages and should be spot-checked
against a real session before it is trusted.

---

## P1 — Blocks distribution or leaves a shipped feature unproven

### P1-1. The repo has a remote and has been pushed — still private

`origin` is `git@github.com:Carlo1911/skill-evolution.git`, closing the last step of
`integration-plan.md` Fase 4. The install URLs in `README.md` and `SKILL.md` carry the real
path instead of an `<owner>` placeholder.

**Mind the name mismatch:** the GitHub repo is `skill-evolution`, the local directory and the
skill's own `name:` field are `hermes-skill-evolution`. The install URL must use the *repo*
name; the skill keeps its own. `SKILL.md` says so inline, because getting this backwards
yields a 404 that looks like a permissions problem.

Settled at the same time, since a permissive licence does not by itself preserve the author's
future options: the `LICENSE` copyright and `SKILL.md`'s `author:` field said
"Nous Research Community", attributing this work to an organisation that did not write it.
Both now name the actual author. MIT stays — it is already declared in `LICENSE`,
`pyproject.toml`, `README.md` and `SKILL.md`, and for a community skill maximum adoption is
the point. The Nous credit remains where it is accurate: inspiration, in the README's About
section.

**Amended 2026-07-29 (later same day).** This section previously claimed "nothing has been
pushed yet." That was wrong at the time of writing — `git reflog show origin/main` records
`update by push` at 2026-07-29 18:55:08, and `origin/main` already matches local `HEAD`
(`84c0869`). Verify push state with `git log origin/main..HEAD` (empty = nothing to push)
rather than trusting this file's prose, since this is exactly the kind of claim that goes
stale the next time someone pushes.

**One thing still gates an actual publication:** the repo is **private** —
`https://github.com/Carlo1911/skill-evolution` returns 404 unauthenticated, and the GitHub
account currently authenticated in `gh` (`CaT-Carlo19`) cannot see it either, confirming it
isn't just an unauthenticated-view artifact. It must be made **public** for
`hermes skills install <raw-url>` to work for anyone else. That is an owner action in GitHub
repo settings, not something to script.

### P1-2. Neither new provider has been exercised against a live endpoint

`openai` and `gemini` shipped with 23 unit tests between them, but every one stubs
`_post_json`, so nothing has confirmed the real request shape is accepted, the real
response shape is what the parser expects, or that the auth header is honoured. Two
specific unverified assumptions:

- Gemini's `x-goog-api-key` header survives urllib's `str.capitalize()` normalization
  (`x-goog-api-key` → `X-goog-api-key`). This relies on HTTP header case-insensitivity,
  which is correct per spec but untested against Google's gateway.
- Gemini's `v1beta` surface and `gemini-2.0-flash` model id are current.

Precedent for why this matters: the OpenCode Zen adapter's **User-Agent 403** was invisible
to unit tests and only appeared against the real gateway. Ollama and OpenCode Zen have both
since been verified end to end; **Claude has not** (no `ANTHROPIC_API_KEY` in this
environment), so three of five adapters are now live-verified and two are not.

Cost to close: one real judge call per provider, with credentials.

---

## P2 — Capability gaps with clear scope

### P2-1. The three new evaluation targets do not gate anything

`evaluate_proposal()`, `evaluate_tool_calls()` and `evaluate_analyzer_prompt()` write to
history and are inspectable via `--eval-target` and `--list-candidates --target all`, but
`apply_proposal()`'s gate is still skill-text-only. This was a deliberate call (the
coverage-expansion plan's KTD3/OQ1: widening what blocks auto-apply needs owner sign-off,
so they shipped observability-first), and it is explicitly recorded as a follow-up decision
rather than an oversight.

Open questions if it is taken up: which targets should be blocking vs advisory, whether
they get their own strictness setting, and what a `tool_calls` failure should mean for a
proposal whose *content* is fine.

### P2-2. Host-agnostic / multi-agent support

The owner's stated principle is that the more agnostic the design, the better, and
multi-agent support (Codex, Claude Code, future hosts) is an explicit project goal. The
coupling is already mapped to exactly four seams, each isolated in one module:

| Seam | Module | What is Hermes-specific |
|---|---|---|
| Session source | `fetch_sessions.py` | `~/.hermes/state.db` and its SQLite schema |
| Skill source | `skill_index.py` | `~/.hermes/skills/<category>/<skill>/SKILL.md` |
| Processed state | `state.py` | `~/.hermes/skill_evolution_state.json` |
| Mutation tool | `proposal.py` | emits `skill_manage` instruction dicts |

Everything else — evaluator registry, gate, size guards, history, provider layer, analyzer
prompt — is already host-neutral, because it operates on skill *text* and proposal *shape*,
not provenance.

**The read side generalizes cheaply; the write side is the expensive half** — no host
outside Hermes exposes `skill_manage`, so each new host needs its own apply strategy. A
first increment could be read-only support (ingest another host's sessions and skills,
produce proposals) without touching the write side at all.

### P2-3. Skill quality tracking (`integration-plan.md` Fase 2)

Neither `dataset_builder.py` (synthetic eval datasets) nor `skill_quality.py` (periodic
tracker/reporter) exists. Fase 2's stated deliverable was
`python skill_quality.py --output report.md` producing per-skill scores and change-vs-baseline,
runnable as a periodic cron.

**No longer blocks anything.** The GEPA optimizer originally motivated this, but it uses
real recorded sessions as its trainset instead of a synthetic dataset, so this is now a
standalone "would be nice" rather than a dependency. Note the Fase 2 text also predates the
`dspy` → `gepa` correction and mentions DSPy; treat the file list, not the library choice,
as the spec.

### P2-4. Human-in-the-loop evaluator (evaluation plan R3)

Deferred from the original evaluation plan. Still no concrete trigger scenario has appeared
that justifies building it — the existing flow already requires a human to approve a
proposal before `apply_proposal()` runs, so the marginal value of an *evaluator* that asks
a human mid-gate is unclear. Leave until a scenario names it.

### P2-5. Heavier evaluation techniques

Pairwise / win-rate comparison, debate or ensemble judging, execution re-run, embedding
similarity, fine-tuned judges. Deliberately deferred and not reconsidered since. The
single-judge rubric plus the regression check has been sufficient so far.

### P2-6. MiniMax and Kimi K2 first-party branches

Still deferred, unchanged from R7's original scope note. Both are reachable **today**
through the OpenCode Zen gateway (OpenAI-compatible), so a first-party branch buys direct
billing and a shorter path, not new capability. A generic
`SKILL_EVOLUTION_OPENAI_COMPATIBLE_*` catch-all adapter was considered and rejected in the
provider-expansion plan as another adapter branch rather than a generalization — worth
revisiting only if the count of near-identical branches grows (see P3-3).

---

## P3 — Small, well-understood, low-risk

### P3-1. `include_all_statuses` has no CLI flag

`retroactive_reevaluate()` / `_select_retroactive_proposals()` accept
`include_all_statuses=True` to re-score rejected and applied proposals, but it is
Python-only — `--retroactive` always filters to `status == proposed`. The default is
correct (re-scoring dead proposals would inject entries into the same history stream the
live gate regresses against); the gap is only that the opt-in is unreachable from the
command line.

### P3-2. `proposals/` and `reports/` are tracked, empty, and unused

Both exist at the repo root and are tracked, but nothing in `scripts/` writes to either:
`proposal.get_proposals_dir()` defaults to `docs/skill-evolution/proposals/` and the
history file defaults to `docs/skill-evolution/eval_history.jsonl`. Either wire them up,
remove them, or document them as intentional placeholders — but confirm with the owner
which directory a given deployment actually writes to first.

### P3-3. Three near-identical OpenAI-compatible callers

`_call_ollama`, `_call_opencode` and `_call_openai` share the same URL construction,
auth-header shape, fail-closed missing-key check, and `choices[0].message.content` parsing,
differing only in env-var names and defaults. Raised in code review as advisory (no defect,
no violated contract) and demoted to a residual risk. The argument for extracting a
`_call_openai_compatible()` helper strengthens with each new branch — P2-6 would make it
four. Gemini stays separate regardless: its request shape, response shape and auth header
genuinely differ.

### P3-4. No retry or backoff anywhere in the provider layer

By design — a failed call raises rather than retrying. One concrete consequence worth
knowing: a `--retroactive` batch against a provider with a tight per-minute quota (Gemini's
free tier is the sharpest example) can trip a rate limit partway through, and every
subsequent judge call fails closed with `score=0.0`. Those entries are written to the
shared history **indistinguishably from a genuine quality regression**, where they can
mislead a human reviewer and feed `optimize_skill.find_low_scoring_targets()`.

Two independent mitigations if this is ever taken up: inter-call backoff, and tagging
transport-caused failures distinctly from content-caused ones in the history record. The
second is the more valuable of the two.

### P3-5. Remaining test gaps

None of these is a correctness defect; each is coverage that would catch a future
regression.

- The generic cross-provider timeout test iterates `("claude", "ollama", "opencode")` —
  `openai` and `gemini` were never added to that tuple.
- Neither new provider test file asserts on the `timeout` value it captures from
  `_post_json` (captured, never read).
- No test exercises a real `HTTPError` (401/403) through `_post_json` for any provider.
- No end-to-end test runs `LLMJudgeEvaluator.evaluate()` with `provider=openai|gemini`;
  coverage stops at the adapter function. This matches the pre-existing convention for
  `opencode`/`ollama`, so it is a consistent gap rather than a new one.
- No test asserts that `SKILL.md`'s documented provider list matches
  `sorted(PROVIDER_CALLERS)` — which is exactly the drift a review pass had to catch by
  hand on 2026-07-28.

### P3-6. Two optimizer tunables are still untuned

`MIN_SESSIONS` (3) and `DEFAULT_MAX_SESSIONS_FOR_SKILL` (20) have no evidence pointing
anywhere: none of the three real GEPA runs tested `MIN_SESSIONS`'s boundary, and both 8-
and 20-session trainsets ran fine. `DEFAULT_MAX_METRIC_CALLS` was the one that moved
(20 → 8) on real-run cost data. Moving the other two now would be tuning on taste.

---

## P4 — Accepted by design (recorded so they are not rediscovered)

Carried forward from `pending-work-2026-07-26.md`, plus one addition from the 2026-07-28
review:

- **Paraphrased injection via GEPA's reflection prompt.** Narrowed by the anti-verbatim
  instruction plus untrusted-content framing, but `gepa`'s internal prompt construction is
  out of reach. Residual, accepted.
- **Noisy trainset (KD2).** Real recorded sessions are uncurated by choice.
- **Growth/shrink checks are best-effort in the optimizer.** Silently skipped when the
  skill cannot be resolved, rather than blocking the draft.
- **Oversized skills are improvable, not downsizable.** The ratchet lets a >15KB skill be
  replaced by a body no larger than itself, but at 2048B/pass a 103KB skill would need ~44
  approved passes to reach compliance — it still needs a human to split it.
- **Base URLs are not scheme-validated or allowlisted.** `SKILL_EVOLUTION_*_BASE_URL` is
  operator-set, not attacker-controlled, so this is configuration error rather than an SSRF
  surface. Since 2026-07-28 a malformed value fails closed as `ProviderError` instead of a
  raw `ValueError`, which is the part that actually mattered.
- **`create_new` keeps the strict absolute cap.** A new skill is never born oversized; the
  ratchet exemption applies only to text that replaces existing text.

---

## Recommended order

0. ~~**All five P0s**~~ — **done 2026-07-29.** Suite 507 → 533.
1. **P1-1** (git remote) — mostly done: `origin` points at `Carlo1911/skill-evolution` and
   `main` is already pushed (`origin/main` == `HEAD`). Only making the repo **public** remains.
2. **P1-2** (live-verify the two new providers) — now the top open item, and cheap: it is the
   only thing standing between "shipped" and "proven" for work already merged. P0-5 is the
   argument for doing it — a shape nobody exercised was wrong in production for two days.
3. Then **P2-2** (host-agnostic), which is the highest stated owner interest. Start with the
   read side; the write side needs a per-host apply strategy and is the expensive half.
4. **P2-1** (gate the new targets) whenever the owner wants to decide it — the code is
   already in place, so this is a policy change, not a build.

P2-3, P2-4, P2-5 and P2-6 have no trigger yet; leaving them is the right call. The P3 items
are opportunistic — fold them into whatever pass next touches the same file.
