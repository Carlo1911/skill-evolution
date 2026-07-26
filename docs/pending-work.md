# Pending Work

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
and `docs/integration-plan.md`. The GEPA optimizer was one; these six are
untouched.

1. **Evaluation coverage expansion.** Only `evaluate_skill_text()` exists.
   Missing: proposal-quality, tool-call-quality, and analyzer-prompt-quality
   evaluation targets (evaluation plan's R5 fast-follow scope). Reuses the
   existing evaluator registry and gate — no dependency on GEPA.
2. **Provider coverage expansion.** *(Partially done.)* Claude,
   Ollama/llama.cpp, and **OpenCode Zen** (`opencode`) are implemented — the
   latter is an OpenAI-compatible gateway, so it reaches MiniMax, Kimi, GLM,
   DeepSeek and Qwen families through one adapter, covering most of R7's named
   targets by proxy. Still missing as first-party branches: OpenAI and Gemini
   direct. See the OpenCode notes at the bottom for the Zen-vs-Go catalogue trap.
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
   **The gating condition as worded is not directly executable** (found in the
   Fase 0 pass below): `--retroactive` re-evaluates *saved proposals*, not
   sessions — `_select_retroactive_proposals()` loads proposals from disk and
   filters `status == proposed`. With no proposals on disk it is a clean no-op
   ("Nothing to re-evaluate"), and `--list-candidates` is likewise a no-op with
   no `eval_history.jsonl`. Restate the gate as "a clean retroactive run over
   real *proposals*", and note that proposals only come into existence from the
   LLM analyst step, the optimizer, or by hand — so bootstrapping the corpus is
   itself a prerequisite step, not a side effect.
6. **Heavier evaluation techniques.** Pairwise/win-rate comparison,
   debate/ensemble judging, execution-based re-run verification,
   embedding-similarity checks, fine-tuned local judges — catalogued and
   deliberately deferred in the evaluation plan's Scope Boundaries. Not
   reconsidered since.

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
  configurable, not yet tuned.)* `MIN_SESSIONS`, `DEFAULT_MAX_METRIC_CALLS`,
  and `DEFAULT_MAX_SESSIONS_FOR_SKILL` are now overridable via
  `SKILL_EVOLUTION_OPTIMIZER_MIN_SESSIONS`,
  `SKILL_EVOLUTION_OPTIMIZER_MAX_METRIC_CALLS`, and
  `SKILL_EVOLUTION_OPTIMIZER_MAX_SESSIONS_FOR_SKILL`, with today's values
  (3 / 20 / 20) kept as defaults. Actually tuning those numbers against real
  usage data is still unstarted — see "GEPA optimizer: shipped but not yet
  run for real" below.

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

### Blocker: the optimizer's objective ignores the constraint it is gated on

The run produced **no proposal**. `draft_proposal_from_gepa_result()`'s
structural check rejected the winner:

```
growth 121.8% exceeds 20% limit over baseline
```

Baseline 1861B; the 20% cap allows 2233B; the winning candidate was ~4127B.

This is a design conflict, not a bug — both halves did exactly what they were
built to do. GEPA optimizes purely for the judge's score, gepa's reflective
mutation expands prose to raise it, and *nothing in the objective string tells
it a hard size ceiling exists*. So the search reliably converges on candidates
the gate must then throw away. Note the judge's rubric already includes
`conciseness` and the longer document still won, meaning correctness and
procedure-following gains outweighed it — a rubric weight will not fix this on
its own.

As configured, `optimize_skill.py` is therefore near-unproductive for any skill
whose improvements involve added detail: it burns metric calls to produce
candidates that are structurally inadmissible. Options, cheapest first:

1. **State the constraint in the objective** — pass the byte ceiling into
   `run_gepa_optimization()`'s `objective` string so the reflection LM optimizes
   inside the budget it will be judged against. Most principled: today the
   search optimizes one objective and is gated on another.
2. **Per-origin growth policy** — let optimizer-drafted proposals carry a
   higher cap (a `SKILL_EVOLUTION_MAX_GROWTH_PCT_OPTIMIZER`-style override),
   accepting that GEPA rewrites wholesale rather than patching.
3. **Reject early** — score candidates that breach the ceiling at 0 inside
   `score_candidate()` so gepa learns the constraint through its own feedback
   loop rather than discovering it after the run ends.
4. **Leave as-is** and treat the optimizer as a tightening-only tool.

Whichever is chosen should be decided before item 5's cron migration: an
auto-apply loop that regularly discards its own optimizer output is wasted
budget.

```bash
pip install -e ".[dev,optimizer]"
SKILL_EVOLUTION_OPTIMIZER_ENABLED=true python3 scripts/optimize_skill.py --list-candidates
SKILL_EVOLUTION_OPTIMIZER_ENABLED=true python3 scripts/optimize_skill.py --skill <name>
```

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
