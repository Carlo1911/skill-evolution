# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A host-agnostic agent skill (`skill-evolution`) that gives the host agent a self-improvement loop: it reads past session transcripts through the active `HostAdapter` (default Hermes → `~/.hermes/state.db`; Claude Code → `~/.claude/projects/*/*.jsonl`), compares them against installed skills, writes markdown proposals (`improve_existing`, `create_new`, `merge_skills`, `deprecate_skill`) for human review, and — if opted in — auto-applies them through the host's mutation channel, gated by an evaluation framework that scores proposal content before anything mutates.

No daemon, no server. The pipeline is standalone stdlib Python scripts piped together by a scheduled job. The analysis step is an LLM call made by the host agent following an analyzer prompt maintained **outside this repo** (see SKILL.md, "Scheduled Runs"). The evaluation gate (`scripts/evaluate.py`) *is* implemented here.

## Git tracking

`proposals/`, `reports/`, `labels/` and `eval_history.jsonl` are gitignored: they hold output derived from real transcripts, which shape-based redaction can't fully clean. `.gitignore` patterns are anchored — unanchored ones once silently excluded real pipeline code.

Runtime path defaults (all at repo root): `./proposals/` (`SKILL_EVOLUTION_PROPOSALS_DIR`), `./eval_history.jsonl` (`SKILL_EVOLUTION_HISTORY_PATH`), `./reports/` (`SKILL_EVOLUTION_QUALITY_REPORT_DIR`), `labels/review_labels.jsonl` (`SKILL_EVOLUTION_LABELS_PATH`).

## Commands

```bash
# Pipeline (mirrors the cron job)
python3 scripts/fetch_sessions.py --dry-run                    # preview, don't mark processed
python3 scripts/fetch_sessions.py | python3 scripts/analyze.py # NDJSON -> LLM-ready prompt
python3 scripts/skill_index.py                                 # skills -> JSON index
python3 scripts/fetch_sessions.py --prune-state                # prune state (reports on stderr)

# Proposals
python3 scripts/proposal.py --list | --example | --show <id>
python3 scripts/proposal.py --validate <file|id>...            # exit 1 unless all valid
python3 scripts/proposal_diagnostics.py                        # read-only queue diagnostic (--all, --format json)
python3 scripts/review_labels.py --next                        # label pending proposals (TTY); --stats, --export

# Evaluation gate
python3 scripts/evaluate.py --list-evaluators
python3 scripts/evaluate.py --retroactive [--dry-run]
python3 scripts/evaluate.py --prune
python3 scripts/evaluate.py --backfill-transport-failures [--apply]
SKILL_EVOLUTION_EVALUATORS=llm_judge,regression python3 scripts/evaluate.py --eval-target proposal --proposal-id <id>
#   other targets: tool_calls --session-id <id>; analyzer_prompt --session-id <id> --proposal-id <id>

# Skill quality report
python3 scripts/skill_quality.py [--skill <name>] [--below 0.7] [--format json|markdown]

# Optional GEPA optimizer (needs `pip install -e ".[optimizer]"`, use .venv/bin/python)
SKILL_EVOLUTION_OPTIMIZER_ENABLED=true .venv/bin/python scripts/optimize_skill.py --list-candidates [--target all]
SKILL_EVOLUTION_OPTIMIZER_ENABLED=true .venv/bin/python scripts/optimize_skill.py --skill <name> [--iterations N]

# Tests
pip install -e ".[dev]"
pytest -q -rs
```

The core pipeline is stdlib-only (any `python3`). `gepa` (PyPI, not `dspy`) is needed only by `optimize_skill.py`; `fastembed` only by the opt-in `embedding_similarity` evaluator. Pass/skip counts depend on which extras are installed — re-run `pytest -q -rs` instead of trusting a number written here.

## Architecture

