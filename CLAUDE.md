# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Hermes Agent skill (`hermes-skill-evolution`) that gives a Hermes Agent a self-improvement feedback loop: it reads the agent's past session transcripts from `~/.hermes/state.db`, compares them against the agent's installed skills, generates structured markdown proposals (`improve_existing`, `create_new`, `merge_skills`, `deprecate_skill`) for a human to review, and — if opted in — auto-applies high-confidence proposals via Hermes's `skill_manage` tool, gated on an evaluation framework that scores proposal content before anything mutates.

There is no daemon and no server component. The "pipeline" is a set of standalone Python stdlib scripts that a Hermes cron job invokes and pipes together; the actual analysis step is an LLM call made by the Hermes agent itself (not by any script here), using `docs/session-analyzer-prompt.md` as its instructions. The evaluation gate (`scripts/evaluate.py`), by contrast, *is* implemented in this repo — it's not delegated to the cron agent.

## Git tracking

`.gitignore` used to use unanchored patterns (`scripts/`, `docs/`, `proposals/`, `reports/`) that matched those directory names anywhere in the tree, silently excluding all real pipeline code from version control while `git status` read clean. This was fixed in `41d188e` (anchored to `/scripts/`, `/docs/`, etc.) and `git ls-files` now correctly shows the full implementation: `scripts/*.py`, `docs/`, and `tests/`. If a file under one of those directories seems to have no history, it's more likely genuinely new than re-ignored — but `git status --ignored` is still the way to check.

Two empty top-level directories, `proposals/` and `reports/`, exist on disk and are tracked, but nothing in `scripts/` writes to them — `proposal.py`'s `get_proposals_dir()` defaults to `docs/skill-evolution/proposals/` (overridable via `SKILL_EVOLUTION_PROPOSALS_DIR`), and `evaluate.py`'s history file defaults to `docs/skill-evolution/eval_history.jsonl`. Confirm with the user before assuming which directory a given deployment actually writes to, or before repurposing/removing the top-level placeholders.

## Commands

```bash
# Run the pipeline manually, end to end (mirrors what the cron job does)
python3 scripts/fetch_sessions.py --dry-run              # preview sessions without marking them processed
python3 scripts/fetch_sessions.py | python3 scripts/analyze.py   # NDJSON sessions -> LLM-ready prompt text
python3 scripts/skill_index.py                            # scan ~/.hermes/skills/ -> JSON index

# Proposal inspection/scaffolding
python3 scripts/proposal.py --example     # print an example proposal
python3 scripts/proposal.py --list        # list current proposals
python3 scripts/proposal.py --show <id>   # show one proposal by id

# Evaluation gate
python3 scripts/evaluate.py --list-evaluators             # show the resolved evaluator set
python3 scripts/evaluate.py --retroactive --dry-run       # preview batch re-evaluation of saved proposals
python3 scripts/evaluate.py --retroactive                 # re-run evaluation against saved proposals, append history
python3 scripts/evaluate.py --prune                       # prune eval history per SKILL_EVOLUTION_HISTORY_RETENTION

# Optional GEPA optimizer (needs `pip install -e ".[optimizer]"`)
SKILL_EVOLUTION_OPTIMIZER_ENABLED=true python3 scripts/optimize_skill.py --list-candidates      # read-only report of low-scoring targets
SKILL_EVOLUTION_OPTIMIZER_ENABLED=true python3 scripts/optimize_skill.py --skill <name> [--iterations N]   # run GEPA for one skill

# Tests
pip install -e ".[dev]"
pytest
```

No build step and no non-stdlib runtime dependencies for the core pipeline. `gepa` (the standalone PyPI package, not `dspy`) is an optional extra (`pip install -e ".[optimizer]"`) required only by `scripts/optimize_skill.py`; nothing else imports it.

## Architecture

```
fetch_sessions.py ──NDJSON──► analyze.py ──formatted prompt──┐
skill_index.py ──JSON index──────────────────────────────────┼──► [Hermes cron agent + session-analyzer-prompt.md]
                                                               │
                                                               ▼
                                                proposal.py (write proposal .md files, status: proposed)
                                                               │
                                                     human review (status: approved)
                                                               │
                                                  apply_proposal() ──► evaluate.evaluate_and_record()
                                                               │            (deterministic + llm_judge + regression,
                                                               │             appends to eval_history.jsonl)
                                                               │
                                                        gate passed? ──No──► can_apply: False, proposal stays proposed
                                                               │
                                                              Yes
                                                               │
                                                               ▼
                                                     skill_manage (Hermes tool)

optimize_skill.py (optional, SKILL_EVOLUTION_OPTIMIZER_ENABLED=true)
  --list-candidates: reads eval_history.jsonl, prints low-scoring targets (read-only, no proposal written)
  --skill <name>: fetch_sessions.sessions_for_skill() ──► gepa.optimize_anything() ──► drafts improve_existing
                  proposal from the winning candidate via proposal.save_proposal()
  (never calls apply_proposal() itself — optimizer-originated proposals go through the same gate as any other)
```

