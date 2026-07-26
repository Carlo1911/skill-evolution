# Pending Work

Status snapshot as of 2026-07-26, after the real GEPA optimizer (see
`docs/plans/2026-07-24-001-feat-real-gepa-optimizer-plan.md`) and the
follow-up hardening pass (see
`docs/plans/2026-07-25-001-fix-skill-evolution-hardening-plan.md`) shipped to
`main`. This is a decision inventory, not a committed roadmap — pick an item,
then run it through `ce-brainstorm`/`ce-plan` when you're ready to scope it.

## Not yet built (from the original gap analysis)

Seven areas were identified against `docs/plans/2026-07-23-001-feat-skill-evolution-evaluation-plan.md`
and `docs/integration-plan.md`. The GEPA optimizer was one; these six are
untouched.

1. **Evaluation coverage expansion.** Only `evaluate_skill_text()` exists.
   Missing: proposal-quality, tool-call-quality, and analyzer-prompt-quality
   evaluation targets (evaluation plan's R5 fast-follow scope). Reuses the
   existing evaluator registry and gate — no dependency on GEPA.
2. **Provider coverage expansion.** Only Claude and Ollama/llama.cpp.
   Missing: OpenAI, Gemini, MiniMax, Kimi K2 (evaluation plan's R7
   fast-follow scope).
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

## GEPA optimizer: shipped but not yet run for real

`scripts/optimize_skill.py --skill <name> [--iterations N]` is implemented
and covered by 166 passing tests (all against stubbed `gepa`/provider
calls) but has never been run against a real installed skill, real session
history, or the real `gepa` PyPI package. Before trusting its output:

```bash
pip install -e ".[optimizer]"
SKILL_EVOLUTION_OPTIMIZER_ENABLED=true python3 scripts/optimize_skill.py --list-candidates
SKILL_EVOLUTION_OPTIMIZER_ENABLED=true python3 scripts/optimize_skill.py --skill <name>
```

Watch for: the `gepa` package's actual `reflection_lm`/config parameter
names drifting from what was verified against `gepa==0.1.4` (KTD4 in the
plan flags this as version-sensitive), and whether the seed-session cap or
iteration budget defaults feel right in practice.
