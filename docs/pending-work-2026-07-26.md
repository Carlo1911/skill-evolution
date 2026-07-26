# Pending Work — consolidated backlog as of 2026-07-26

Built by reading every document in the repo (`CLAUDE.md`, `README.md`, `SKILL.md`,
`docs/integration-plan.md`, `docs/session-analyzer-prompt.md`, all three plans under
`docs/plans/`, and `docs/pending-work.md`) and cross-checking each deferred item against
the code as it stands after today's commits (`a5e754d`..`13fd4bd`).

**Relationship to `docs/pending-work.md`:** that file stays as the *evidence record* — the
Fase 0 validation narrative, the measured numbers, the retracted conclusion, the bug
post-mortems. This file is the *actionable backlog*. It references that evidence rather
than restating it, so the two do not drift. (Three divergent copies of
`fetch_sessions.py` are what caused P0-1 below; duplicating prose is the same failure mode
in a cheaper form.)

Test baseline: **351 passed** with `gepa` installed, **347 passed + 1 skipped** without
(was 253/249 when this document was first written; the P0 and P1 items added the rest).

---

## P0 — Production and security

Nothing else in this document is urgent. These three are.

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

Self-audit. Both are small and both are mine.

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

---

## ~~P2 — Documentation debt~~ — RESOLVED 2026-07-26

All five closed. One turned out to be more than documentation debt:

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

### P2-3. 12 of 23 env vars are undocumented in `SKILL.md`

`ANTHROPIC_API_KEY`, `OPENCODE_API_KEY`, `SKILL_EVOLUTION_MAX_SHRINK_PCT`,
`MAX_CUMULATIVE_GROWTH_PCT`, `MAX_CUMULATIVE_SHRINK_PCT`, `OLLAMA_MODEL`,
`OPENCODE_BASE_URL`, `OPENCODE_MODEL`, `OPTIMIZER_MAX_METRIC_CALLS`,
`OPTIMIZER_MAX_SESSIONS_FOR_SKILL`, `OPTIMIZER_MIN_SESSIONS`, `PROPOSALS_DIR`.
`SKILL.md` also documents none of the shrink/cumulative guards or the OpenCode provider.

### P2-4. Every plan's Definition of Done is unchecked

All 12 checkboxes in the evaluation plan, all 9 in the GEPA plan, and all 4 units in the
hardening plan are `[ ]` despite the corresponding code being shipped and tested.
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
| 1 | **Evaluation coverage expansion** — proposal-quality, tool-call-quality, analyzer-prompt-quality targets (eval plan R5) | **Not started.** Only `evaluate_skill_text()` exists; registry holds exactly 3 evaluators. The seam is a comment (`evaluate.py:754-758`). Note R6's `messages.tool_calls` read path already exists but only for session *selection* (`fetch_sessions.py:289`), not evaluation. Blocks the plan's own "shared-core extensibility is provisional" claim from being validated. |
| 2 | **Provider coverage expansion** (R7) | **Partial.** `claude`, `ollama`, `opencode`. OpenCode Zen is OpenAI-compatible so it reaches MiniMax/Kimi/GLM/DeepSeek/Qwen by proxy — most of R7's named targets. Missing: first-party OpenAI and Gemini branches. |
| 3 | **Human-in-the-loop evaluator** (R3) | **Not started**, deferred pending a trigger scenario. None has appeared. |
| 4 | **Skill quality tracking** (`dataset_builder.py`, `skill_quality.py` — integration-plan Fase 2) | **Not started.** Neither file exists. No longer blocks anything: the optimizer uses real sessions as its trainset. |
| 5 | **Production cron** | **Superseded by P0-1** — the cron already points here; its *scripts* do not. |
| 6 | **Heavier evaluation techniques** — pairwise/win-rate, debate/ensemble, execution re-run, embedding similarity, fine-tuned judges | **Not started**, deliberately deferred. Not reconsidered. |
| 7 | **Host-agnostic / multi-agent support** (Codex, Claude Code, …) | **Mapped, not started.** Four coupling seams, each isolated in one module. Owner's principle: the more agnostic, the better. Read side generalizes cheaply; the write side needs a per-host apply strategy since no host outside Hermes exposes `skill_manage`. |
| 8 | **Community-skill publication** (integration-plan Fase 4) | **Not started.** Needs P2-1/P2-2/P2-3 plus a git remote. Overlaps P0-1: Fase 4's "self-contained, no ai-memory-stack dependency" is the same requirement. |

### Smaller open items

- **Optimizer defaults untuned.** `MIN_SESSIONS=3`, `DEFAULT_MAX_METRIC_CALLS=20`,
  `DEFAULT_MAX_SESSIONS_FOR_SKILL=20` are env-configurable but never tuned against usage.
  Three real runs exist now to reason from.
- **The 15KB absolute cap makes three skills un-improvable.** `wiki-harvest` (40KB),
  `llm-wiki` (23KB), `code-review-and-quality` (20KB) already exceed
  `SKILL_EVOLUTION_MAX_SKILL_SIZE_KB`, so *any* body proposal for them fails on size
  regardless of delta. Decide: raise the cap, support partial/section-scoped proposals, or
  accept that they are manual-only.
- **Percentage-only size limits are weakest where damage is largest.** 15% of the median
  9,748B skill is ~1.4KB; 15% of the largest (102KB) is ~15KB. An absolute byte floor
  alongside the percentages is the natural complement.
- **`create_new` proposals can never regress.** Empty `target_skill` → target key falls
  back to `proposal:<uuid>`, so each gets a single-entry lineage and `RegressionEvaluator`
  always reports "no baseline yet". Regression coverage is effectively
  `improve_existing`-only.
- **`prune_history()` deletes only.** Eval plan R22 says entries "may be archived or
  compacted"; only deletion exists, and it is CLI-invoked (`--prune`) — never called
  automatically, so unattended runs grow the file indefinitely.
- **`processed_sessions` grows unbounded.** No pruning of the state file either.
- **`STATE_FILE` is not configurable.** A module constant with no env override, so running
  against isolated state requires monkeypatching. Note that once dedup works,
  `fetch_sessions.py --dry-run` legitimately returns 0 sessions — correct, but it looks
  like a failure.
- **R9's runner-up narrative is score-only.** The plan asks the rationale to report *what
  trade-off* the runner-up made; the code reports its score and margin, not a qualitative
  description.
- **`sessions_for_skill()` does not degrade on a missing/empty DB** — it raises
  `sqlite3.OperationalError` because sqlite creates the file and the query then finds no
  tables. Only reachable via the optimizer path, so low impact, but it is an unhandled
  crash rather than an empty result.
- **An invalid proposal status silently drops the file from inventory** —
  `list_proposals()` swallows `load_proposal()`'s `ValueError`, so a model-authored
  status like `already_covered` makes a proposal vanish from `--list` and
  `--retroactive` with no warning. Seen for real in the ai-memory-stack proposals.
- **The User-Agent fix is unverified against Claude and Ollama.** It changed shared
  `_post_json()` behavior and was only exercised against OpenCode Zen.

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
5. ~~**P2-1..P2-5** — documentation, batched.~~ **Done 2026-07-26.**
6. Then choose between **item 7** (multi-agent, highest owner interest) and **item 1**
   (evaluation coverage, unblocks the shared-core claim).

Items 3, 4, and 6 have no trigger yet; leaving them is the right call.
