---
name: skill-evolution
description: "Autonomous skill improvement — analyze agent sessions, generate structured improvement proposals, and optionally auto-apply high-confidence changes to your skills. Host-agnostic (Hermes, Claude Code, and any HostAdapter)"
author: Carlo Alva
version: 1.0.0
platforms: [macos, linux]
metadata:
  hermes:
    tags: [self-improvement, skills, automation, pipeline, proposals, maintenance]
    category: automation
    related_skills: [code-review-and-quality, using-agent-skills, cronjob-management]
---

# Skill Evolution

A **self-improvement feedback loop** for agent skills. Reads your past sessions, analyzes them against your loaded skills, and generates structured proposals to make your skills better over time.

The skill is host-agnostic: it ships adapters for Hermes (`HermesAdapter`, default) and
Claude Code (`ClaudeCodeAdapter`, set `SKILL_EVOLUTION_HOST=claude_code`). Other hosts can
be added via the `HostAdapter` interface in `scripts/host.py`. The cron prompt and the
session analyzer are written in host-agnostic language and use the env vars
`SKILL_EVOLUTION_*` to talk to whichever host is configured.

No daemons. No external services. No GPU. Just your host agent, a scheduled job, and a
few Python scripts.

## Minimum Configuration

The full knob list lives in "Evaluation Environment Variables" below, but two things are
**not optional** — skip either one and the pipeline runs without erroring while quietly
doing the wrong thing:

- **`SKILL_EVOLUTION_HOST`** — defaults to `hermes`. There is no auto-detection: on
  Claude Code, forgetting to set this to `claude_code` means every read (and any write)
  targets Hermes's session DB and skills tree instead of `~/.claude`.
- **A provider credential** for the evaluation gate — `ANTHROPIC_API_KEY` by default
  (`SKILL_EVOLUTION_PROVIDER=claude`), or the key matching whichever provider you pick
  instead. Without one, `llm_judge` fails **closed** on every call, which means the gate
  fails on every proposal forever — even after you set `status: approved` by hand. There
  is no error message pointing back at this; the gate just never passes.

Everything else has a default you can leave alone for a first run.

## How It Works

```
fetch_sessions.py ──► NDJSON session data
       │
       ▼ (injected into the job agent's prompt)
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

Installation is per-host. The skill itself is a folder of files; the install command
differs by host.

**Hermes:**

```bash
hermes skills install https://raw.githubusercontent.com/Carlo1911/skill-evolution/main/SKILL.md
```

**Claude Code:** Clone the repo and load the skill as a directory:

```bash
git clone https://github.com/Carlo1911/skill-evolution ~/projects/skill-evolution
# Then point Claude Code at it (or symlink skills/<name> into ~/.claude/skills/)
```

**Other / manual:** Clone the repo anywhere, point your host at the
`SKILL.md` (or the script directory) and set `SKILL_EVOLUTION_REPO` to the checkout path
for your cron/job configuration to find `scripts/`.

> This repo ships the pipeline scripts, not an analyzer prompt or a cron job definition —
> those are operator-specific (which host, what schedule, where results go) and belong in
> your own job configuration. See "Scheduled Runs (Cron)" below for the contract your
> prompt needs to satisfy.

### 2. Load and Run

**Hermes:**

```bash
hermes -s skill-evolution
# Then ask:
"Run skill evolution analysis on my recent sessions"
```

**Claude Code:** load the skill in your session, then run the same prompt. Set
`SKILL_EVOLUTION_HOST=claude_code` in the environment so the read/write adapter routes
to `~/.claude`.

The agent will:
1. Fetch recent unprocessed sessions from the host's session database
2. Scan your installed skills
3. Analyze each session for skill coverage gaps
4. Generate proposal files in `./proposals/` (overridable via `SKILL_EVOLUTION_PROPOSALS_DIR`)
5. Deliver a summary

### 3. Review Proposals

```bash
ls proposals/                          # default location, relative to the skill repo
cat proposals/<id>.md                  # override with SKILL_EVOLUTION_PROPOSALS_DIR
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
analyzer prompt forbids calling the host's skill-mutation tool, whatever the confidence.
`AUTO_APPLY` and `MIN_CONFIDENCE` are inputs to `apply_proposal()`, which a human (or a
step you write) runs afterwards; the gate then re-scores the proposal and only emits
mutation instructions if it passes. On Hermes, those instructions are `skill_manage`
calls; on Claude Code, they are direct file writes. Nothing in this repo invokes that
step for you.

There is no CLI for this step on purpose — invoke it directly:

