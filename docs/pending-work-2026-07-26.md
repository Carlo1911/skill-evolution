# Pending Work — consolidated backlog as of 2026-07-26

Built by reading every document in the repo (`CLAUDE.md`, `README.md`, `SKILL.md`,
`docs/integration-plan.md`, `docs/session-analyzer-prompt.md`, all three plans under
`docs/plans/`, and `docs/pending-work.md`) and cross-checking each deferred item against
the code as it stands after today's commits (`a5e754d`..`5bd781f`).

**Relationship to `docs/pending-work.md`:** that file stays as the *evidence record* — the
Fase 0 validation narrative, the measured numbers, the retracted conclusion, the bug
post-mortems. This file is the *actionable backlog*. It references that evidence rather
than restating it, so the two do not drift. (Three divergent copies of
`fetch_sessions.py` are what caused P0-1 below; duplicating prose is the same failure mode
in a cheaper form.)

Test baseline: **511 passed** with `gepa` installed, **507 passed + 1 skipped** without
(was 253/249 when this document was first written, 351/347 after the P0/P1 items,
443/439 before the provider-timeout knob added its 7 on 2026-07-27, and 450/446+1 before
the first-party OpenAI/Gemini provider expansion and its review-fix pass added 61 more on
2026-07-28).

---

## P0 — Production and security — all three RESOLVED 2026-07-26

These were the only urgent items in this document, and none remain open. Each is kept with
its original finding collapsed underneath, because the *reason* each was urgent is the part
worth not forgetting.

### ~~P0-1. Production runs a different, older copy of the pipeline~~ — RESOLVED 2026-07-26

**Owner's decision: this repo is the source of truth.** Resolved as follows (see
`CLAUDE.md` → "Production deployment" for the durable description):

- `~/.hermes/scripts/skill-evolution-fetch.sh` is now a **symlink** into this repo instead
  of a hardcoded 2-line wrapper that `cd`'d to `ai-memory-stack`. A symlink rather than a
  copy, so a fourth divergent copy cannot accumulate. Old file backed up as
  `…-fetch.sh.bak-2026-07-26`.
- The repo's own wrapper had to be fixed first — it had **never worked**: it probed two
  paths that do not exist here, and it leaked the repo path to stdout via
  `if git rev-parse --show-toplevel`, which would have corrupted the NDJSON stream. It now
  resolves `fetch_sessions.py` relative to its own location, following symlinks, and passes
  arguments through.
- The job prompt now lives versioned at `docs/cron-job-prompt.md` and was pushed with
  `hermes cron edit ba29abd33988 --prompt "$(cat …)"` — the CLI, not a hand-edit of
  `jobs.json`, since the daemon owns fields in that file. Backup of the pre-change
  `jobs.json` was taken.
- The analyzer prompt was reconciled. The deployed copy was not merely *behind* the repo's:
  it contained a `Phase 2: Auto-Apply` section instructing in-session `skill_manage` calls,
  which the repo's Rule 2 explicitly forbids. Owner chose proposals-only, so **auto-apply
  is now off** (it was live before, applying anything at `confidence >= 0.85`), and the
  repo's prompt gained a "Making a proposal reviewable" spec plus a generalized Reporting
  section carrying the deployed copy's delivery substance without its personal config.

Verified: the live execution chain contains no `ai-memory-stack` reference in either the
wrapper or any job field; the wrapper emits clean NDJSON through the symlink from an
unrelated cwd; schedule, `enabled`, and the daemon's own timestamp fields were preserved.

**Follow-up done:** `~/Projects/ai-memory-stack/docs/skill-evolution/DEPRECATED.md` now
records that nothing there executes, which copy was stale in what way, and what still needs
attention. The stale copies themselves were left in place — a different project, and
deleting them is the owner's call. The file is uncommitted in that repo (it already had 8
uncommitted changes; committing on its behalf is not this work's place).

**Three things that surfaced while writing it:**

1. **Five unreviewed proposals were about to be orphaned.** Production had been writing to
   `ai-memory-stack/docs/skill-evolution/proposals/` and 8 real proposals accumulated there;
   new ones now go to this repo, so nobody would have looked at the old ones again. Five
   still carry `status: proposed` and await a decision — `gh-cli-in-subagents` (0.85),
   `hermes-agent` (0.80), `money-admin-operations` (0.90), `ci-cd-and-automation` (0.75),
   `local-ai-model-audit` (0.80). Listed in DEPRECATED.md. **Decide whether to migrate them
   into this repo's proposals directory or close them out.**
2. **Auto-apply was demonstrably firing.** Two proposals are `status: applied`, the most
   recent from the night of 2026-07-25 at confidence 0.88. So turning auto-apply off was not
   a theoretical change — the nightly run had been mutating installed skills without review.
3. **An invalid status silently disappears from inventory.** One file carries
   `status: already_covered`, which is not in `ProposalStatus`
   (`proposed | approved | rejected | applied`). `load_proposal()` raises `ValueError` on it,
   and `list_proposals()` **swallows that and returns 7 of 8 files with no warning**. A
   model-authored status value can therefore make a proposal vanish from `--list` and from
   `--retroactive` without any signal. Worth a fix: either accept unknown statuses as
   opaque, or surface the skipped file rather than dropping it.

<details>
<summary>Original finding (kept for context)</summary>

#### P0-1. Production runs a different, older copy of the pipeline

