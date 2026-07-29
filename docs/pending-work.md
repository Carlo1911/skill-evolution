# Pending Work

> **The live backlog is [`pending-work-2026-07-28.md`](pending-work-2026-07-28.md)** — read
> that one for what is still open. [`pending-work-2026-07-26.md`](pending-work-2026-07-26.md)
> is the closed 2026-07-26 consolidation: it gathered every deferred item across the repo,
> re-verified each against the code, and prioritized them; its P0–P2 sections are all
> resolved, so keep it for the reasoning behind those decisions rather than for status. It
> also records three findings this file predates: that production ran an older copy of the
> pipeline from `ai-memory-stack` (since fixed), that the plan's PII risk never got its
> stated sign-off (since recorded), and that malformed numeric env vars crashed the gate
> (since guarded).
>
> This file remains the **evidence record** — the Fase 0 validation narrative, the measured
> numbers, the retracted conclusion, and the bug post-mortems. The backlogs reference it
> rather than restating it, so the three do not drift.

Status snapshot as of 2026-07-26, after the real GEPA optimizer (see
`docs/plans/2026-07-24-001-feat-real-gepa-optimizer-plan.md`), the
follow-up hardening pass (see
`docs/plans/2026-07-25-001-fix-skill-evolution-hardening-plan.md`), and the
provider-free **Fase 0 validation pass** (see the section near the bottom —
read it before trusting any "shipped" claim here). This is a decision
inventory, not a committed roadmap — pick an item, then run it through
`ce-brainstorm`/`ce-plan` when you're ready to scope it.

## Not yet built (from the original gap analysis)

Seven areas were identified against `docs/plans/2026-07-23-001-feat-skill-evolution-evaluation-plan.md`
and `docs/integration-plan.md`. The GEPA optimizer was one; items 1–6 are the
rest. Item 7 is newer and did not come from that analysis. **Items 1 and 2 have
since shipped in full** (2026-07-27 and 2026-07-28), and item 5's cold-start
blocker is closed — so read each item's own status rather than assuming the whole
list is untouched. The current live inventory is
[`pending-work-2026-07-28.md`](pending-work-2026-07-28.md).

1. ~~**Evaluation coverage expansion.**~~ **DONE 2026-07-27.** All three fast-follow
   targets shipped as siblings to `evaluate_skill_text()`: `evaluate_proposal()`,
   `evaluate_tool_calls()`, `evaluate_analyzer_prompt()`. They write to history
   but deliberately do not gate auto-apply.
2. ~~**Provider coverage expansion.**~~ **DONE 2026-07-28** for the first-party gap.
   Five adapters now: `claude`, `ollama`, `opencode`, `openai`, `gemini`. OpenCode
   Zen remains the OpenAI-compatible gateway reaching MiniMax, Kimi, GLM, DeepSeek
   and Qwen by proxy, so first-party MiniMax/Kimi K2 branches stay deferred. See the
   OpenCode notes at the bottom for the Zen-vs-Go catalogue trap.
3. **Human-in-the-loop evaluator.** Deferred from the evaluation plan's R3;
   no concrete trigger scenario has justified building it yet.
4. **Skill quality tracking (`docs/integration-plan.md` Fase 2).**
   `dataset_builder.py` (synthetic eval datasets) and `skill_quality.py`
   (periodic quality tracker/report) don't exist. The GEPA optimizer
   deliberately does **not** depend on this — it uses real recorded sessions
   as its trainset instead — so this is no longer a blocker for anything,
   just still unbuilt.
5. **Production cron migration.** `~/.hermes/cron/jobs.json` still points at
   `ai-memory-stack`, not this repo. The original plan gates this on the
   shared-core evaluators completing clean retroactive runs against real
   historical sessions — a condition unrelated to GEPA shipping.
   **The gating condition as worded was not directly executable**: `--retroactive`
   re-evaluates *saved proposals*, not sessions — `_select_retroactive_proposals()`
   loads proposals from disk and filters `status == proposed`. With no proposals on
   disk it is a clean no-op ("Nothing to re-evaluate"), and `--list-candidates` is
   likewise a no-op with no `eval_history.jsonl`. Read the gate as "a clean
   retroactive run over real *proposals*".

   **That cold start is now closed** — see "The analyzer half" below: a real
   analysis pass produced a 5-proposal corpus from real sessions, all 5 scored
   through the three-evaluator gate and re-evaluated retroactively. What remains
   for this item is operational, not blocked: pointing
   `~/.hermes/cron/jobs.json` at this repo. Both policy questions that were open
   here have since been decided — the shrink floor is 15% per pass with a 30%
   cumulative ceiling, and the optimizer's objective now carries the byte budget.