```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from proposal import load_proposal, apply_proposal
p = load_proposal('proposals/20260804-001.md')
result = apply_proposal(p, min_confidence=0.85)
print('can_apply:', result['can_apply'])
print('reason:', result.get('reason', result.get('evaluation_results')))
"
```

`can_apply: True` means different things per host: on Claude Code the skill file was
already rewritten (`applied_by: direct`); on Hermes the result carries `skill_manage`
instruction dicts a separate agent step still has to execute (`applied_by: agent`) —
`apply_proposal()` never calls `skill_manage` itself. `False` means the gate failed
(check `evaluation_results`), the host can't write skills, or the proposal itself is
invalid (e.g. a `create_new` with a placeholder body).

## Scheduled Runs (Cron)

Schedule a job on whichever host you're using. This repo doesn't ship a job prompt —
write your own (or ask your host agent to draft one) that:

- Finds the skill repo via `SKILL_EVOLUTION_REPO` (or runs with it as the cwd)
- Runs `scripts/fetch_sessions.py` piped into `scripts/skill_index.py`'s output as context
- Has an LLM analyze that output against the criteria in "What Gets Analyzed" below and
  write proposal files matching `proposal.py`'s schema
- Never calls the host's skill-mutation tool directly — only `apply_proposal()` (run by a
  human, or a separate step) may do that, after the evaluation gate passes
- Uses the standard `SKILL_EVOLUTION_*` env vars to find the right directories, so the same
  prompt works across hosts

**Hermes example:**

```bash
# In a session with the skill loaded, ask:
"Create a daily cron job for skill evolution, running at 9 AM, delivering results to my home channel"
```

Or set it up manually with `hermes cron create`, passing your own job prompt via `--prompt`.

**Claude Code / other hosts:** schedule the equivalent on your host's cron, using the same
job-prompt contract above — the host-specific bits are the install/cron command, not the
prompt itself.

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
instructions are for a human or agent to execute — `apply_proposal()` itself is host-
agnostic and hands the mutation to the active `HostAdapter` (Hermes emits `skill_manage`
instruction dicts; Claude Code writes skill files directly).

## Constraints & Guardrails

Every auto-applied change must pass an evaluation gate (`scripts/evaluate.py`) before the active `HostAdapter` runs the mutation:

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
| `scripts/fetch_sessions.py` | Read the host's session database → NDJSON session data |
| `scripts/skill_index.py` | Scan installed skills → structured JSON index |
| `scripts/analyze.py` | Format NDJSON sessions for LLM consumption |
| `scripts/proposal.py` | Proposal schema, I/O, and `apply_proposal()` logic |
| `scripts/evaluate.py` | Evaluation gate: deterministic + LLM-judge + regression + opt-in `human_review` evaluators, versioned history |
| `scripts/host.py` | `HostAdapter` ABC + `HermesAdapter` + `ClaudeCodeAdapter`. Routes session/skill reads and proposal writes through the active host |
| `scripts/optimize_skill.py` | Optional GEPA optimizer — runs a real `gepa.optimize_anything()` loop over a skill's own session history (needs the `gepa` extra) |
| `scripts/skill_quality.py` | Periodic skill quality tracking — evaluates all installed skills and generates trend reports |
| `scripts/state.py` | Track processed sessions in JSON state file (per-host) |
| `scripts/embedding_backends.py` | Embedding backend ABC + `FastEmbedBackend` for the `embedding_similarity` evaluator |
| `scripts/embedding_similarity.py` | `EmbeddingSimilarityEvaluator` (opt-in; semantic duplicate / drift / grounding) |
| `scripts/skill-evolution-fetch.sh` | Cron wrapper (sets CWD, calls fetch_sessions) |
| `scripts/skill-quality-report.sh` | Cron wrapper for skill quality reports |

## Dependencies

- Python 3.10+
- A supported host (Hermes or Claude Code). For Hermes: any version with `state.db`,
  `cronjob`, `skill_manage`, `write_file`. For Claude Code: the standard
  `~/.claude/{skills,projects}` layout. Other hosts can be added via `HostAdapter`.

No pip packages required beyond Python stdlib for the core pipeline. Two optional extras:

- `pip install -e ".[optimizer]"` installs `gepa==0.1.4` (the standalone PyPI package,
  **not** `dspy`) for `scripts/optimize_skill.py`.
- `pip install -e ".[embeddings]"` installs `fastembed>=0.2.0` (~50MB, ONNX-based, no
  PyTorch) for the `embedding_similarity` evaluator.

