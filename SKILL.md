---
name: hermes-skill-evolution
description: "Autonomous skill improvement — analyze Hermes sessions, generate structured improvement proposals, and optionally auto-apply high-confidence changes to your skills"
author: Nous Research Community
version: 1.0.0
platforms: [macos, linux]
metadata:
  hermes:
    tags: [self-improvement, skills, automation, pipeline, proposals, maintenance]
    category: automation
    related_skills: [code-review-and-quality, using-agent-skills, cronjob-management]
---

# Hermes Skill Evolution

A **self-improvement feedback loop** for Hermes Agent skills. Reads your past sessions, analyzes them against your loaded skills, and generates structured proposals to make your skills better over time.

No daemons. No external services. No GPU. Just your Hermes agent, a cron job, and a few Python scripts.

## How It Works

```
fetch_sessions.py ──► NDJSON session data
       │
       ▼ (injected into cron agent's prompt)
skill_index.py + session-analyzer-prompt.md ──► LLM analysis
       │
       ▼
Proposal files (markdown) ──► review ──► auto-apply (opt-in)
```

Each proposal is a structured markdown file with YAML frontmatter:

```yaml
proposal_id: 20260723-001
type: improve_existing
target_skill: debugging-and-error-recovery
confidence: 0.85
status: proposed
proposed_changes:
  - field: body
    description: Add FastAPI exception-handler patterns section
```

## Quick Start

### 1. Install the Skill

```bash
# Replace <owner> with the account hosting your fork; this repo has no published remote yet.
hermes skills install https://raw.githubusercontent.com/<owner>/hermes-skill-evolution/main/SKILL.md
```

### 2. Load and Run

```bash
# Load the skill
hermes -s hermes-skill-evolution

# Then ask:
"Run skill evolution analysis on my recent sessions"
```

The agent will:
1. Fetch recent unprocessed sessions from `~/.hermes/state.db`
2. Scan your installed skills
3. Analyze each session for skill coverage gaps
4. Generate proposal files in `docs/skill-evolution/proposals/`
5. Deliver a summary

### 3. Review Proposals

```bash
ls docs/skill-evolution/proposals/          # override with SKILL_EVOLUTION_PROPOSALS_DIR
cat docs/skill-evolution/proposals/<id>.md
```

Each proposal is a markdown file with a structured YAML frontmatter. Read it, decide, change `status: approved` if you want.

### 4. Auto-Apply (Opt-In)

By default, proposals are **review-only**. Enable auto-apply:

```bash
# Set in your .env or session
export SKILL_EVOLUTION_AUTO_APPLY=true
export SKILL_EVOLUTION_MIN_CONFIDENCE=0.85
```

Note what this does **not** do: the analysis session never applies anything itself — the
analyzer prompt forbids calling `skill_manage`, whatever the confidence. `AUTO_APPLY` and
`MIN_CONFIDENCE` are inputs to `apply_proposal()`, which a human (or a step you write) runs
afterwards; the gate then re-scores the proposal and only produces `skill_manage`
instructions if it passes. Nothing in this repo invokes that step for you.

## Scheduled Runs (Cron)

```bash
# In a session with the skill loaded, ask:
"Create a daily cron job for skill evolution, running at 2 AM, delivering results to my home channel"
```

Or set it up manually:

```bash
hermes cron create "0 2 * * *" \
  --name skill-evolution-analysis \
  --skill hermes-skill-evolution \
  --deliver all \
  --prompt "Run the full skill evolution pipeline."
```

## What Gets Analyzed

| Aspect | What the LLM looks for |
|--------|------------------------|
| **Skill coverage** | Was there a skill for this task? Was it helpful? |
| **Repeated patterns** | Same task appears 3+ times without a dedicated skill |
| **Skill overlap** | Two skills covering the same thing with contradictions |
| **Skill staleness** | A skill exists but is never referenced |
| **Improvement opportunities** | A skill was loaded but didn't help — what was missing? |

## Proposal Types

| Type | Description | `apply_proposal()` emits |
|------|-------------|--------------------------|
| `improve_existing` | Patch a skill's description or body | one `patch` instruction per change |
| `create_new` | Create a skill for a recurring pattern | one `create` instruction |
| `merge_skills` | Combine overlapping skills | one `delete` per source, tagged `absorbed_into` |
| `deprecate_skill` | Remove a stale skill | one `delete` instruction |

All four pass through the same evaluation gate before any instruction is emitted, and the
instructions are for a human or agent to execute — `apply_proposal()` never calls
`skill_manage` itself.

## Constraints & Guardrails

Every auto-applied change must pass an evaluation gate (`scripts/evaluate.py`) before `apply_proposal()` calls `skill_manage`:

