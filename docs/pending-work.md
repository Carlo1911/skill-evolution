# Pending Work

Status snapshot as of 2026-07-25, after the real GEPA optimizer (see
`docs/plans/2026-07-24-001-feat-real-gepa-optimizer-plan.md`) shipped to
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

## Discovered while building GEPA, not fixed

Found during implementation and code review of the GEPA optimizer; explicitly
left out of that work's scope rather than silently expanded into.

- **`fetch_sessions()` still queries the wrong columns.** `scripts/fetch_sessions.py`'s
  `SESSION_QUERY`/`MESSAGE_QUERY` select `created_at`/`total_tokens`, but the
  live `~/.hermes/state.db` schema has `sessions.started_at` (no
  `created_at`, no `total_tokens` at all) and `messages.timestamp` (no
  `created_at`). Verified directly against the real database. This means the
  base cron pipeline (`fetch_sessions.py` → `analyze.py` → the cron agent)
  likely cannot run successfully against production data today. GEPA's own
  session query (`sessions_for_skill()`) was written against the correct
  live columns from the start and doesn't share this bug — only the
  original `fetch_sessions()` function does. A related crash
  (`sqlite3.Row` has no `.get()`) in the same function *was* fixed as a
  side effect of a code-review-driven refactor; the column-name mismatch
  is a separate, larger fix and was left alone.
- **GEPA's reflection-mutation prompt has no injection-defense framing.**
  `scripts/optimize_skill.py`'s `_reflection_lm_adapter()` forwards whatever
  prompt the `gepa` package builds internally straight to
  `evaluate.call_provider()`, with no boundary/untrusted-content wrapping —
  unlike every other LLM-judge prompt in this codebase. There's no clean
  point to add that framing without rewriting `gepa`'s own internal prompt
  construction. Mitigated today by the existing evaluation gate and human
  review before any proposal is applied; accepted as a residual risk, not
  fixed at the source.
- **Optimizer defaults are unvalidated against real usage.** `MIN_SESSIONS`
  (3), `DEFAULT_MAX_METRIC_CALLS` (20), and `DEFAULT_MAX_SESSIONS_FOR_SKILL`
  (20) in `scripts/optimize_skill.py`/`scripts/fetch_sessions.py` are
  judgment calls made during planning, not tuned against an actual run.

## GEPA optimizer: shipped but not yet run for real

`scripts/optimize_skill.py --skill <name> [--iterations N]` is implemented
and covered by 148 passing tests (all against stubbed `gepa`/provider
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