## Evaluation Targets

The evaluation framework scores four independent targets, each writing to the same history file with its own namespace:

| Target | Function | History key | What it scores |
|--------|----------|-------------|----------------|
| **skill text** (v1) | `evaluate_skill_text()` | `skill:<name>` | The body/description change in a proposal — size, growth, frontmatter structure, rubric quality, regression vs prior passes |
| **proposal quality** | `evaluate_proposal()` | `proposal:<uuid>` | The proposal as a document — summary clarity, rationale groundedness, change coherence |
| **tool-call quality** | `evaluate_tool_calls()` | `tool_calls:<session_id>` | Tool selection, sequencing, and result extraction from the session that produced the proposal |
| **analyzer prompt quality** | `evaluate_analyzer_prompt()` | `analyzer_prompt:<session_id>` | Whether the analyzer's generation step produced a proposal grounded in the sessions it was given |

The auto-apply gate runs every target in `SKILL_EVOLUTION_GATE_TARGETS` — **skill text** and **proposal quality** by default, `tool_calls`/`analyzer_prompt` if you add them. Each target's evaluators combine under its own strictness and the overall decision is the AND of every gating target, so a weak proposal document blocks apply even when the skill text itself passes. Targets with no data never block: a proposal without `session_ids` simply skips the session-based targets.

The `deterministic` evaluator (size/growth checks) is meaningful only for skill text. For the other three targets, set `SKILL_EVOLUTION_EVALUATORS=llm_judge,regression` to skip it.

### Human-in-the-loop evaluator (opt-in)

`human_review` is an interactive evaluator, added by listing it in
`SKILL_EVOLUTION_EVALUATORS=...,human_review`. It is never in the default set and never runs
in a cron job:

- **Requires a TTY** — without an interactive terminal (`stdin`/`stdout` not `isatty()`) it
  fails closed and the gate decision treats it as a failure, so it can't silently no-op in
  unattended runs.
- **Binary prompt**: `y`/`yes` approves, `n`/`no` rejects; empty or unparsable input
  re-prompts up to 3 times, then fails closed. `EOF`/`Ctrl-C` also fail closed.
- **Runs last** in the gate, after the automatic evaluators and the regression check, and
  shows you those prior verdicts before asking.
- **Approve/reject returns the automatic aggregate score, not 1.0** — a hardcoded 1.0 would
  inflate the evaluation history that the regression check and the optimizer baseline
  against.
- **One prompt per gating target** (each target in `SKILL_EVOLUTION_GATE_TARGETS` asks
  once). To get a single prompt for only the skill-text change, set
  `SKILL_EVOLUTION_GATE_TARGETS=skill`.

### Embedding similarity evaluator (opt-in)

`embedding_similarity` uses vector embeddings to detect semantic patterns the rubric judge
can miss. Add it via `SKILL_EVOLUTION_EVALUATORS=...,embedding_similarity`. It is never in
the default set. Requires the `embeddings` extra (`pip install -e ".[embeddings]"`), which
installs `fastembed` (~50MB, ONNX-based, no PyTorch).

- **Three modes**, selected automatically based on the context provided:
  - **duplicate_detection**: flags content too similar to existing skills (similarity >
    threshold). Context key: `existing_skills`.
  - **drift_detection**: flags content that drifted too far from its baseline (similarity <
    threshold). Context key: `baseline`.
  - **grounding_check**: flags content not grounded in source sessions (average similarity <
    threshold). Context key: `sessions`.
- **Thresholds** are configurable via environment variables (see below).
- **Backend architecture** is extensible. The default backend is `fastembed`, but the
  architecture supports other backends (`ollama`, `openai`, `llama_cpp`) via
  `SKILL_EVOLUTION_EMBEDDING_BACKEND`. Only `fastembed` is implemented; the others are
  stubbed with implementation examples in their docstrings.

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

# --- One-off evaluation of any of the four targets (advisory by default --
# -- only `skill` and `proposal` gate auto-apply; `tool_calls`/`analyzer_prompt`
# -- must be added to SKILL_EVOLUTION_GATE_TARGETS to gate) ---
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