```
fetch_sessions.py ──NDJSON──► analyze.py ──prompt──┐
skill_index.py ──JSON index────────────────────────┼──► [host agent + analyzer prompt]
                                                   ▼
                              proposal.py (writes .md, status: proposed)
                                                   │  human review (approved)
                                                   ▼
                    apply_proposal() ──► evaluate.evaluate_and_record() ──fail──► can_apply: False
                                                   │ pass
                                                   ▼
                              adapter.apply_skill_write(plan)
                              (Hermes: skill_manage instructions; Claude Code: direct file writes)

optimize_skill.py (optional): history/sessions ──► gepa ──► drafts an improve_existing proposal
                              (never applies; goes through the same gate)
```

### Scripts

- **`fetch_sessions.py`** — reads `state.db`, skips processed sessions and anything older than `--lookback-hours`, keeps at most `--max-sessions` / `SKILL_EVOLUTION_MAX_SESSIONS` (default 200, newest first, counted *after* skipping processed ones; overflow stays unmarked for the next run). Redacts, truncates, emits one JSON per session, and marks them processed unless `--dry-run`. Output keys: `started_at` (not `created_at`), no `total_tokens`. `sessions_for_skill()` (optimizer) returns `[]` on an unreadable DB; `fetch_sessions()` (cron) raises on purpose. Both check the DB exists *before* `sqlite3.connect()`, which would otherwise create an empty file.
- **Secrets and PII are handled differently, on purpose.** `contains_secret()` (fixed `SECRET_PATTERNS` + shape-based `SECRET_REGEXES`) is the single chokepoint for both redaction points (provider prompts and on-disk proposals) — err toward over-detection. A secret **drops the whole message**. PII is **masked in place** by `redact_pii()` (`[PII:EMAIL]`, etc.). Monetary amounts are deliberately **not** masked: they identify nobody and are evidence the analyzer needs. Card detection requires a 3–6 leading digit plus Luhn.
- **`skill-evolution-fetch.sh`** — cron wrapper; resolves `fetch_sessions.py` relative to itself, following symlinks. **Nothing but `fetch_sessions.py` may write to stdout** (it's the NDJSON channel).
- **`state.py`** — processed-session tracker (`~/.hermes/skill_evolution_state.json`, override `SKILL_EVOLUTION_STATE_FILE`). `fetch_sessions.py` **duplicates this logic on purpose**; change both (`tests/test_state_schema_compat.py` and `test_state_pruning.py` parametrize over both). Two on-disk shapes must both be read: the documented `{"processed_sessions": [...], ...}` and the deployed flat `{session_id: timestamp}` map. `mark_processed()` keeps whatever layout the file already has — don't "normalize" it. Keys are host-prefixed (`<host>:<id>`); legacy un-prefixed keys belong to `hermes`. `prune_processed()` (`SKILL_EVOLUTION_STATE_RETENTION`) only prunes the flat shape, only the active host's entries, and always keeps `keep_ids` (the batch just written, which all share one timestamp). Retention tighter than `--lookback-hours` causes re-analysis; there's a stderr warning, not a fix. Invalid retention values warn and are treated as unbounded, never raise.
- **`skill_index.py`** — scans skill roots, parsing frontmatter by hand (no PyYAML). Every root is walked as both flat (`<root>/<skill>/SKILL.md`) and nested (`<root>/<category>/<skill>/SKILL.md`). Flat entries that resolve (`realpath`) to an already-indexed file are dropped; two *distinct* files sharing a name both stay so lookups report ambiguity. Dot-prefixed dirs (`.archive/`, `.hub/`, `.curator_backups/`) are skipped. `--extra-root` adds roots; the primary root wins on name clashes.
- **`analyze.py`** — pure formatter (`format_session()`), no analysis logic. Prompt size vs provider context limits is why session caps exist.
- **`proposal.py`** — schema (`SkillEvolutionProposal`, enums) and I/O.
  - Hand-rolled, **whitespace-sensitive** frontmatter parser (2-space list items, 4-space change blocks, 6-space `|`/`|-` literal blocks for multi-line `old_value`/`new_value`). `render()` writes the canonical quoted/`b64:` form. Richer YAML needs a parser change, not just a `render()` change.
  - `list_proposals()` skips unparseable files but names each on stderr; parsing stays strict.
  - `save_proposal()` runs `evaluate.redact_secrets()` before writing, and writes back to the file it loaded from (`source_path`) — the filename isn't necessarily the id. `find_proposal_file()` raises if several files claim one id.
  - `apply_proposal()`: status must be `proposed` or `approved`, `confidence >= min_confidence`; refuses *before* the gate if the host can't write or the proposal isn't a valid mutation (e.g. `create_new` with placeholder body). Runs the gate, then hands a plan to the adapter; flips status to `applied` (and migrates `create_new` history) only after the adapter returns `can_apply: True`.
  - Import direction: `proposal.py` imports `evaluate` at module scope; `evaluate.py` imports `proposal` only locally. Keep it that way.
- **`proposal_diagnostics.py`** — read-only queue diagnostic. One primary state per proposal by precedence (`unreadable` > `duplicate_id` > `incomplete` > `ambiguous` > `archived` > `unresolved` > `stale` > `valid`) plus every applicable reason and possible-duplicate flags. Reuses the gate's own helpers; never calls a provider or writes.
- **`review_labels.py`** — human labels (`useful`, `harmful`, `duplicate`, `incomplete`, `obsolete`, `unsure`) used as ground truth for decision-model pilots. Latest label per proposal wins; rows record the file digest and a fixed `holdout`/`design` split. Never touches the proposal's `status`.
- **`skill_quality.py`** — scores every installed skill with the gate's rubric, records `skill:<name>` history entries (`subject: installed`), writes trend reports. Strips `human_review` from the evaluator list. Costs one judge call per skill — run weekly/monthly.
- **`optimize_skill.py`** — optional GEPA optimizer (see Conventions for import rules).
  - `--list-candidates` only considers entries that scored the **installed** skill, uses the judge's own score vs its threshold when recorded, and drops skills that no longer resolve or whose content digest changed.
  - `--skill` seeds GEPA with the installed `SKILL.md`. Its objective states a byte window that is the **intersection of every size limit the gate applies** (per-pass %, byte floor, absolute cap ratchet, cumulative %). If the window is empty it says why and which side is violated. This is soft guidance; the deterministic gate is still the real check.
  - Judge and reflection LM both go through `evaluate.py`'s provider/redaction layer. Tunables: `SKILL_EVOLUTION_OPTIMIZER_MIN_SESSIONS` (3), `_MAX_METRIC_CALLS` (8 — real calls take 30s+ each), `_MAX_SESSIONS_FOR_SKILL` (20).

### Evaluation framework (`scripts/evaluate.py`)

- **Evaluators** return `EvalResult(score, feedback, passed, evaluator_name)` and register into `REGISTRY` at import. `SKILL_EVOLUTION_EVALUATORS` picks them (default `deterministic,llm_judge,regression`); unknown names raise.
  - `deterministic` — size and structure:
    - Absolute cap `MAX_SKILL_SIZE_KB` (15) is a **ratchet**: it fails only if the candidate is over the cap *and* larger than its baseline. `create_new` has no baseline, so it gets the strict cap.
    - Per-pass limits: growth `MAX_GROWTH_PCT` (20), shrink `MAX_SHRINK_PCT` (15), plus a byte floor `MAX_SHRINK_BYTES` (2048). The ratchet and the byte floor **must stay together**: the floor is what stops big deletions on oversized skills once the ratchet lets them through.
    - Cumulative limits vs the target's original size: `MAX_CUMULATIVE_GROWTH_PCT` (50), `MAX_CUMULATIVE_SHRINK_PCT` (30). They exist because the judge's `conciseness` rewards deletion, so per-pass limits alone compound into erosion.
    - Frontmatter checks run only when `content_kind == "body"`.
  - `llm_judge` — rubric `correctness`/`procedure_following`/`conciseness`, threshold `SKILL_EVOLUTION_LLM_JUDGE_THRESHOLD` (0.7). Content is framed between a per-call random boundary. Any provider error or malformed JSON fails closed. `JUDGE_RUBRIC_VERSION` must be bumped when the prompt changes (`tests/test_judge_provenance.py` hashes it).
  - `regression` — compares against the last **passing** entry for the target, like with like (installed vs installed, candidate vs candidate). No baseline → pass.
  - `human_review` (opt-in) — TTY y/n prompt, fails closed without a TTY, runs last.
  - `embedding_similarity` (opt-in, `fastembed`) — duplicate detection for `create_new`, drift for body changes with an on-disk baseline, grounding of new text against cited sessions. Long texts are windowed (the default model truncates at 512 tokens). Thresholds 0.90 / 0.90 / 0.74 were calibrated on real data. It abstains (`n/a`) when it has nothing to compare.
- **Abstention** (`EvalResult.not_applicable`) is excluded from the gate, means and history scores; it is not a failure. An evaluator that tries and errors still fails closed.
- **Gate**: phases are automatic → regression (fed the automatic mean) → human_review. `SKILL_EVOLUTION_GATE_STRICTNESS` is `strict` (default, all pass) or `majority`, with per-target > per-type > global overrides. `evaluate_and_record()` runs each target in `SKILL_EVOLUTION_GATE_TARGETS` (default `skill,proposal`; also `tool_calls`, `analyzer_prompt`), records one `gate` entry per target, and ANDs the results. It's the single call site shared by `apply_proposal()` and `--retroactive` (which re-scores only pending proposals unless `--all-statuses`).
- **Candidate building**: every change in a proposal is applied in order to the installed skill (`compose_body_changes()`); each anchor must match exactly once. Structural problems (empty `new_value`, several description changes, whole-body replacement mixed with other changes) fail early. **`baseline_size` comes from the installed `SKILL.md`, never from the proposal's `old_value`** (LLM-written, and an inflated value would raise the ratchet). Omitting `baseline_size` silently disables the growth check. An ambiguous target (two distinct files, same name) fails closed.
- **Providers** (`call_provider()`, stdlib `urllib`): `claude`, `ollama`, `lmstudio`, `opencode`, `opencode_go`, `openai`, `gemini`. Select with `SKILL_EVOLUTION_PROVIDER` (comma-separated fallback chain allowed) or `SKILL_EVOLUTION_<EVALUATOR>_PROVIDER`. Each provider reads its own `*_BASE_URL`/`*_MODEL`/API key env vars. Behavior:
  - Permanent errors (401/403, missing key, bad shape) move on to the next provider; transient ones (timeout, 408/429/5xx, connection errors) retry with backoff (`SKILL_EVOLUTION_PROVIDER_RETRIES`=2, `_RETRY_BASE_SECONDS`=1.0) before moving on.
  - Timeout: `SKILL_EVOLUTION_PROVIDER_TIMEOUT` (60s, one knob for all providers — local reasoning models need more).
  - Every prompt goes through `redact_secrets()` before leaving the machine.
  - This machine uses `opencode,lmstudio` (LM Studio with Phi-3.5 Mini).
- **Provider gotchas**:
  - `_post_json()` sends `User-Agent: skill-evolution/0.1`. This is load-bearing: OpenCode's gateway 403s urllib's default UA. If a provider 403s while `curl` works, check the UA first.
  - OpenCode Zen (`/zen/v1`) and Go (`/zen/go/v1`) have different model catalogues. Zen's free models (`big-pickle`, `*-free`) 403 any non-OpenCode client. Go requires the `x-opencode-session` header (sent automatically) and defaults to `deepseek-v4.1-flash`.
- **History** (`eval_history.jsonl`, append-only, keyed `skill:<name>` / `proposal:<id>` / `tool_calls:<sid>` / `analyzer_prompt:<sid>`):
  - Entries carry `content_size`/`baseline_size`, `subject` (`installed` | `candidate`), `content_sha256`, `judge` provenance (provider, model, rubric version, judge score), and `transport_failure` when a provider fault (not a quality failure) caused the verdict.
  - `SKILL_EVOLUTION_HISTORY_RETENTION` enables pruning, which runs automatically after each record. Dropped entries are **archived** (written to the archive before the primary is rewritten). Per target it always keeps the latest entry, the latest passing entry, the earliest entry, and the earliest entries carrying sizes — these are what regression and cumulative-drift checks read.
  - `create_new` history is migrated from `proposal:<id>` to `skill:<name>` after creation.
- **Module identity**: running `python3 scripts/evaluate.py` aliases `__main__` to `sys.modules["evaluate"]` so `proposal.py`'s import doesn't create a second registry.

### Proposal types and the write side

| `type` | Hermes (`applied_by: agent`) | Claude Code (`applied_by: direct`) |
|---|---|---|
| `improve_existing` | one `patch` per change | rewrites `SKILL.md` in place (each `old_value` must match once) |
| `create_new` | one `create` with `name` + full `body` | writes `skills/<name>/SKILL.md` (refuses existing names) |
| `merge_skills` | one `delete` per `source_*` change (`absorbed_into`) | archives sources into `skills/.archive/` |
| `deprecate_skill` | one `delete` | archives into `skills/.archive/` |

All types pass through the same gate first.

## Conventions specific to this codebase

- **Host-agnostic by default.** Multi-host support is an explicit goal. `scripts/host.py` defines the `HostAdapter` interface; `SKILL_EVOLUTION_HOST` (default `hermes`) picks the adapter, and unknown names raise.
  - Read: `iter_sessions`, `iter_skills`, `read_skill_body` (best-effort, returns `None`, never raises), `iter_archived_skills`, `read_sessions(ids)`.
  - Write: `supports_write` + `apply_skill_write(plan)` (fails closed by default).
  - State: `iter_processed`/`mark_processed`/`prune_processed`, each adapter with its own state file.
  - `HermesAdapter` delegates to the existing scripts byte-for-byte. `ClaudeCodeAdapter` (root: `SKILL_EVOLUTION_CLAUDE_CODE_HOME` > `CLAUDE_CONFIG_DIR` > `~/.claude`) reads the flat `skills/*/SKILL.md` tree and JSONL sessions defensively, through the same redaction chokepoint. It writes atomically (permissions preserved) and refuses to write through symlinked skill dirs.
  - New adapters must produce the shared session dict (`session_id`, `started_at`, `title`, `model`, `source`, `message_count`, `user_messages`, `assistant_messages`, `messages`) and skill dict (`name`, `category`, `description`, `path`, `size`).
  - Keep prompt prose host-neutral too ("this agent skill", not "this Hermes skill").
- Every script is a standalone stdlib CLI with `argparse` in `main()` and an importable core function. No shared framework. No PyYAML.
- Auto-apply is opt-in (`SKILL_EVOLUTION_AUTO_APPLY`, `SKILL_EVOLUTION_MIN_CONFIDENCE`), read by the cron agent, not the scripts. The evaluation gate inside `apply_proposal()` always runs.
- `gepa` is imported lazily via `_require_gepa()` — don't move it to module scope. It returns the `gepa.optimize_anything` **submodule** (which holds the function and the config classes), not the top-level package.
- **Stubbed tests can't catch API drift or wiring gaps.** Pair stubbed tests with contract tests against the real dependency (`tests/test_optimize_skill_gepa_contract.py`, `importorskip`), and assert wiring end-to-end across module seams, not each side in isolation.
- **Test isolation is structural** (`tests/conftest.py`): every `SKILL_EVOLUTION_*` var and `CLAUDE_CONFIG_DIR` is cleared per test and all runtime paths point at temp dirs. Real state, history, proposals, reports and skill trees are fingerprinted, and the run fails if any change (a test once wiped the real state file). A test checking a *default* path must `delenv` the variable itself. `tests/host_sandbox.py` builds per-test host trees; parametrize over `HOSTS` for behavior shared by both hosts. New evaluators or gate behavior get a matching `tests/test_evaluate_*.py`.