- **Size limit**: Skills ≤ 15KB (`SKILL_EVOLUTION_MAX_SKILL_SIZE_KB`), applied as a **ratchet**: a skill already over the limit may still be replaced by a body *no larger than itself*, so an oversized skill can be improved but never made worse. A new skill (no baseline) faces the limit strictly and is never created over it. This makes oversized skills improvable, not downsizable — a very large skill still needs a human to split it
- **Growth limit**: No more than 20% larger than baseline (`SKILL_EVOLUTION_MAX_GROWTH_PCT`)
- **Structure**: Valid YAML frontmatter with `name:` and `description:`
- **Shrink floor**: no more than 15% smaller than baseline (`SKILL_EVOLUTION_MAX_SHRINK_PCT`) — tighter than the growth cap on purpose, because the judge's conciseness criterion rewards deletion and a real optimizer run scored *higher* after cutting 70% of a skill
- **Absolute deletion floor**: no more than 2048 bytes removed in a single pass (`SKILL_EVOLUTION_MAX_SHRINK_BYTES`; the stricter of this and the percentage floor applies, `0` disables). A percentage scales with the skill, so it is weakest where a deletion does most damage — 15% of a small skill is a paragraph, 15% of a 100KB one is several sections
- **Baseline from disk**: size comparisons for a body change measure against the *installed* `SKILL.md`, not against the `old_value` the proposal reports, since that field is written by the analysing model
- **Cumulative drift**: total change measured against where the skill *started*, not the body it replaces — 50% growth / 30% shrink (`SKILL_EVOLUTION_MAX_CUMULATIVE_GROWTH_PCT` / `_SHRINK_PCT`). Per-pass limits reset their reference each run and therefore compound; this bounds the total
- **Rubric score**: An LLM judge scores correctness, procedure-following, and conciseness against a configurable threshold (`SKILL_EVOLUTION_LLM_JUDGE_THRESHOLD`, default 0.7)
- **No regression**: The new score must not fall below the target's last passing score in its evaluation history

By default every configured evaluator must pass (`SKILL_EVOLUTION_GATE_STRICTNESS=strict`); set it to `majority` to require only more than half. Any evaluator or provider failure is treated as a failing result — the gate fails closed, never open. Every evaluation run (live or `--retroactive`) appends to a versioned history file rather than overwriting the previous result.

## Files

This skill ships with ready-to-use scripts. On first load, the agent checks if they exist and creates them if needed.

| Script | Purpose |
|--------|---------|
| `scripts/fetch_sessions.py` | Read `~/.hermes/state.db` → NDJSON session data |
| `scripts/skill_index.py` | Scan `~/.hermes/skills/` → structured JSON index |
| `scripts/analyze.py` | Format NDJSON sessions for LLM consumption |
| `scripts/proposal.py` | Proposal schema, I/O, and `apply_proposal()` logic |
| `scripts/evaluate.py` | Evaluation gate: deterministic + LLM-judge + regression evaluators, versioned history |
| `scripts/optimize_skill.py` | Optional GEPA optimizer — runs a real `gepa.optimize_anything()` loop over a skill's own session history (needs the `gepa` extra) |
| `scripts/state.py` | Track processed sessions in JSON state file |
| `scripts/skill-evolution-fetch.sh` | Cron wrapper (sets CWD, calls fetch_sessions) |

## Dependencies

- Python 3.10+
- Hermes Agent (any version with `state.db`, `cronjob`, `skill_manage`, `write_file`)

No pip packages required beyond Python stdlib for the core pipeline. `gepa==0.1.4` (the standalone PyPI package, **not** `dspy`) is an optional extra (`pip install -e ".[optimizer]"`) needed only for `scripts/optimize_skill.py`. Install it into the interpreter you will run that script with; the rest of the pipeline runs under any `python3`.

## Evaluation Targets

The evaluation framework scores four independent targets, each writing to the same history file with its own namespace:

| Target | Function | History key | What it scores |
|--------|----------|-------------|----------------|
| **skill text** (v1) | `evaluate_skill_text()` | `skill:<name>` | The body/description change in a proposal — size, growth, frontmatter structure, rubric quality, regression vs prior passes |
| **proposal quality** | `evaluate_proposal()` | `proposal:<uuid>` | The proposal as a document — summary clarity, rationale groundedness, change coherence |
| **tool-call quality** | `evaluate_tool_calls()` | `tool_calls:<session_id>` | Tool selection, sequencing, and result extraction from the session that produced the proposal |
| **analyzer prompt quality** | `evaluate_analyzer_prompt()` | `analyzer_prompt:<session_id>` | Whether the analyzer's generation step produced a proposal grounded in the sessions it was given |

The v1 auto-apply gate (`apply_proposal()`) is wired only for **skill text**. The other three targets are **observability-first**: they write to history and can be inspected via `--list-candidates` or `--eval-target`, but do not gate auto-apply. Wiring them into the gate is a separate decision.