6. **Heavier evaluation techniques.** Pairwise/win-rate comparison,
   debate/ensemble judging, execution-based re-run verification,
   embedding-similarity checks, fine-tuned local judges — catalogued and
   deliberately deferred in the evaluation plan's Scope Boundaries. Not
   reconsidered since.
7. **Host-agnostic / multi-agent support.** *(New — not from the original gap
   analysis.)* Let the pipeline evolve skills for agents other than Hermes:
   Codex, Claude Code, Claude, and future providers. **Guiding principle,
   stated by the project owner: the more agnostic the design, the better** —
   treat host-neutrality as the default for new work rather than something
   retrofitted later, and prefer an adapter seam over an inline assumption
   whenever a host-specific detail would otherwise be hardcoded.
   The concrete surface is already mapped: exactly four hardcoded coupling
   points, each isolated in its own module (session source, skill source,
   processed state, mutation tool) — see "Multi-agent support: four seams"
   below for the table and the read-side/write-side asymmetry. Everything else
   (evaluator registry, gate, size guards, history, provider layer, analyzer
   prompt) is already host-agnostic because it operates on skill *text* and
   proposal *shape*, not on provenance. Scope the read side first: it
   generalizes far more cheaply than the write side, which needs a per-host
   apply strategy since no host outside Hermes exposes `skill_manage`.

## Discovered while building GEPA, fixed in the hardening pass

Found during implementation and code review of the GEPA optimizer; explicitly
left out of that work's scope, then addressed by
`docs/plans/2026-07-25-001-fix-skill-evolution-hardening-plan.md`.

- **`fetch_sessions()` queried the wrong columns.** *(Fixed.)*
  `scripts/fetch_sessions.py`'s `SESSION_QUERY`/`MESSAGE_QUERY` now select
  `sessions.started_at`/`messages.timestamp`, matching the live
  `~/.hermes/state.db` schema and `sessions_for_skill()`'s existing correct
  queries. The output dict's `created_at` key was renamed to `started_at`
  and `total_tokens` was dropped (non-breaking — `analyze.py` already
  defaults it).
- **GEPA's reflection-mutation prompt had no injection-defense framing.**
  *(Narrowed, not eliminated — tracked as an accepted residual risk.)* The
  judge's `feedback` field (embedded verbatim into `gepa`'s own internal
  reflection prompt) is now (a) produced under an explicit instruction not to
  quote session content verbatim, and (b) wrapped with the same
  untrusted-content framing used elsewhere in this codebase before being
  returned to `gepa`. `gepa`'s own internal prompt construction remains out
  of reach — wrapping it directly risks breaking `gepa`'s reflective-mutation
  mechanism (KD2) — so a paraphrased (non-verbatim) injection is still a live
  residual risk, just a narrower one than before.
- **Optimizer defaults were unvalidated against real usage.** *(Made
  configurable in the hardening pass; `DEFAULT_MAX_METRIC_CALLS` tuned
  2026-07-26 against the three Fase 0 runs below — RESOLVED for that
  constant, still open for the other two.)* `MIN_SESSIONS`,
  `DEFAULT_MAX_METRIC_CALLS`, and `DEFAULT_MAX_SESSIONS_FOR_SKILL` are
  overridable via `SKILL_EVOLUTION_OPTIMIZER_MIN_SESSIONS`,
  `SKILL_EVOLUTION_OPTIMIZER_MAX_METRIC_CALLS`, and
  `SKILL_EVOLUTION_OPTIMIZER_MAX_SESSIONS_FOR_SKILL`. Of the three, only
  `DEFAULT_MAX_METRIC_CALLS` had load-bearing evidence behind it — see
  "Size limits vs. the optimizer: three runs, not one" below: the
  `money-admin-messaging` run (`--iterations 4`) took 2+ minutes wall-clock
  against `big-pickle` (30s+/metric-call) and already found both a real
  score gain and the pathological growth failure inside those 4 calls, so
  the old default of 20 meant a 10+ minute unattended foreground call by
  default for a manual, non-cron operation. Moved 20 → 8 (2x the
  empirically-sufficient count). `MIN_SESSIONS` (3) and
  `DEFAULT_MAX_SESSIONS_FOR_SKILL` (20) are unchanged: none of the three
  runs tested `MIN_SESSIONS`'s boundary, and both 8- and 20-session
  trainsets ran fine, so neither has evidence pointing anywhere in
  particular. Still real judgment calls, just no longer *uninformed* ones
  for the constant that had a concrete cost signal.