# --- Skill quality tracking ---
python3 scripts/skill_quality.py                                 # evaluate all skills, print report
python3 scripts/skill_quality.py --output report.md              # write report to file
python3 scripts/skill_quality.py --skill <name>                  # evaluate one skill
python3 scripts/skill_quality.py --since 30d                     # skip skills evaluated in the last 30 days (cost control)
python3 scripts/skill_quality.py --below 0.7                     # only skills below threshold
python3 scripts/skill_quality.py --format json                   # JSON output instead of markdown
```

## Evaluation Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SKILL_EVOLUTION_EVALUATORS` | `deterministic,llm_judge,regression` | Which evaluators run. Add `human_review` for interactive TTY gate (binary approve/reject, fails closed without terminal; never in cron). Add `embedding_similarity` for semantic similarity checks (requires `embeddings` extra) |
| `SKILL_EVOLUTION_GATE_STRICTNESS` | `strict` | `strict` (all must pass) or `majority`; per-proposal-type override via `SKILL_EVOLUTION_GATE_STRICTNESS_<TYPE>` and per-target override via `SKILL_EVOLUTION_GATE_STRICTNESS_<TARGET>` (target wins) |
| `SKILL_EVOLUTION_GATE_TARGETS` | `skill,proposal` | Which evaluation targets gate auto-apply. Comma-separated: `skill`, `proposal`, `tool_calls`, `analyzer_prompt`. The gate is the AND of every target listed; a proposal with no `session_ids` skips the session-based ones. Adding `tool_calls`/`analyzer_prompt` widens what blocks apply and costs up to 2 extra provider calls per session per apply |
| `SKILL_EVOLUTION_MAX_SKILL_SIZE_KB` | `15` | Absolute size limit, applied as a ratchet: an over-limit skill may still be replaced by a body no larger than itself; a new skill may not be created over it |
| `SKILL_EVOLUTION_MAX_GROWTH_PCT` | `20` | Per-pass growth-vs-baseline limit |
| `SKILL_EVOLUTION_MAX_SHRINK_PCT` | `15` | Per-pass shrink floor (tighter than growth on purpose) |
| `SKILL_EVOLUTION_MAX_SHRINK_BYTES` | `2048` | Absolute per-pass deletion limit; the stricter of this and the percentage floor applies. `0` disables it |
| `SKILL_EVOLUTION_MAX_CUMULATIVE_GROWTH_PCT` | `50` | Total growth vs the skill's *original* recorded size |
| `SKILL_EVOLUTION_MAX_CUMULATIVE_SHRINK_PCT` | `30` | Total shrink vs the original; bounds compounding erosion |
| `SKILL_EVOLUTION_LLM_JUDGE_THRESHOLD` | `0.7` | Minimum average rubric score to pass |
| `SKILL_EVOLUTION_EMBEDDING_BACKEND` | `fastembed` | Embedding backend to use. Options: `fastembed` (implemented), `ollama`, `openai`, `llama_cpp` (stubbed). Requires the corresponding backend to be available |
| `SKILL_EVOLUTION_FASTEMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Model name for the `fastembed` backend. Models are cached in `~/.cache/fastembed/` |
| `SKILL_EVOLUTION_EMBEDDING_DUPLICATE_THRESHOLD` | `0.85` | Max cosine similarity before flagging as duplicate (higher = stricter) |
| `SKILL_EVOLUTION_EMBEDDING_DRIFT_THRESHOLD` | `0.70` | Min cosine similarity before flagging as drift (lower = stricter) |
| `SKILL_EVOLUTION_EMBEDDING_GROUNDING_THRESHOLD` | `0.60` | Min average cosine similarity for grounding check (lower = stricter) |
| `SKILL_EVOLUTION_PROVIDER` | `claude` | `claude`, `ollama`, `opencode`, `openai`, or `gemini`; per-evaluator override via `SKILL_EVOLUTION_<EVALUATOR>_PROVIDER` |
| `SKILL_EVOLUTION_PROVIDER_TIMEOUT` | `60` | HTTP timeout (seconds) for every provider call — one generic knob, since the latency it exists for is a property of the model, not the adapter. Raise it for local reasoning models, which spend much of their output on chain-of-thought before the JSON |
| `SKILL_EVOLUTION_PROVIDER_RETRIES` | `2` | Retries *after* the first attempt for transient transport failures (HTTP 408/429/5xx, timeouts, connection errors). `0` disables retry and restores strict fail-fast. Auth/4xx and malformed responses are never retried |
| `SKILL_EVOLUTION_PROVIDER_RETRY_BASE_SECONDS` | `1.0` | Base backoff delay in seconds; each retry waits `base × 2^attempt` (a 429 `Retry-After` header overrides the schedule) |
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
| `SKILL_EVOLUTION_HISTORY_PATH` | `eval_history.jsonl` (repo root) | Shared eval history file |
| `SKILL_EVOLUTION_HISTORY_RETENTION` | unbounded | Max versions (bare int) or age (`90d`, `6mo`); always keeps up to 3 anchor entries per target (most recent, most recent passing, earliest); applies automatically after every evaluation, not just via `--prune` |
| `SKILL_EVOLUTION_HISTORY_ARCHIVE_PATH` | sibling of the history file (`eval_history.archive.jsonl`) | Where entries pruned by the above are moved, not deleted |
| `SKILL_EVOLUTION_PROPOSALS_DIR` | `proposals/` (repo root) | Where proposals are written |
| `SKILL_EVOLUTION_QUALITY_REPORT_DIR` | `reports/` (repo root) | Where the skill-quality cron wrapper writes its timestamped report. The `skill-quality-report.sh` wrapper honors this; `--output` on `skill_quality.py` overrides per-run |
| `SKILL_EVOLUTION_DB_PATH` | per-host (Hermes: `~/.hermes/state.db`) | Session database the `tool_calls` / `analyzer_prompt` targets read. `fetch_sessions.py` takes `--db-path` instead; this covers the in-process callers that have no flag to thread a path through |
| `SKILL_EVOLUTION_STATE_FILE` | per-host (see below) | Universal override for processed-session state. Each host defaults to its own location: Hermes → `~/.hermes/skill_evolution_state.json`, Claude Code → `<SKILL_EVOLUTION_CLAUDE_CODE_HOME>/skill_evolution_state.json`. When set, redirects whichever host is active |
| `SKILL_EVOLUTION_STATE_RETENTION` | unbounded | Max processed-session entries (bare int) or age (`90d`, `6mo`); flat-dict state shape only (see below); applies automatically, and via `fetch_sessions.py --prune-state` |
| `SKILL_EVOLUTION_HOST` | `hermes` | Which host adapter (`scripts/host.py`) session/skill reads and writes route through — `hermes` or `claude_code`. On `hermes`, `apply_proposal()` emits `skill_manage` instruction dicts (`applied_by: agent`) for the agent to execute; on `claude_code`, it writes skill files directly (`applied_by: direct`), archiving deprecate/merge sources under `skills/.archive/` and refusing symlinked skill dirs |
| `SKILL_EVOLUTION_CLAUDE_CODE_HOME` | `~/.claude` | Root directory the `claude_code` host adapter reads skills (`skills/*/SKILL.md`) and sessions (`projects/*/*.jsonl`) from, writes applied skills to (`skills/<name>/SKILL.md`, archive under `skills/.archive/`), and stores processed-session state (`skill_evolution_state.json`) |
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
- **Reset analysis** — delete the host's state file (Hermes:
  `~/.hermes/skill_evolution_state.json`; Claude Code: `<CLAUDE_CODE_HOME>/skill_evolution_state.json`,
  or set `SKILL_EVOLUTION_STATE_FILE` to redirect) to re-process all sessions.
- **For deeper analysis**, load `code-review-and-quality` skill alongside this one.

## Troubleshooting

| Problem | Likely fix |
|---------|-----------|
| "No sessions found" | Check that the host adapter sees your session database: `hermes sessions list` (Hermes) or look under `~/.claude/projects/` (Claude Code) |
| A proposal stays `proposed` forever, even after you approve it | Check that a provider credential is set (`ANTHROPIC_API_KEY` by default) — without one, `llm_judge` fails closed on every call and the gate never passes. Run `python3 scripts/evaluate.py --list-evaluators` to see the resolved set, or drop `llm_judge` from `SKILL_EVOLUTION_EVALUATORS` if you only want the deterministic checks |
| Cron timeout 3600s / script auto-recurses | `scripts/skill-evolution-fetch.sh` must exec `fetch_sessions.py` found relative to its own location — if your deployed wrapper instead execs a copy of itself (e.g. a self-referential install script), you get an infinite `exec` loop until the job times out. Fix: make sure the deployed wrapper matches this repo's `scripts/skill-evolution-fetch.sh` |
| Proposals feel low quality | Try a stronger model in the cron job |
| Script won't import | Check the path: Python can't import from directories with hyphens |
| Cron job not delivering | Check the host's job list (Hermes: `hermes cron list`) — the job needs a delivery target |

## About

Created by Carlo Alva. Inspired by `NousResearch/hermes-agent-self-evolution` and
practical experience running skill evolution in production since July 2026. Originally
built for Hermes Agent; the host-agnostic refactor (2026-08-03) generalized the design
so the same pipeline works for Claude Code and any future host that implements the
`HostAdapter` interface.

MIT License — use, modify, share.