The `deterministic` evaluator (size/growth checks) is meaningful only for skill text. For the other three targets, set `SKILL_EVOLUTION_EVALUATORS=llm_judge,regression` to skip it.

## Command Reference

Every script is a standalone CLI; none of them requires the skill to be loaded in a session.

```bash
# --- Pipeline ---
python3 scripts/fetch_sessions.py --dry-run        # preview sessions without marking them processed
python3 scripts/fetch_sessions.py | python3 scripts/analyze.py   # NDJSON -> LLM-ready prompt text
python3 scripts/skill_index.py                     # scan installed skills -> JSON index
python3 scripts/fetch_sessions.py --prune-state    # prune processed-session state (reports on stderr)

# --- Proposals ---
python3 scripts/proposal.py --example              # print an example proposal
python3 scripts/proposal.py --list                 # list current proposals
python3 scripts/proposal.py --show <id>            # show one proposal

# --- Evaluation gate ---
python3 scripts/evaluate.py --list-evaluators      # show the resolved evaluator set
python3 scripts/evaluate.py --retroactive --dry-run    # preview re-evaluation of saved proposals
python3 scripts/evaluate.py --retroactive              # re-evaluate and append history
python3 scripts/evaluate.py --prune                    # prune history per the retention var

# --- The three observability-only targets (never gate auto-apply) ---
# `deterministic` is meaningless for these, so opt out of it:
SKILL_EVOLUTION_EVALUATORS=llm_judge,regression \
  python3 scripts/evaluate.py --eval-target proposal --proposal-id <id>
SKILL_EVOLUTION_EVALUATORS=llm_judge,regression \
  python3 scripts/evaluate.py --eval-target tool_calls --session-id <id>
SKILL_EVOLUTION_EVALUATORS=llm_judge,regression \
  python3 scripts/evaluate.py --eval-target analyzer_prompt --session-id <id> --proposal-id <id>

# --- Optional GEPA optimizer (needs the `gepa` extra) ---
export SKILL_EVOLUTION_OPTIMIZER_ENABLED=true
python3 scripts/optimize_skill.py --list-candidates              # low-scoring `skill:` targets
python3 scripts/optimize_skill.py --list-candidates --target all # include the other three namespaces
python3 scripts/optimize_skill.py --skill <name> [--iterations N]
```