## Fase 0 validation pass (2026-07-26): what real data proved

A provider-free validation pass against the real `~/.hermes/state.db`
(450MB, 426 sessions, 26,826 messages) and the real installed skills tree.
Environment: a project-local `.venv` on pyenv 3.12.2 with
`pip install -e ".[dev,optimizer]"` (`gepa==0.1.4` installed).

**Verified working against real data:**

- `sessions.started_at` / `messages.timestamp` match the live schema — the
  hardening pass's column fix is correct, not just internally consistent.
- `fetch_sessions.py --dry-run --lookback-hours 168` exits 0 and emits 20
  sessions; output keys are exactly `started_at` (no `created_at`, no
  `total_tokens`), as documented.
- `skill_index.py` parses 140 real `SKILL.md` files across 17 categories with
  zero empty descriptions — the hand-rolled frontmatter parser holds up,
  including folded (`>`) scalars and non-English bodies.
- `proposal.py` round-trips a real multi-line skill body through
  `save_proposal()` / `load_proposal()` with `proposed_changes` intact.
- The deterministic gate discriminates: a legitimate +4% edit passes; oversized
  (>15KB), missing-frontmatter, and runaway-growth candidates are all rejected.
- Cold-start paths degrade cleanly (exit 0, clear message) rather than crashing.

**Three real bugs found and fixed** (each with a regression test; suite went
166 → 177):

1. **`optimize_skill.py` could never have run.** `_require_gepa()` returned the
   top-level `gepa` package, but all four symbols the call site uses live in the
   `gepa.optimize_anything` submodule — so `gepa.optimize_anything(...)` raised
   `TypeError: 'module' object is not callable` and `gepa.GEPAConfig` /
   `EngineConfig` / `ReflectionConfig` raised `AttributeError`. Invisible to the
   suite because every test stubs `_require_gepa` with a fake matching the
   code's own assumption. Fixed; the KTD4 worry about `reflection_lm`/config
   *parameter names* turned out to be unfounded — those are all correct for
   `gepa==0.1.4`. The real drift was the module/function shadowing.
2. **`skill_index.py` indexed dot-directories.** `.archive/`'s retired skills
   were reported as currently existing, and the archived twin of the live
   `general-skills/money-admin-messaging` made `--skill money-admin-messaging`
   fail as an ambiguous duplicate. Now skips dot-prefixed categories.
3. **The growth-vs-baseline guard was dead code on the live path.**
   `evaluate_skill_text()` never populated `context["baseline_size"]`, and the
   check is `if baseline_size:`-gated — so `SKILL_EVOLUTION_MAX_GROWTH_PCT`
   never applied through `apply_proposal()` or `--retroactive`. Demonstrated
   with a +343% body that reported "all deterministic checks passed"; now
   correctly rejected as "growth 339.3% exceeds 20% limit over baseline".

**The LLM half, verified against a real provider.** Run through the new
`opencode` provider on OpenCode Zen's free `big-pickle`:

- `big-pickle`, `deepseek-v4-flash-free` and `mimo-v2.5-free` all answer, and
  `big-pickle` returns clean unfenced JSON — `LLMJudgeEvaluator`'s hard
  requirement.
- `LLMJudgeEvaluator` scored a real installed `SKILL.md` at **0.87 (passed)** —
  its first execution against a real provider response.
- Full gate with the judge in the loop: deterministic 1.00 + llm_judge 0.87 →
  mean 0.93, `strictness=strict`, **passed**.