- **`scripts/fetch_sessions.py`** — reads `~/.hermes/state.db` (SQLite; `sessions`/`messages` tables), filters out already-processed sessions (via `state.py`'s state file) and anything older than `--lookback-hours`, redacts messages matching `SECRET_PATTERNS` (API key/token prefixes) via `contains_secret()`, truncates long messages, and emits one JSON object per session to stdout. Marks sessions processed as a side effect unless `--dry-run`.
- **`scripts/state.py`** — small JSON-file-backed tracker (`~/.hermes/skill_evolution_state.json`) for which session IDs have already been analyzed. `fetch_sessions.py` duplicates this logic inline rather than importing it — if you change one, check the other.
- **`scripts/skill_index.py`** — scans `~/.hermes/skills/<category>/<skill>/SKILL.md`, hand-parses the YAML frontmatter (no PyYAML dependency — stdlib only) via `parse_name_description_frontmatter()` for `name`/`description`, and emits a JSON index the analyzer prompt uses as "what skills currently exist." `evaluate.py`'s `DeterministicEvaluator` reuses this same parser to validate a proposal's frontmatter.
- **`scripts/analyze.py`** — pure formatter: NDJSON sessions on stdin → markdown-ish text on stdout for LLM consumption. No analysis logic lives here; the actual judgment is made by whatever LLM/agent reads this text plus `docs/session-analyzer-prompt.md`.
- **`scripts/proposal.py`** — the schema and I/O layer:
  - `SkillEvolutionProposal` dataclass + `ProposalType`/`ProposalStatus` enums define the proposal shape.
  - `render()`/`load_proposal()` do round-trip markdown+YAML-frontmatter serialization using a **hand-rolled frontmatter parser** (`_parse_frontmatter`, no PyYAML) — it's whitespace-sensitive (2-space list items, 4-space nested change blocks). Be careful preserving exact indentation if you edit proposal rendering.
  - `get_proposals_dir()` defaults to `docs/skill-evolution/proposals/` (overridable via `SKILL_EVOLUTION_PROPOSALS_DIR`) — see "Git tracking" above.
  - `save_proposal()` pipes the rendered markdown through `evaluate.redact_secrets()` before writing to disk, so a proposal can never persist a secret that leaked into session evidence or a proposed change.
  - `apply_proposal()` validates `status == proposed` and `confidence >= min_confidence`, then runs the evaluation gate (`evaluate.evaluate_and_record()`) *before* mutating anything. If evaluation raises or the gate fails, it returns `can_apply: False` (with `evaluation_results` and, on a raised exception, `evaluation_error`) and leaves the proposal's status untouched. Only once the gate passes does it build the `skill_manage` instruction dict, flip `status` to `applied`, and rewrite the file. It does not call `skill_manage` itself — this script has no Hermes runtime dependency.
  - `proposal.py` imports `evaluate` at module scope; `evaluate.py` avoids importing `proposal` at module scope (it imports it locally inside `_select_retroactive_proposals`) specifically to break that cycle — keep that direction if you touch either file's imports.
- **`scripts/evaluate.py`** — the evaluation/gating framework (see "Evaluation framework" below).
- **`scripts/optimize_skill.py`** — optional GEPA optimizer, gated behind `SKILL_EVOLUTION_OPTIMIZER_ENABLED=true` and the optional standalone `gepa` PyPI package (not `dspy`). `--list-candidates` prints `find_low_scoring_targets()`'s entries (history below `DEFAULT_LOW_SCORE_THRESHOLD` 0.6) read-only, with no optimization run. `--skill <name> [--iterations N]` runs `run_gepa_optimization()`: resolves the skill's installed `SKILL.md` via `skill_index.scan_skills()` as the seed candidate, fetches that skill's session history via `fetch_sessions.sessions_for_skill()` (capped at `DEFAULT_MAX_SESSIONS_FOR_SKILL`, most recent first; exits cleanly below `MIN_SESSIONS` without importing `gepa` at all), then calls `gepa.optimize_anything()` in Single-Task Search mode, scored by `score_candidate()` (built on `evaluate.py`'s provider/redaction layer, reusing `LLMJudgeEvaluator.parse_and_score()`) with `gepa`'s own reflective-mutation step also routed through that same provider layer via `_reflection_lm_adapter()` rather than `gepa`'s litellm/OpenAI default. `draft_proposal_from_gepa_result()` builds an `improve_existing` proposal from the winning candidate, with a rationale reporting the full explored-candidate frontier. It never calls `apply_proposal()` — optimizer-drafted proposals re-enter the same human-review/auto-apply gate as any analyst-drafted one.
- **`docs/session-analyzer-prompt.md`** — the actual instructions given to the LLM cron agent: analysis criteria (skill coverage, cross-session patterns, specificity, confidence scoring 0.5–1.0), output format, and rules (skip `source="cron"` sessions, read-only `skill_manage` inspection before proposing, auto-apply gate). Treat this as the "business logic" of the analysis half of the system — the Python scripts are plumbing around it. The evaluation half (`evaluate.py`) is separate: it scores whatever content a proposal already contains, regardless of which agent or prompt produced it.
- **`docs/plans/2026-07-23-001-feat-skill-evolution-evaluation-plan.md`** — the requirements doc (R1–R22) that `evaluate.py` was built against. Useful as a spec reference for *why* the module is shaped the way it is (e.g. R21 "fail closed" explains the `_run_one`/`_fail` pattern, R20 explains the LLM-judge's random-boundary prompt framing, R11/R13 explain the append-only history).
- **`docs/integration-plan.md`** — an earlier (partly Spanish) draft plan proposing a `ConstraintValidator`/`constraints.py` and an `LLMJudge`/`fitness.py` ported from an external repo. That plan shipped, but under different names than it originally proposed: `evaluate.py`'s `DeterministicEvaluator` *is* the constraint validator (size/growth/YAML checks), and `LLMJudgeEvaluator` *is* the fitness judge (rubric-based scoring) — there's no separate `constraints.py`/`fitness.py` file. The doc's Phase 3 (GEPA optimizer) is `optimize_skill.py`, which now runs a real `gepa.optimize_anything()` loop (see above) rather than the placeholder described in earlier notes. Its Phase 2 (`dataset_builder.py`/`skill_quality.py` — synthetic eval datasets and a periodic quality tracker/report) has not been built; `optimize_skill.py`'s trainset instead comes directly from real recorded sessions.

### Evaluation framework (`scripts/evaluate.py`)

Introduced across commits `cc51860`..`0e7465e` (tagged `U1`–`U11` in commit messages) to gate auto-apply on measured quality rather than just the proposal's self-reported `confidence`.

- **`EvalResult`** (`score`, `feedback`, `passed`, `evaluator_name`) is the shape every evaluator returns. **`Evaluator`** is the ABC; concrete evaluators register themselves into the module-level `REGISTRY` dict via `register_evaluator()` at import time — there's no dynamic discovery/plugin scanning.
- **Which evaluators run** is controlled by `SKILL_EVOLUTION_EVALUATORS` (comma-separated; default `"deterministic,llm_judge,regression"`, resolved by `get_enabled_evaluators()`). An unknown name raises `ValueError` rather than silently skipping.
- **Three v1 evaluators**, all registered at import:
  - `DeterministicEvaluator` — binary size (`SKILL_EVOLUTION_MAX_SKILL_SIZE_KB`, default 15KB), growth-vs-baseline (`SKILL_EVOLUTION_MAX_GROWTH_PCT`, default 20%), and YAML-frontmatter-structure checks. The frontmatter check only runs when `context["content_kind"] == "body"` — a bare description change or a merge/deprecate summary+rationale fallback is plain prose and is exempt.
  - `LLMJudgeEvaluator` — rubric-based (`correctness`, `procedure_following`, `conciseness`, each 0–1, threshold `SKILL_EVOLUTION_LLM_JUDGE_THRESHOLD` default 0.7). Frames evaluated content as untrusted data between a **per-call random hex boundary** (not a static tag) specifically so injected content can't forge the closing marker and escape the block; fails closed (`self._fail(...)`) on any provider error or malformed/out-of-range JSON response rather than guessing.
  - `RegressionEvaluator` — compares a target's new aggregate score against the *last entry that actually passed* in that target's history (not just the last entry) so a failed attempt can never lower the bar a later one is compared against. No prior passing entry → auto-passes ("no baseline yet").
- **Gate combination**: `run_evaluators()` runs everything except `regression` first, then feeds `regression` the mean of those scores as `new_score` in its context. `combine_gate(results, strictness)` then combines per `SKILL_EVOLUTION_GATE_STRICTNESS` (or a per-proposal-type override, `SKILL_EVOLUTION_GATE_STRICTNESS_<TYPE>`, resolved by `resolve_gate_strictness()`): `"strict"` (default) requires every evaluator to pass; `"majority"` requires more than half. Zero configured evaluators never blocks.
- **Provider adapter layer** (`call_provider()`) is stdlib-only `urllib` HTTP to Claude (`ANTHROPIC_API_KEY` + `SKILL_EVOLUTION_CLAUDE_MODEL`) or Ollama/llama.cpp (`SKILL_EVOLUTION_OLLAMA_BASE_URL`/`SKILL_EVOLUTION_OLLAMA_MODEL`), selected by `SKILL_EVOLUTION_PROVIDER` (default `claude`) with an optional per-evaluator override `SKILL_EVOLUTION_<EVALUATOR_NAME>_PROVIDER`. Every prompt is run through `redact_secrets()` (reusing `fetch_sessions.py`'s `contains_secret()`/`SECRET_PATTERNS`, whole-line replacement) before it ever leaves the machine, independent of the redaction `save_proposal()` already does before writing to disk — R18/R19 in the plan doc treat "sent to a provider" and "written to disk" as two separate redaction points, not one.
- **History**: `append_history()`/`read_history()` read/write one shared append-only JSONL file (`get_history_path()`, default `docs/skill-evolution/eval_history.jsonl`, overridable via `SKILL_EVOLUTION_HISTORY_PATH`), keyed per-target (`target_key_for_proposal()` → `"skill:<name>"` or `"proposal:<id>"` fallback). `prune_history()` (invoked via `--prune`) enforces `SKILL_EVOLUTION_HISTORY_RETENTION` (a bare int = max versions, `"90d"`/`"6mo"` = age-based) while always keeping the most recent entry per target regardless of the limit.
- **`evaluate_and_record()`** is the single call site both `apply_proposal()` and the retroactive path share — it runs `evaluate_skill_text()`, combines the gate, and appends one combined `EvalResult` (`evaluator_name="gate"`) to history, so both paths produce identical history semantics.
- **Retroactive/batch mode** (`--retroactive`, `retroactive_reevaluate()`) only re-evaluates already-saved proposals with `status == proposed` by default (`_select_retroactive_proposals`) — rejected/applied proposals are excluded because they're not live decisions and re-scoring them would inject entries into the same history stream the live gate regresses against; pass `include_all_statuses=True` (Python-only, no CLI flag yet) to opt in. `--target`/`--since` filter further.
- **Module-identity gotcha**: running `python3 scripts/evaluate.py` directly loads it as `__main__`; since `proposal.py` does `import evaluate` at module scope, the file registers itself under `sys.modules["evaluate"]` at the top (`if __name__ == "__main__": sys.modules.setdefault(...)`) so both names resolve to the *same* module instance (same `REGISTRY`, same classes) instead of `proposal.py` re-executing and re-registering everything as a second, distinct module.

### Proposal types and their `skill_manage` action mapping

| `type` | What it means | `apply_proposal()` emits |
|---|---|---|
| `improve_existing` | Patch a skill's description/body | one `patch` instruction per `proposed_changes` entry |
| `create_new` | New skill for a recurring uncovered pattern | one `create` instruction (name/description/category pulled from `proposed_changes`) |
| `merge_skills` | Combine overlapping skills into `target_skill` | one `delete` instruction per `source_*`-prefixed change, each tagged `absorbed_into` |
| `deprecate_skill` | Remove a stale/unused skill | one `delete` instruction |

All four types pass through the same evaluation gate (above) before any instructions are emitted — the gate runs once per `apply_proposal()` call regardless of `type`.

## Conventions specific to this codebase

- Every script is a standalone stdlib-only CLI with its own `argparse` in `main()`, importable for its core function (`fetch_sessions()`, `scan_skills()`, `apply_proposal()`, `run_evaluators()`, etc.) — keep new scripts consistent with this pattern rather than introducing a shared framework/package layout.
- No PyYAML anywhere by design (`SKILL.md` promises "no pip packages required") — frontmatter is parsed and rendered by hand in both `skill_index.py` and `proposal.py`. If a change needs richer YAML (nested structures beyond flat keys + one-level lists), it likely needs a matching parser update in `_parse_frontmatter`, not just a `render()` change.
- Auto-apply is opt-in via env vars (`SKILL_EVOLUTION_AUTO_APPLY`, `SKILL_EVOLUTION_MIN_CONFIDENCE`), read by the cron agent/prompt, not by the Python scripts themselves. The evaluation gate inside `apply_proposal()`, by contrast, always runs (it's not behind an opt-in flag) — `SKILL_EVOLUTION_EVALUATORS`/`SKILL_EVOLUTION_GATE_STRICTNESS` only change *which* evaluators run and how strictly, not whether the gate runs at all.
- `gepa` is imported lazily (`_require_gepa()` inside `optimize_skill.py`, called from `run_gepa_optimization()`, not at module import time) so the rest of the pipeline works with the dependency absent — don't move that import to module scope.
- Tests exist now (`tests/test_*.py`, one file per evaluator/feature area, run via `pytest`) — this replaces an earlier state where `pyproject.toml` configured `pytest` but no `tests/` directory existed. New evaluators or gate behavior should get a matching `tests/test_evaluate_*.py`.