The live cron job `skill-evolution-analysis` (daily 02:00, `enabled: true`,
`last_status: ok`, last ran 2026-07-26T02:02) already points at *this skill*
(`skill: hermes-skill-evolution`, `script: skill-evolution-fetch.sh`) with
`provider: opencode`, `model: big-pickle`. But the deployed wrapper
(`~/.hermes/scripts/skill-evolution-fetch.sh`) execs
`scripts/skill-evolution/fetch_sessions.py`, which resolves to a copy in
**ai-memory-stack**, not this repo. Three divergent copies exist:

| copy | state schema | regex secrets | columns |
|---|---|---|---|
| `ai-memory-stack/scripts/skill-evolution/` ← **what runs** | flat dict | **no** | `started_at` ✅ |
| `ai-memory-stack/skills/hermes-skill-evolution/scripts/` | documented | no | `created_at` ❌ stale |
| this repo | reads both | **yes** | `started_at` ✅ |

Consequences, in order of severity:

1. **The regex secret redaction added today is not live.** The copy in production uses
   substring matching only. Scanning the real DB found 62 message bodies carrying shapes
   it misses (27 `password=`, 23 generic `*_TOKEN=`, 6 generic `sk-`, 5
   `Authorization: Bearer`, 1 credentialed URI). When those fall inside the cron's
   lookback window they reach OpenCode Zen verbatim, nightly.
2. Neither the shrink floor, the cumulative limits, the state-schema compatibility, nor
   any of today's four bug fixes are in effect in production.
3. The job's `prompt` reads `session-analyzer-prompt.md` from the ai-memory-stack path —
   a fourth divergent artifact.
4. This is also the root cause of the state-schema mismatch documented in
   `pending-work.md`: the production copy is the canonical writer of the flat-dict
   `~/.hermes/skill_evolution_state.json`. Not an unknown producer.