- `RegressionEvaluator` against a real multi-entry history file: no-baseline
  passes; 0.92-vs-0.90 passes; 0.60-vs-0.90 is rejected; and after a *failed*
  0.20 entry a 0.60 attempt is still compared against the last **passing** 0.90
  and rejected — the documented "a failed attempt can never lower the bar"
  behavior, confirmed rather than assumed.

**A fourth bug, found only by a real HTTP call.** `_post_json()` sent urllib's
default `User-Agent` (`Python-urllib/3.12`), which OpenCode Zen's gateway
blanket-blocks: **403 for it, 200 for the byte-identical request** under any
descriptive UA. Every provider shared that code path, so this would have hit
Claude/Ollama deployments behind similar gateways too. Fixed by sending
`USER_AGENT = "hermes-skill-evolution/0.1"` (caller-overridable). Diagnostic
worth remembering: when a provider 403s but `curl` succeeds, suspect the UA
before the key.

**Follow-up, 2026-07-26: verified against a real local Ollama server.** Its endpoint turned
out not to gate on User-Agent at all (both the old and new UA returned 200), so the
"Claude/Ollama deployments behind similar gateways too" worry above did not materialize for
Ollama specifically — the fix is confirmed harmless there, not confirmed necessary. The pass
went further than `curl`: `LLMJudgeEvaluator.evaluate()` → `_call_ollama()` → `_post_json()`
scored a real installed skill (`systematic-debugging`, 15,325B) via `gemma4` at 0.98, and the
full `deterministic` + `llm_judge` gate ran end to end and correctly reported FAILED
(deterministic legitimately rejected the candidate on the absolute cap; see the ratchet
section above). Unrelated finding surfaced by the same pass: the judge call took 54–56s of
the 60s default timeout with `gemma4`, and `qwen3.5` timed out outright on the same
prompt — local reasoning models spend a large share of their output on chain-of-thought
before the JSON, which a hosted API typically doesn't do. Claude remains unverified; no
`ANTHROPIC_API_KEY` was available in this environment.

**The GEPA loop ran for real, end to end.**
`--skill money-admin-messaging --iterations 4` on `big-pickle`, exit 0. Every
component worked: `_reflection_lm_adapter()` conforms to gepa's LanguageModel
protocol and produced complete, well-formed `SKILL.md` candidates (valid
frontmatter, `references:`, action tables — not fragments); `score_candidate()`
discriminated (a weak candidate scored 0.3 and gepa's own selection discarded
it); and the search genuinely improved the skill, **0.55 → 0.633** on the
valset. The loop is slow — 4 metric calls exceeded two minutes — so run it
detached, not inline. Session availability is not a blocker: 12 installed skills
have ≥3 sessions (`hermes-agent` 20, `money-admin-messaging` 8, `llm-wiki` 8, …).

### Size limits vs. the optimizer: three runs, not one

An earlier draft of this document concluded from the single
`money-admin-messaging` run that the optimizer was "near-unproductive". **That
was an overgeneralization from n=1 and is retracted.** Two further runs both
drafted proposals successfully:

| skill | installed | seed → best | outcome |
|---|---|---|---|
| `money-admin-messaging` | 1861B | 0.55 → 0.633 | rejected: growth **+121.8%** |
| `hermes-agent` | 11884B | 0.80 → 0.80 | proposal drafted (+0.7%, no score gain) |
| `test-driven-development` | 10258B | 0.85 → **0.90** | proposal drafted (**−70.4%**) |

The growth rejection was specific to a 1861B skill, where the 20% cap allows
only +368B — almost any added detail breaches it. Skills with real headroom
optimize fine. So the objective/constraint mismatch is real but narrow: worth
addressing (passing the byte budget into `run_gepa_optimization()`'s `objective`
string is the cheapest fix, so the search optimizes inside the budget it is
judged against), not a blocker.

### The actual danger found by those runs: unbounded shrink

`test-driven-development` is the serious result. GEPA raised the score by
**deleting 70% of the body** (10258B → 3041B), and the judge rated the gutted
version *higher* (0.85 → 0.90). 17 of 29 headings were lost, including
`## Red Flags — STOP and Start Over`, `## Common Rationalizations`,
`## Testing Anti-Patterns`, `## Avoid Horizontal Slices`, and the
`Verify RED`/`Verify GREEN` steps with their commands — the guardrail sections.