## Evaluation Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SKILL_EVOLUTION_EVALUATORS` | `deterministic,llm_judge,regression` | Which evaluators run |
| `SKILL_EVOLUTION_GATE_STRICTNESS` | `strict` | `strict` (all must pass) or `majority`; per-type override via `SKILL_EVOLUTION_GATE_STRICTNESS_<TYPE>` |
| `SKILL_EVOLUTION_MAX_SKILL_SIZE_KB` | `15` | Absolute size limit, applied as a ratchet: an over-limit skill may still be replaced by a body no larger than itself; a new skill may not be created over it |
| `SKILL_EVOLUTION_MAX_GROWTH_PCT` | `20` | Per-pass growth-vs-baseline limit |
| `SKILL_EVOLUTION_MAX_SHRINK_PCT` | `15` | Per-pass shrink floor (tighter than growth on purpose) |
| `SKILL_EVOLUTION_MAX_SHRINK_BYTES` | `2048` | Absolute per-pass deletion limit; the stricter of this and the percentage floor applies. `0` disables it |
| `SKILL_EVOLUTION_MAX_CUMULATIVE_GROWTH_PCT` | `50` | Total growth vs the skill's *original* recorded size |
| `SKILL_EVOLUTION_MAX_CUMULATIVE_SHRINK_PCT` | `30` | Total shrink vs the original; bounds compounding erosion |
| `SKILL_EVOLUTION_LLM_JUDGE_THRESHOLD` | `0.7` | Minimum average rubric score to pass |
| `SKILL_EVOLUTION_PROVIDER` | `claude` | `claude`, `ollama`, `opencode`, `openai`, or `gemini`; per-evaluator override via `SKILL_EVOLUTION_<EVALUATOR>_PROVIDER` |
| `SKILL_EVOLUTION_PROVIDER_TIMEOUT` | `60` | HTTP timeout (seconds) for every provider call — one generic knob, since the latency it exists for is a property of the model, not the adapter. Raise it for local reasoning models, which spend much of their output on chain-of-thought before the JSON |
| `ANTHROPIC_API_KEY` | — | Required by the `claude` provider |
| `SKILL_EVOLUTION_CLAUDE_MODEL` | `claude-sonnet-5` | Model used by the Claude provider adapter |
| `SKILL_EVOLUTION_OLLAMA_BASE_URL` / `_MODEL` | `http://localhost:11434/v1` / `llama3` | Local-model provider adapter config |
| `OPENCODE_API_KEY` | — | Required by the `opencode` provider; fails closed if unset |
| `SKILL_EVOLUTION_OPENCODE_BASE_URL` | `https://opencode.ai/zen/v1` | Zen carries `big-pickle` and the `*-free` models; the Go tier (`/zen/go/v1`) is a different, smaller catalogue — point this at the tier matching your model id |
| `SKILL_EVOLUTION_OPENCODE_MODEL` | `big-pickle` | Model id from the chosen catalogue |
| `OPENAI_API_KEY` | — | Required by the `openai` provider; fails closed if unset |
| `SKILL_EVOLUTION_OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible base URL |
| `SKILL_EVOLUTION_OPENAI_MODEL` | `gpt-4o` | Model used by the OpenAI provider adapter |
| `GEMINI_API_KEY` | — | Required by the `gemini` provider; fails closed if unset |
| `SKILL_EVOLUTION_GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com` | Gemini API base URL |
| `SKILL_EVOLUTION_GEMINI_MODEL` | `gemini-2.0-flash` | Model used by the Gemini provider adapter |
| `SKILL_EVOLUTION_HISTORY_PATH` | `docs/skill-evolution/eval_history.jsonl` | Shared eval history file |
| `SKILL_EVOLUTION_HISTORY_RETENTION` | unbounded | Max versions (bare int) or age (`90d`, `6mo`); always keeps up to 3 anchor entries per target (most recent, most recent passing, earliest); applies automatically after every evaluation, not just via `--prune` |
| `SKILL_EVOLUTION_HISTORY_ARCHIVE_PATH` | sibling of the history file (`eval_history.archive.jsonl`) | Where entries pruned by the above are moved, not deleted |
| `SKILL_EVOLUTION_PROPOSALS_DIR` | `docs/skill-evolution/proposals` | Where proposals are written |
| `SKILL_EVOLUTION_STATE_FILE` | `~/.hermes/skill_evolution_state.json` | Which sessions have already been analyzed; override to run against isolated state |
| `SKILL_EVOLUTION_STATE_RETENTION` | unbounded | Max processed-session entries (bare int) or age (`90d`, `6mo`); flat-dict state shape only (see below); applies automatically, and via `fetch_sessions.py --prune-state` |
| `SKILL_EVOLUTION_AUTO_APPLY` / `_MIN_CONFIDENCE` | `false` / `0.85` | Inputs to `apply_proposal()`; the analysis session never self-applies regardless |
| `SKILL_EVOLUTION_OPTIMIZER_ENABLED` | `false` | Enables `scripts/optimize_skill.py` |
| `SKILL_EVOLUTION_OPTIMIZER_MIN_SESSIONS` | `3` | Minimum recorded sessions before the optimizer will run for a skill |
| `SKILL_EVOLUTION_OPTIMIZER_MAX_METRIC_CALLS` | `8` | GEPA metric-call budget per run (tuned 2026-07-26 from real-run cost data; `--iterations` always overrides) |
| `SKILL_EVOLUTION_OPTIMIZER_MAX_SESSIONS_FOR_SKILL` | `20` | Cap on sessions fed to the optimizer as trainset |

A malformed numeric value falls back to the default and says so on stderr, rather than
raising out of whichever component reads it first.

## Safety

- **Auto-apply is OFF by default.** You must explicitly enable it.
- **Proposals are markdown files** — human-readable, human-reviewable.
- **Confidence gate** — only proposals with strong evidence get auto-applied.
- **Nothing is applied without a human.** No automated step calls `apply_proposal()` — there is no second cron job and nothing in `scripts/` does it, so a proposal waits until someone acts on it.
- **Not scoped to agent-created skills.** The gate does not distinguish hub-installed skills from agent-created ones; if you enable auto-apply, any installed skill is in scope. Scope it yourself if that matters.
- **Git-friendly** — proposal files are plain text, commit them for lineage.

## Tips

- **Start in review-only mode** for a week. See what the agent proposes before enabling auto-apply.
- **Adjust confidence threshold** to your taste. Lower = more auto-apply. Higher = safer.
- **Run it manually first** to understand the output format.
- **Commit proposals to git** — they're your skill evolution history.
- **Reset analysis** — delete `~/.hermes/skill_evolution_state.json` to re-process all sessions.
- **For deeper analysis**, load `code-review-and-quality` skill alongside this one.

## Troubleshooting

| Problem | Likely fix |
|---------|------------|
| "No sessions found" | Have you used Hermes recently? Run `hermes sessions list` to check |
| Proposals feel low quality | Try a stronger model in the cron job |
| Script won't import | Check the path: Python can't import from directories with hyphens |
| Cron job not delivering | Check `hermes cron list` — the job needs a delivery target |

## About

Created by the Hermes Agent community. Inspired by `NousResearch/hermes-agent-self-evolution`
and practical experience running skill evolution in production since July 2026.

MIT License — use, modify, share.