**Decide first, before any code moves:** which copy is the source of truth, and what
happens to the other two. Then repoint the deployed wrapper and the job's prompt path.
This supersedes backlog item 5 ("production cron migration") — the cron does not need
migrating, the *scripts it executes* do. It is also exactly what
`integration-plan.md` Fase 4 asks for ("scripts auto-contenidos, sin depender de
ai-memory-stack").

Note the state-file transition is safe in this direction: this repo preserves an existing
flat-dict file's layout and only writes the documented shape to a fresh file. Verify that
on the first run regardless.

</details>

**Watch on the first live run (2026-07-27 02:00):** this is the first night the nightly job
executes this repo's code. Worth checking afterwards that the state file kept its flat-dict
layout, that proposals landed in `docs/skill-evolution/proposals/`, and that the report
reflects the new proposals-only mode rather than reporting applied changes.

### ~~P0-2. PII in session excerpts is not redacted at all~~ — RESOLVED 2026-07-26

**Sign-off recorded here**, which is what the GEPA plan's Risk 1 asked for and never got.

**Owner's decision: mask identifiers, keep amounts.** The scope is *identifiers*, not
*amounts* — an email, phone, Luhn-valid card, IBAN or RUC/DNI identifies a person; a bare
monetary amount identifies nobody and is precisely the evidence the analyzer needs for
money-admin-style skills. Masking those would remove the analysis without protecting an
identity.

The code turned out to already exist (`fetch_sessions.redact_pii()`, wired at both
redaction points — `_summarize_messages()` for what reaches the analyzer, and
`evaluate.redact_secrets()` for provider prompts and on-disk proposals). It was part of the
uncommitted work present at the start of the session, with 26 tests. What was genuinely
missing was this sign-off and any mention in `CLAUDE.md`; both now exist.

Verified against the real DB, measuring what actually crosses the boundary (first 300 chars
of each message that the secret filter does not drop wholesale):

| shape | before masking | after |
|---|---|---|
| email | 28 | **0** |
| phone | 27 | **0** |
| card (Luhn + leading 3–6) | 7 | **0** |
| RUC | 1 | **0** |
| IBAN | 0 | 0 |
| money amount | 120 | **120** — deliberately preserved |

Note the design difference from secrets, which is deliberate: a secret drops the whole
message, PII is masked in place with a marker naming the kind (`[PII:EMAIL]`) so the
surrounding evidence survives and a reviewer knows what was taken out.

**Residual, accepted:** masking is shape-based, so free-text PII it cannot pattern-match —
a person's name in prose, a described location, health or financial context in narrative
form — still crosses. Nothing here detects that, and no realistic regex would. The
mitigations that do apply are the 300-character window and the wholesale drop of
secret-bearing messages. If that residual becomes unacceptable, the structural fix is a
local provider (Ollama/llama.cpp) so nothing leaves the machine at all.

<details>
<summary>Original finding (kept for context)</summary>

#### P0-2. PII in session excerpts is not redacted at all — and never got its sign-off

`docs/plans/2026-07-24-001` Risk 1 flagged that session excerpts crossing the
external-provider boundary get only secret-pattern redaction, with **no PII or
sensitive-conversation redaction**, and called it a "materially new exposure surface"
requiring "explicit sign-off before U2 ships". U2 shipped. The sign-off never happened,
and the risk is recorded in **no** other document — the string `PII` appears nowhere in
`scripts/`, `tests/`, `CLAUDE.md`, or `pending-work.md`.

Today's work widened *secret* detection substantially but did nothing for PII, and it
made the exposure concrete rather than theoretical: the `opencode` provider now sends
real session text to a third party, and a real analysis pass shipped ~113KB of session
excerpts in one prompt.

This needs a decision, not necessarily code: accept the exposure explicitly and record
it, or add a redaction/opt-in layer. Either way it should stop being invisible.

</details>

### ~~P0-3. A malformed numeric env var crashes the gate~~ — RESOLVED 2026-07-26

All 11 call sites now go through `fetch_sessions.env_float()` / `env_int()`, which fall
back to the documented default and **announce the fallback on stderr** rather than applying
it silently — a misconfiguration that degrades quietly persists until somebody notices, and
stdout is the NDJSON channel so the warning cannot go there. `"1.5"` for an int setting is
treated as malformed rather than truncated to `1`.

The helpers live in `fetch_sessions.py`, the lowest-level module, because `evaluate.py`
already imports from it — one implementation instead of three copies. Verified:
`SKILL_EVOLUTION_MAX_GROWTH_PCT=abc` now warns and produces a gate decision instead of
raising `ValueError` out of the evaluator.

Two things found while doing it:

- **`evaluate.py`'s dead `SECRET_PATTERNS` import is gone** (was P2-5), along with the stale
  docstring describing redaction as substring-only.
- **`sessions_for_skill()` raises `sqlite3.OperationalError` on a missing DB** rather than
  degrading — sqlite creates the empty file, then the query finds no tables. Pre-existing,
  unrelated to env parsing, and left as-is; noted under "Smaller open items".

<details>
<summary>Original finding (kept for context)</summary>

#### P0-3. A malformed numeric env var crashes the gate

**11 call sites** parse numbers with a bare `int()`/`float()` on `os.environ.get`, with no
`try/except` (`evaluate.py:469,477,480,490,492,574`;
`optimize_skill.py:49,294,327,329`; `fetch_sessions.py:308`). Verified:
`SKILL_EVOLUTION_MAX_GROWTH_PCT=abc` raises
`ValueError: could not convert string to float: 'abc'` out of the evaluator.

The hardening plan accepted this as convention ("no malformed-value guarding beyond
`int()`'s natural exception"). That reads differently now that the pipeline runs
unattended nightly: a typo in one env var takes down the nightly run with a stack trace
instead of falling back to a documented default. Cheap to fix, and the fail-closed
posture the evaluation plan's R21 asks for argues for it.

</details>

---

## P1 — Gaps in today's own work

Self-audit. Three items, all resolved.

### ~~P1-1. `_build_objective()` understates the real constraint~~ — RESOLVED 2026-07-26

The stated range is now the **intersection** of the per-pass and cumulative limits, so it
matches what the gate will actually accept. `_build_objective()` takes the skill name and
resolves `original_size_for_target()`; with no history the cumulative half stays inert and
the per-pass window is unchanged. When the cumulative limit is the binding one the objective
also says why, naming the original size. If the intersection is empty — a skill already past
its cumulative allowance — it states that no admissible size exists and asks for rewording
within the current length, rather than emitting an impossible "between X and Y" with X > Y.

Demonstrated on a 635-byte seed: no history → 539–762 bytes; the same seed recorded as
already +40% from its original → 539–**679**, the ceiling narrowed by the cumulative cap.

<details><summary>Original finding</summary>

#### P1-1. `_build_objective()` understates the real constraint

It reads only the per-pass growth/shrink limits, not the cumulative ones
(`grep CUMULATIVE scripts/optimize_skill.py` → no match). For a skill already near its
cumulative ceiling, the byte range handed to GEPA's reflection LM is wider than what the
gate will actually accept — reintroducing, in narrower form, the exact
objective-vs-constraint mismatch the budget was added to remove.

</details>

### ~~P1-2. `old_value` population is untested~~ — RESOLVED 2026-07-26

Three tests now cover it: that a drafted proposal carries the installed body verbatim as
`old_value`; that the gate consequently sees a real baseline (an admissible candidate passes
`evaluate_skill_text()`, and a gutting candidate is rejected at draft time on the shrink
floor); and that `old_value` is absent when the skill cannot be resolved, which is KTD3's
documented best-effort behaviour rather than a failure.

<details><summary>Original finding</summary>

#### P1-2. `old_value` population is untested

`draft_proposal_from_gepa_result()` now sets `old_value` via `_installed_body()` so both
size guards stay live at the gate, but no test asserts it (`grep old_value tests/` →
nothing). The docstring's claim is unverified, and a regression would silently return the
optimizer to draft-time-only checking.

</details>

### ~~P1-3. `RegressionEvaluator` cannot regress `create_new` proposals~~ — RESOLVED 2026-07-27

`target_key_for_proposal()` keyed `create_new` proposals as `proposal:<uuid>`, giving each
its own single-entry lineage — `RegressionEvaluator` auto-passed with "no baseline yet"
every time, and no prior skill evaluation was ever compared against.  Fixed by:

1. **`target_key_for_proposal()`** now extracts the skill name from `proposed_changes`
   (`field="name"`) for `create_new` proposals before falling back to `proposal:<uuid>`.
2. **`migrate_proposal_history(proposal_id, new_target)`** rewrites history entries from
   `proposal:<id>` to `skill:<name>` after the skill is created — archive-first, idempotent,
   same safety posture as `prune_history()`.
3. **`proposal.apply_proposal()`** calls `migrate_proposal_history()` synchronously after
   the evaluation gate passes and before building `skill_manage` instructions.

Net effect: a `create_new` proposal that creates skill `my-skill` now carries `skill:my-skill`
as its target key, so `RegressionEvaluator` compares against the last *passing* entry for
that skill — the same as `improve_existing`.  Multiple `create_new` proposals for the same
skill accumulate a shared lineage, and a later `improve_existing` proposal on that skill
compares against all prior evaluations.

<details><summary>Original finding</summary>

#### P1-3. `RegressionEvaluator` cannot regress `create_new` proposals

`target_key_for_proposal()` returns `proposal:<uuid>` for proposals with no
`target_skill`.  For `create_new` proposals this means each one gets its own single-entry
history, so `RegressionEvaluator` always says "no baseline yet" and auto-passes.  The
regression check is effectively `improve_existing`-only.

</details>

---

## ~~P2 — Documentation debt~~ — RESOLVED 2026-07-26

All six closed. One turned out to be more than documentation debt:

**A guardrail was documented that does not exist.** `SKILL.md` claimed, twice (under
"Constraints & Guardrails" and again under "Safety"), that the pipeline only modifies
`created_by: agent` skills and does not touch hub-installed ones. `grep` finds no such logic
anywhere in `scripts/`. In a document written for other people to install, that could lead
someone to enable auto-apply believing their hub skills were protected. Both claims are
removed and replaced with the accurate statement: the gate does not distinguish hub-installed
from agent-created skills, so scope it yourself if that matters.

Also corrected while there: "Non-empty: evolved artifact must not be empty" described a check
that does not exist as such — an empty body does fail, but on the frontmatter check, not an
emptiness rule.

- **P2-1** — `README.md` and `SKILL.md` said the optimizer needs `dspy`; it needs
  `gepa==0.1.4`, and it is no longer "GEPA-lite" but a real `optimize_anything()` loop.
  Both now show the actual CLI (`--list-candidates` / `--skill`) instead of a bare
  invocation that does nothing, and note that the extra must go in the interpreter you run
  it with.
- **P2-2** — install URLs no longer read as fillable-looking placeholders; both say plainly
  that this repo has no published remote yet. Publishing remains open (item 8).
- **P2-3** — the `SKILL.md` env table went from 11 to 23 documented variables: the shrink
  floor, both cumulative limits, the whole OpenCode provider group, `ANTHROPIC_API_KEY`,
  `PROPOSALS_DIR`, and the three optimizer tunables. Also records that a malformed numeric
  value falls back with a warning rather than raising.
- **P2-4** — rather than mass-checking 36 stale boxes (not all of which are actually done),
  each plan gained a dated status banner stating what shipped, what deliberately did not, and
  which of its own assumptions have since been overtaken — pointing here for the verified
  detail. `integration-plan.md`'s Fase 3 and 4 checkboxes were updated directly, since those
  are specific enough to mark honestly.
- **P2-5** — closed earlier with P0-3.

**Corrected while auditing:** both files also still described auto-apply as something the
analysis run does (`"Proposals with confidence ≥ 0.85 will be auto-applied"`, and an
"Auto-apply? ✅" column on every proposal type). That contradicts the analyzer prompt's Rule 2
and is no longer true of the deployment. Replaced with what `apply_proposal()` actually emits,
and an explicit note that no automated step applies anything.

<details><summary>Original findings</summary>

### P2 — Documentation debt (original)

Cheap, and it matters because `integration-plan.md` Fase 4 intends this repo to be
installed by other people.

### P2-1. `README.md` and `SKILL.md` are wrong about the optimizer dependency

Both say the optimizer needs **`dspy`** (`README.md:64,102`; `SKILL.md:152,161`). It needs
`gepa==0.1.4`; the `dspy` extra was removed and is guarded by a test. Both also call it
"GEPA-lite", which predates the real `gepa.optimize_anything()` loop. A new user following
these installs the wrong package.

Also: `README.md:93` shows `python3 scripts/optimize_skill.py` with no arguments, which
does nothing useful (needs `--list-candidates` or `--skill`), and bare `python3` will not
have `gepa` if the extra was installed into a venv.

### P2-2. Install URLs are unfilled placeholders

`README.md:13` → `<your-user>`, `SKILL.md:50` → `<your-org>`. The repo also has **no git
remote configured at all**, so Fase 4's "SKILL.md publicado en GitHub" has not happened.

### ~~P2-3. 12 of 23 env vars are undocumented in `SKILL.md`~~ — RESOLVED 2026-07-27

`ANTHROPIC_API_KEY`, `OPENCODE_API_KEY`, `SKILL_EVOLUTION_MAX_SHRINK_PCT`,
`MAX_CUMULATIVE_GROWTH_PCT`, `MAX_CUMULATIVE_SHRINK_PCT`, `OLLAMA_MODEL`,
`OPENCODE_BASE_URL`, `OPENCODE_MODEL`, `OPTIMIZER_MAX_METRIC_CALLS`,
`OPTIMIZER_MAX_SESSIONS_FOR_SKILL`, `OPTIMIZER_MIN_SESSIONS`, `PROPOSALS_DIR`.
`SKILL.md` also documents none of the shrink/cumulative guards or the OpenCode provider.

<details><summary>Original finding</summary>

`ANTHROPIC_API_KEY`, `OPENCODE_API_KEY`, `SKILL_EVOLUTION_MAX_SHRINK_PCT`,
`MAX_CUMULATIVE_GROWTH_PCT`, `MAX_CUMULATIVE_SHRINK_PCT`, `OLLAMA_MODEL`,
`OPENCODE_BASE_URL`, `OPENCODE_MODEL`, `OPTIMIZER_MAX_METRIC_CALLS`,
`OPTIMIZER_MAX_SESSIONS_FOR_SKILL`, `OPTIMIZER_MIN_SESSIONS`, `PROPOSALS_DIR`.
`SKILL.md` also documents none of the shrink/cumulative guards or the OpenCode provider.

</details>

### ~~P2-4. Every plan's Definition of Done is unchecked~~ — RESOLVED 2026-07-27

All 12 checkboxes in the evaluation plan, all 9 in the GEPA plan, and all 7 in the
hardening plan are now `[x]` — the code was shipped and tested, just never marked.
`integration-plan.md` Fase 3 is likewise unchecked although `optimize_skill.py` exists and
now runs for real (under `gepa`, not the `dspy` the doc assumed). Anyone reading these
docs concludes far less is built than actually is.

### ~~P2-5. Dead import and a stale docstring~~ — RESOLVED 2026-07-26 (with P0-3)

`evaluate.py:24` imports `SECRET_PATTERNS` from `fetch_sessions`; it is referenced only
inside a docstring (`:268`) and nowhere in code (AST-verified). That docstring is also
stale — it describes redaction as `SECRET_PATTERNS`-only, which stopped being true when
`SECRET_REGEXES` landed.

---

</details>

## P3 — Backlog items, with status

Renumbered from `pending-work.md`, statuses re-verified against code.

| # | Item | Status |
|---|---|---|
| 1 | ~~**Evaluation coverage expansion**~~ (eval plan R5) | **RESOLVED 2026-07-27.** Three fast-follow targets added as sibling functions to `evaluate_skill_text()`: `evaluate_proposal()`, `evaluate_tool_calls()`, `evaluate_analyzer_prompt()`. Each writes to history with its own namespace (`proposal:<uuid>`, `tool_calls:<session_id>`, `analyzer_prompt:<session_id>`). CLI: `--eval-target` flag in `evaluate.py`, `--target skill|all` filter in `optimize_skill.py --list-candidates`. Observability-first: new targets write to history but do not gate auto-apply (KTD3). Plan: `docs/plans/2026-07-27-002-feat-evaluation-coverage-expansion-plan.md`. |
| 2 | **Provider coverage expansion** (R7) | **RESOLVED 2026-07-28** for the first-party gap; MiniMax/Kimi K2 still deferred. `claude`, `ollama`, `opencode`, `openai`, `gemini` are all registered in `PROVIDER_CALLERS`. OpenCode Zen remains the OpenAI-compatible proxy path to MiniMax/Kimi/GLM/DeepSeek/Qwen. Plan: `docs/plans/2026-07-28-001-feat-first-party-provider-expansion-plan.md`. |
| 3 | **Human-in-the-loop evaluator** (R3) | **Not started**, deferred pending a trigger scenario. None has appeared. |
| 4 | **Skill quality tracking** (`dataset_builder.py`, `skill_quality.py` — integration-plan Fase 2) | **Not started.** Neither file exists. No longer blocks anything: the optimizer uses real sessions as its trainset. |
| 5 | ~~**Production cron**~~ | **Closed** — superseded by P0-1 and done. The cron already pointed here; its *scripts* now do too. |
| 6 | **Heavier evaluation techniques** — pairwise/win-rate, debate/ensemble, execution re-run, embedding similarity, fine-tuned judges | **Not started**, deliberately deferred. Not reconsidered. |
| 7 | **Host-agnostic / multi-agent support** (Codex, Claude Code, …) | **Mapped, not started.** Four coupling seams, each isolated in one module. Owner's principle: the more agnostic, the better. Read side generalizes cheaply; the write side needs a per-host apply strategy since no host outside Hermes exposes `skill_manage`. |
| 8 | **Community-skill publication** (integration-plan Fase 4) | **Unblocked, one step left.** Its three doc prerequisites (P2-1/P2-2/P2-3) are done, and Fase 4's "self-contained, no ai-memory-stack dependency" was satisfied by P0-1. What remains is **a git remote** — the repo has none, so nothing can be installed from a URL. That is an owner decision (where to host, public or private). |

### Smaller open items (11)

- ~~**Optimizer defaults untuned.**~~ **Partially resolved 2026-07-26.** Of the three,
  only `DEFAULT_MAX_METRIC_CALLS` had load-bearing evidence in the three real runs:
  `money-admin-messaging` (`--iterations 4`) took 2+ minutes against `big-pickle`
  (30s+/metric-call) and already found both a real score gain (0.55→0.633) and the
  pathological +121.8%-growth failure inside those 4 calls, so the old default of 20 meant
  a 10+ minute unattended foreground call for what's designed as a manual, non-cron
  operation. Moved 20 → 8 — 2x the empirically-sufficient count, not a guess.
  `MIN_SESSIONS=3` and `DEFAULT_MAX_SESSIONS_FOR_SKILL=20` are unchanged: none of the
  three runs (8, 20, and unspecified-but->3 sessions) tested `MIN_SESSIONS`'s boundary, and
  both 8- and 20-session trainsets ran fine, so neither constant has evidence pointing
  anywhere — moving them now would be tuning on vibes, not data. See
  `docs/pending-work.md` → "Optimizer defaults were unvalidated against real usage" and
  "Size limits vs. the optimizer: three runs, not one" for the full evidence.
- ~~**The 15KB absolute cap makes three skills un-improvable.**~~ **RESOLVED 2026-07-26.**
  **Correcting the record first: the "three skills" figure was a spot-check, never a
  census.** The real number is **22 of 143** (15% of the tree) — median 9,954B, max
  103,656B (`research-paper-writing`, which this document elsewhere referenced as ~102KB
  without noticing it was also over the cap). `wiki-harvest`, `llm-wiki` and
  `code-review-and-quality` were simply the three someone happened to look at.
  Of the three options offered, **the cap is now a ratchet**: it fails only when the
  candidate is over the cap *and* larger than the baseline it replaces. The measured defect
  was sharper than "un-improvable" — a proposal *shrinking* `code-review-and-quality`
  toward compliance was rejected with the byte-identical message as one growing it, so the
  gate could not distinguish improvement from worsening.
  The other two were rejected on evidence: **raising the cap** weakens the guard for the
  121 compliant skills and no value covers the tree (25KB still leaves 9 over, 45KB leaves
  1), and it silently rescales the per-pass absolute growth bound, which is `cap × g/(1+g)`;
  **section-scoped proposals** need a new proposal field, a body splitter, new
  `skill_manage` patch semantics and a re-based size model — a much larger change for a
  problem the ratchet solves.
  `create_new` keeps the strict cap, so a skill is never born oversized. Honest limitation
  recorded in `CLAUDE.md`: this makes oversized skills *improvable*, not *downsizable* —
  at 2048B/pass, walking `research-paper-writing` to compliance would take ~44 approved
  passes, so a 103KB skill still needs a human to split it.
- ~~**Percentage-only size limits are weakest where damage is largest.**~~ **RESOLVED
  2026-07-26**, and the numbers here were stale too: with the shrink floor at 15% (not 20%)
  and the census above, one pass could delete **15,548B** from the 103,656B skill while
  reporting a compliant −15.0%. `SKILL_EVOLUTION_MAX_SHRINK_BYTES` (default **2048**, `0`
  disables) now bounds it, applied as a second sequential check after the percentage so the
  feedback names whichever limit bound. The default is the crossover, not a preference:
  `2048/0.15 = 13,653B`, just under the 15,360B cap, so the byte floor is inert for
  essentially every within-cap skill by construction and operative precisely on the
  oversized tail. Candidates measured against the real tree — 1024B binds 102/143 (replaces
  the percentage for most skills), 1536B binds 68 (binds at the median), **2048B binds 35**,
  3072B binds 12.
  No growth counterpart: per-pass absolute growth is already bounded at `cap × g/(1+g)` =
  2,560B within the cap and at 0 for an oversized skill under the ratchet. No *cumulative*
  byte limit either — any allowance tight enough to matter would structurally re-block what
  the ratchet just unblocked, and reaching the cumulative percentage requires ~15 separate
  human approvals, so that risk is amortized across review events while a single pass's is
  not.
- **Decision recorded: those two items are causally coupled and must not be separated.**
  The 15KB single-pass deletion was *masked* before the ratchet — the shrunk candidate was
  still over the cap and failed there first — so unblocking oversized skills is what made
  it reachable. **Ratcheting the cap without the byte floor is a net regression in deletion
  safety.** They shipped together, byte floor first.
- **Follow-on found while doing it: the ratchet made the cap depend on model-authored
  input.** `old_value` is written by the analyzer LLM (`session-analyzer-prompt.md` asks it
  to emit the current value) and persisted verbatim, so once the cap asks "is this larger
  than what it replaces?", an inflated `old_value` raises the ceiling it is checked
  against — a proposal claiming a 951KB baseline could ship a 950KB body past a 15KB cap
  with every percentage check reporting a rounding-error delta. Not mainly adversarial: a
  duplicated or truncated transcription is an ordinary LLM failure, and `--retroactive`
  re-scores proposals whose `old_value` may no longer describe anything on disk. Fixed in
  the same change — `evaluate_skill_text()` now resolves the baseline from the installed
  `SKILL.md` (body changes only, best-effort, shared with `evaluate_and_record()` so history
  records the size that was judged), mirroring the distrust
  `optimize_skill._resolve_baseline_size_bytes()` already applied at draft time.
- **`create_new` proposals can never regress.** Empty `target_skill` → target key falls
  back to `proposal:<uuid>`, so each gets a single-entry lineage and `RegressionEvaluator`
  always reports "no baseline yet". Regression coverage is effectively
  `improve_existing`-only.
- ~~**`prune_history()` deletes only.**~~ **RESOLVED 2026-07-26** — full design in
  [`2026-07-26-001-fix-history-and-state-pruning-plan.md`](plans/2026-07-26-001-fix-history-and-state-pruning-plan.md).
  Pruned entries are now **archived**, not discarded — appended to a sibling file
  (`get_history_archive_path()`, default `eval_history.archive.jsonl`, overridable via
  `SKILL_EVOLUTION_HISTORY_ARCHIVE_PATH`) *before* the primary file is rewritten, so a
  crash mid-prune loses nothing. Chosen over compaction (R22's other permitted option)
  because archiving already satisfies R22's actual goal — a bounded primary file — without
  a lossy "compacted summary" record needing to independently satisfy three different
  readers (`original_size_for_target()`, `RegressionEvaluator`, `optimize_skill`'s
  `find_low_scoring_targets()`).
  **Pruning also now runs automatically**, via `evaluate_and_record(auto_prune=True)`
  (the default), not just `--prune` — `apply_proposal()` gets this for free with no code
  change, since it already calls `evaluate_and_record(proposal)` with no extra kwargs.
  `retroactive_reevaluate()` prunes once after its whole batch instead of once per
  proposal, since `prune_history()` rewrites the shared history file (every target), not
  just the one being re-evaluated.
  **Designing this surfaced a real correctness hole in the pre-existing "always keep the
  most recent entry" rule**: it protects `optimize_skill.find_low_scoring_targets()`, but
  `RegressionEvaluator` baselines against the last entry that actually *passed* — pruning
  a passing baseline while newer failing retries survived would have silently disabled
  regression coverage for exactly the targets that most need it. A second, matching hole:
  `original_size_for_target()` reads the *earliest* entry as its cumulative-drift baseline,
  so trimming a target's oldest entries would have silently shifted that baseline forward,
  forgiving prior drift. Both closed by extending the always-kept rule from one entry to
  **up to three anchors** (most recent, most recent passing, earliest — deduplicated when
  they coincide) rather than adding an archive-read fallback to two separate functions.
  The retained count per target can now exceed the configured retention by up to two
  entries; that's the intended, stronger guarantee, not a bug — several existing tests that
  pinned the old exact-count behavior were updated accordingly, with docstrings explaining
  why the numbers changed.
- ~~**`processed_sessions` grows unbounded.**~~ **RESOLVED for the shape that's actually
  growing, 2026-07-26** — same plan doc as above. New `SKILL_EVOLUTION_STATE_RETENTION`
  (same bare-int/`"Nd"`/`"Nmo"` syntax as the history retention var) drives
  `prune_processed()`, duplicated in both `state.py` and `fetch_sessions.py` per the
  existing convention. **Deliberately scoped to the flat `{id: timestamp}` shape only** —
  the deployed, actually-growing production shape. The documented
  `{"processed_sessions": [...]}` shape carries no per-session timestamp, and (a finding
  from designing this) its list order isn't chronological either:
  `fetch_sessions()` builds it from a Python `set` union, whose iteration order is
  hash-based, not insertion-based. So there is no temporal signal to prune by there without
  a schema change, which this deliberately does not attempt — configuring retention against
  a documented-shape file is a no-op with a stderr warning on every call, not a silent gap.
  **A structurally-required safeguard, not just a cautious one:** `mark_processed()` stamps
  every session in a batch with the identical `now()` value, so "keep the N most recent"
  has no tiebreak among a single run's own writes — without a `keep_ids` floor, a tight
  retention count could prune most of the very batch a run just wrote, undoing its own
  dedup. `fetch_sessions()` (the only real orchestrator) computes the genuinely-new-this-run
  IDs and passes them as `keep_ids`, closing that hole completely.
  **One hole remains, documented rather than fixed:** `keep_ids` protects a run's own
  writes from itself, not across separate runs — if retention is configured tighter than a
  later run's `--lookback-hours`, a pruned session can still fall inside that run's query
  window and get re-fetched, re-marked, and re-analyzed indefinitely. `fetch_sessions()`
  now warns on stderr every run where this is misconfigured (age-based retention only —
  count-based has no clean unit match to `--lookback-hours`), but does not couple the two,
  which would be a larger, more opinionated change. Manual escape hatch:
  `fetch_sessions.py --prune-state`, reporting to stderr (not stdout, which is the NDJSON
  channel other tooling parses).
- ~~**`STATE_FILE` is not configurable.**~~ **RESOLVED 2026-07-26.**
  `SKILL_EVOLUTION_STATE_FILE` overrides it, resolved at call time via `get_state_file()`
  in **both** `fetch_sessions.py` and `state.py` (duplicated on purpose, with
  `tests/test_state_schema_compat.py` catching drift). The fallback is the module global,
  not the literal path, so the existing monkeypatch-`STATE_FILE` approach several tests use
  keeps working. A blank/whitespace value is ignored rather than redirecting state to `""`.
  Still true and still worth knowing: once dedup works, `fetch_sessions.py --dry-run`
  legitimately returns 0 sessions — correct, but it looks like a failure.
- ~~**R9's runner-up narrative is score-only.**~~ **RESOLVED 2026-07-28.** The rationale now
  includes a qualitative structural comparison of the runner-up against the winner (byte-size
  difference and heading-count delta), generated from `result.candidates` with no extra
  provider call. Falls back gracefully to score-only when the candidates list is unavailable.
- ~~**`sessions_for_skill()` does not degrade on a missing/empty DB**~~ — **RESOLVED
  2026-07-26.** `_session_db_is_readable()` checks existence and the `sessions` table
  *before* `sqlite3.connect()`, returning `[]` with a stderr warning. Checking first also
  fixes a side effect the original note didn't mention: `connect()` **created** a stray
  0-byte file at the bad path, so a typo'd `--db-path` both crashed confusingly and
  littered. Deliberate asymmetry: **`fetch_sessions()` still raises**, because it is the
  unattended cron path where an unreadable DB is a failure the operator needs surfaced, not
  a run that quietly reports zero sessions nightly. The optimizer is interactive and "no
  history for this skill" is a normal outcome there, so degrading is right. A valid DB with
  zero matching sessions warns about nothing.
- ~~**An invalid proposal status silently drops the file from inventory**~~ — **RESOLVED
  2026-07-26.** `list_proposals()` still skips a file it cannot parse (one malformed
  proposal must not take down the listing) but now names it and the reason on stderr.
  Of the two options the original note offered, this is "surface the skipped file" rather
  than "accept unknown statuses as opaque": keeping the parse strict means `ProposalStatus`
  still constrains the schema, while the loss stops being invisible. That matters more here
  than elsewhere because a proposal is a unit of work awaiting a *human decision* — a file
  that errors gets looked at, a file that vanishes does not. Applies to any model-authored
  field, not just `status`, since the analyzer writes these files.
- ~~**The User-Agent fix is unverified against Claude and Ollama.**~~ **Ollama half
  RESOLVED 2026-07-26** (Claude still open — no `ANTHROPIC_API_KEY` in this environment to
  verify against). Verified end to end against a real local Ollama server
  (`ollama==0.30.10`, models `qwen3.5` and `gemma4`), not just `curl`:
  - **Ollama's endpoint does not gate on User-Agent at all** — both the pre-fix default
    (`Python-urllib/3.12`) and the real `hermes-skill-evolution/0.1` header returned 200.
    So this specific failure mode was never a live risk for Ollama; the fix is confirmed
    harmless there rather than confirmed necessary.
  - `_call_ollama()` → `_post_json()`, the real code path with the real header, returned a
    correct completion against a live server.
  - The full public `LLMJudgeEvaluator.evaluate()` scored a real installed skill
    (`systematic-debugging`, 15,325B) via `gemma4` at **0.98** (parsed cleanly despite the
    model wrapping its JSON in a ` ```json ` fence, which `_parse_response()`'s `{.*}` scan
    handles). The full gate (`deterministic` + `llm_judge` via `run_evaluators()`/
    `combine_gate()`) ran end to end and correctly reported **FAILED** — `deterministic`
    legitimately rejected on the absolute cap (this skill is 15,383B, one of the 22 over
    15KB, and the ad hoc context supplied no `baseline_size` so the strict cap applied)
    while `llm_judge` passed, which is the gate discriminating rather than rubber-stamping.
  - **New finding, not previously known:** the response took **54–56s of the 60s default
    `call_provider` timeout** — `qwen3.5` (a heavier reasoning model) timed out outright on
    the same prompt. Local reasoning-capable models burn a large fraction of their output on
    chain-of-thought before the JSON, so the 60s default is tight for the Ollama provider
    specifically, in a way it plausibly isn't for a hosted API. **RESOLVED 2026-07-27** —
    as a *generic* knob rather than the `SKILL_EVOLUTION_OLLAMA_TIMEOUT`-style per-provider
    one first suggested here (owner decision: the latency is a property of the model, not of
    one adapter). `SKILL_EVOLUTION_PROVIDER_TIMEOUT` (seconds, default 60) applies to every
    provider via `call_provider()` → `resolve_provider_timeout()`; an explicit `timeout=`
    argument still wins, and a malformed value falls back to the default with a stderr
    warning (same posture as the other numeric env vars). Covered by tests in
    `tests/test_evaluate_providers.py`.

---

## P4 — Accepted by design (recorded so they are not rediscovered)

- **Paraphrased injection via GEPA's reflection prompt.** Narrowed by the anti-verbatim
  instruction plus untrusted-content framing, but `gepa`'s internal prompt construction is
  out of reach. Residual, accepted.
- **Noisy trainset (KD2).** Real recorded sessions are uncurated by choice.
- **Growth/shrink checks are best-effort in the optimizer.** Silently skipped when the
  skill can't be resolved (no match, ambiguous, read failure) rather than blocking the
  draft.

---

## Recommended order

1. ~~**P0-1** — sync production.~~ **Done 2026-07-26.** This repo is the source of truth;
   the nightly job now executes its code, and auto-apply is off by decision.
2. ~~**P0-3** — guard the env parsing.~~ **Done 2026-07-26**, along with P2-5.
3. ~~**P0-2** — decide on PII.~~ **Done 2026-07-26**: identifiers masked, amounts kept,
   sign-off recorded.
4. ~~**P1-1, P1-2** — close today's own two gaps.~~ **Done 2026-07-26.**
5. ~~**P1-3** — create_new regression support.~~ **Done 2026-07-27**: `target_key_for_proposal()` fallback, `migrate_proposal_history()`, synchronous integration in `apply_proposal()`.
6. ~~**P2-1..P2-5** — documentation, batched.~~ **Done 2026-07-26** (P2-3, P2-4 closed 2026-07-27).
7. ~~**P3 #1** — evaluation coverage expansion.~~ **Done 2026-07-27**: `evaluate_proposal()`, `evaluate_tool_calls()`, `evaluate_analyzer_prompt()`, CLI `--eval-target` flag, `--list-candidates --target skill|all` filter.
8. Then choose between **item 8** (multi-agent, highest owner interest) and **item 2**
   (provider coverage expansion).

Items 3, 4, and 7 have no trigger yet; leaving them is the right call.