The gate had a growth cap and **no floor**, so this passed structural validation
cleanly. The judge's `conciseness` criterion actively rewards it, meaning the
optimizer will find this attractor repeatedly. The only thing standing between
it and a silently gutted skill was that optimizer proposals carry
`confidence=0.5`, below `apply_proposal()`'s 0.85 default — safety by accident,
not by design.

Fixed: `DeterministicEvaluator` now enforces a shrink floor
(`SKILL_EVOLUTION_MAX_SHRINK_PCT`, default 20%, symmetric with the growth cap),
and `draft_proposal_from_gepa_result()` now populates the change's `old_value`
with the installed body so both size guards stay live at the gate — previously
`old_value` was empty on optimizer proposals, leaving those checks inert
everywhere except draft time.

**Both follow-ups are now implemented.**

*Decision on the floor: **15%** per pass* (`SKILL_EVOLUTION_MAX_SHRINK_PCT`,
configurable as it always was — only the default moved). Measured against the
real candidates, 10/20/30% were indistinguishable: the destructive one was −70.4%
(rejected at any threshold) and the good one +0.7% (accepted at any). The
threshold was never the weak point, so the choice came down to blast radius per
pass — 15% caps one bite at ~1.4KB of a median 9.7KB skill instead of ~1.9KB.
It is intentionally tighter than the 20% growth cap: an over-long skill is
bounded by the absolute 15KB check and merely costs context, while deletion
silently removes guidance.

Worth knowing: the **cumulative** limit, not the per-pass one, governs where
erosion stops. At a 15% floor the cumulative 30% ceiling is reached at pass 3
(62% retained); at 20% it was pass 2 (64% retained). Retuning the per-pass floor
changes how many passes it takes and how big each bite is — not the floor of the
whole process.

*The real weakness was that the floor is per-proposal, so it compounds.* Each
pass resets its reference, and `RegressionEvaluator` cannot intervene because
every deletion *raises* the judge's `conciseness` score — each pass legitimately
outscores the last, so the gate keeps passing. Erosion is self-reinforcing.
Passes needed to destroy half a skill: 7 at a 10% floor, 4 at 20%, **2 at 30%**
— which is why widening the floor was the wrong lever.

Fixed with a second reference point: `SKILL_EVOLUTION_MAX_CUMULATIVE_SHRINK_PCT`
(default 30%) and `SKILL_EVOLUTION_MAX_CUMULATIVE_GROWTH_PCT` (default 50%),
measured against `original_size_for_target()` — the earliest size recorded for
that target in `eval_history.jsonl`. History entries now carry `content_size`
and `baseline_size` to make that lookup possible; entries predating those fields
yield `None` and leave the check inert. Verified by simulating repeated
exact-20% cuts: erosion halts at pass 2 with 65% of the original retained
instead of running to zero.

*The objective now carries the byte budget.* `_build_objective()` states the
seed's byte count, the allowed range from the same env limits the gate enforces,
and an instruction to preserve section headings. Soft defence only — a prompt the
model may ignore — so it complements rather than replaces the hard check; the
point is not spending a metric-call budget on candidates that are inadmissible
from the first byte.

*Percentage limits remained weakest where damage is largest — now fixed.* The
numbers in the original note were stale in two ways: the per-pass floor settled
at 15%, not 20%, and a proper census of the tree (143 skills, median **9,954B**,
max **103,656B** `research-paper-writing`) shows one pass could delete
**15,548B** from the largest skill while reporting a compliant −15.0%.
`SKILL_EVOLUTION_MAX_SHRINK_BYTES` (default **2048**, `0` disables) now bounds
that, as a second sequential check after the percentage so the feedback names
whichever limit actually bound. The default is the crossover rather than a
preference: `2048/0.15 = 13,653B`, just under the 15,360B cap, so the byte floor
is inert for essentially every within-cap skill by construction and operative
precisely on the oversized tail. It binds 35 of 143.

*The absolute cap became a ratchet in the same change.* A flat ceiling rejected
**22 of 143** skills' body proposals regardless of direction — a proposal
shrinking one toward compliance drew the byte-identical message as one growing
it, so the gate could not tell improvement from worsening. It now fails only when
the candidate is over the cap *and* larger than the baseline it replaces;
`create_new` has no baseline and keeps the strict cap, so a skill is never born
oversized. **The two are causally coupled:** that 15,548B deletion was previously
masked (the shrunk candidate was still over the cap and failed there first), so
the ratchet alone would have been a net regression in deletion safety. They
shipped together, byte floor first.

Growth needs no byte counterpart — per-pass absolute growth is bounded at
`cap × g/(1+g)` = 2,560B within the cap, and at 0 for an oversized skill under the
ratchet. Note that invariant *scales with the cap*, so revisit if
`SKILL_EVOLUTION_MAX_SKILL_SIZE_KB` ever moves.

Honest limitation: this makes oversized skills **improvable, not downsizable**.
At 2048B/pass, walking `research-paper-writing` from 103,656B to compliance would
take ~44 approved passes. A skill that large still needs a human to split it; the
env var is the escape hatch for a deliberate consolidation run.

```bash
pip install -e ".[dev,optimizer]"
SKILL_EVOLUTION_OPTIMIZER_ENABLED=true python3 scripts/optimize_skill.py --list-candidates
SKILL_EVOLUTION_OPTIMIZER_ENABLED=true python3 scripts/optimize_skill.py --skill <name>
```

### The analyzer half, run end to end (closes the cold start)

`docs/session-analyzer-prompt.md` is what CLAUDE.md calls the business logic of
the system, and it had never been executed. It has now, against real data:

```
fetch_sessions() -> analyze.format_session() -> analyzer prompt + skill index
  -> OpenCode Zen (big-pickle) -> parse -> save_proposal() -> three-evaluator gate
```

8 real non-cron sessions → ~113KB of formatted evidence → a ~134KB assembled
prompt (analyzer instructions + a 142-skill index + session text) → a valid JSON
array of **5 proposals**, each grounded in session evidence:

| type | target | conf | judge | gate |
|---|---|---|---|---|
| `create_new` | video/Threads download skill | 0.85 | 0.92 | **pass** |
| `improve_existing` | `hermes-execution-contexts` (cron timeouts) | 0.75 | 0.87 | **pass** |
| `create_new` | interview-feedback analysis | 0.80 | 0.87 | **pass** |
| `improve_existing` | `hermes-provider-diagnostics` | 0.70 | 0.67 | **fail** |
| `create_new` | RTK install/config skill | 0.65 | 0.80 | **pass** |

The important result is the fourth row: the gate **rejected** one of the
analyzer's own proposals on the judge threshold (0.67 < 0.7 default) while
passing the other four. It discriminates on real analyzer output rather than
rubber-stamping it. All 5 round-trip cleanly through the hand-rolled frontmatter
parser, all 5 were picked up by `retroactive_reevaluate()`, and **0 unredacted
secret lines** reached disk.

Two design notes surfaced by the run:

- **Prompt size is a real constraint.** 8 sessions already assemble a ~134KB
  prompt. How many sessions one analysis pass can carry is bounded by the
  provider's context window, so `--lookback-hours`/`max_sessions` are capacity
  knobs, not just scope knobs.
- **`create_new` proposals can never regress.** With `target_skill` empty,
  `target_key_for_proposal()` falls back to `proposal:<uuid>`, so every
  `create_new` gets its own single-entry history lineage and
  `RegressionEvaluator` always reports "no baseline yet". Defensible (a new skill
  has no baseline) but it means regression coverage is effectively
  `improve_existing`-only.

### Two production-blocking bugs in the state file and the redactor

Found by the follow-up pass that closed the gaps the first Fase 0 sweep left.

**Secret redaction leaked real credential shapes.** `contains_secret()` matched
only fixed substrings (known vendor prefixes and specific env-var names).
Scanning the real 26,794 message bodies found shapes it missed entirely: 27
`password=`/`passwd:` assignments, 23 generic `*_TOKEN=`/`*_SECRET=`/`*_KEY=`
assignments, 6 generic `sk-` keys, 5 `Authorization: Bearer` headers, and a
credentialed database URI. Every one would have been forwarded verbatim — a gap
that got materially worse the moment the `opencode` third-party provider landed.
Fixed by adding shape-based `SECRET_REGEXES` alongside the substring list; a
re-scan now misses nothing, and over-redaction is 0.229% of lines (741 of
323,208), which is the right side of that trade.

**The state file schema never matched reality, so dedup was dead.** The
deployed `~/.hermes/skill_evolution_state.json` is a flat
`{session_id: iso_timestamp}` map with 101 entries. Both `state.py` and
`fetch_sessions.py` read `data.get("processed_sessions", [])` — the shape the
deployed `SKILL.md` also documents — so `load_processed()` returned `[]` against
the real file **every run**, and every session looked unprocessed. Worse,
`mark_processed()` would have rewritten the file in the documented shape,
destroying 101 entries another producer maintains. Fixed: both copies now read
either shape and preserve whichever layout the file already uses, writing the
documented shape only for a fresh file. Against the real file `load_processed()`
went 0 → 101 ids, and `--dry-run --lookback-hours 168` went from "20
unprocessed" to "0 unprocessed" — i.e. the repo's code would have re-analyzed 20
already-processed sessions.

Note the file itself was **not** modified; the schema disagreement between this
repo and whatever writes that file is surfaced here rather than migrated
unilaterally.

### Environment note: zsh `noclobber`

This machine's zsh has `noclobber` set, which makes `>>` **fail on a
non-existent file** unless `APPEND_CREATE` is also set. Shell snippets that
build up a log with `>>` silently produce nothing; redirect the whole block once
with `>` instead. Cost one confusing empty-log debugging cycle.

### Multi-agent support (Codex, Claude Code, …): four seams

The GEPA objective string is now deliberately **agent-neutral** ("this agent
skill's instructions", not "this Hermes Agent skill's"), both so the wording
survives generalization and so the reflection LM isn't biased toward one host's
conventions. `grep -c "Hermes Agent" scripts/*.py` is now zero.

That is only the wording. The host coupling is real, but it is smaller than
expected — **four hardcoded points, each already isolated in its own module**:

| seam | where | what it assumes |
|---|---|---|
| session source | `fetch_sessions.py:23` | `~/.hermes/state.db`, SQLite with `sessions`/`messages` tables and that column layout |
| skill source | `skill_index.py:2` | `~/.hermes/skills/<category>/<skill>/SKILL.md` |
| processed state | `state.py:12` | `~/.hermes/skill_evolution_state.json` |
| mutation tool | `proposal.py:298` | emits `skill_manage` instruction dicts |

Everything else — the evaluator registry, the gate, the size guards, the history
and its cumulative baseline, the provider adapter layer, the analyzer prompt — is
already host-agnostic: it operates on skill *text* and proposal *shape*, not on
where either came from.

So generalizing is an adapter job at four seams, not a rewrite. What differs per
host is substantial though, and worth scoping before committing: Claude Code
keeps transcripts as per-project JSONL rather than one SQLite DB, skill layouts
differ (`~/.claude/skills/`), and no host outside Hermes exposes a `skill_manage`
tool — so the "apply" step needs a per-host strategy (direct file write plus
whatever review/versioning that host expects) rather than one instruction dict.
The read side (analyze + evaluate + propose) generalizes far more cheaply than
the write side.

### Provider: OpenCode Zen (implemented)

Added to `evaluate.py`'s adapter layer as the `opencode` provider during this
pass, covering much of item 2 above. OpenAI-compatible (`/chat/completions`,
bearer auth), so one branch reaches the MiniMax / Kimi / GLM / DeepSeek / Qwen
families item 2 names.

**The catalogue trap** (cost an incorrect conclusion mid-pass, recorded so it
isn't repeated): there are two non-interchangeable model lists.

| | Zen | Go |
|---|---|---|
| base URL | `https://opencode.ai/zen/v1` | `https://opencode.ai/zen/go/v1` |
| `big-pickle` | **yes** (free, "stealth model … free for limited time") | no |
| free tier | `deepseek-v4-flash-free`, `mimo-v2.5-free`, `ling-3.0-flash-free`, `nemotron-3-ultra-free`, … | — |
| DeepSeek id | `deepseek-v4-flash-free` (note the suffix) | `deepseek-v4-flash` |
| size | 50+ models incl. GPT/Claude/Gemini | 23 models |

Checking only Go's list produced the wrong claim that `big-pickle` does not
exist. Both lists are public — `curl -s https://opencode.ai/zen/v1/models`
needs no key. Defaults in code: Zen base URL + `big-pickle`.
