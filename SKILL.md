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
hermes skills install https://raw.githubusercontent.com/<your-org>/hermes-skill-evolution/main/SKILL.md
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
4. Generate proposal files in `proposals/`
5. Deliver a summary

### 3. Review Proposals

```bash
ls proposals/
cat proposals/20260723-001-my-proposal.md
```

Each proposal is a markdown file with a structured YAML frontmatter. Read it, decide, change `status: approved` if you want.

### 4. Auto-Apply (Opt-In)

By default, proposals are **review-only**. Enable auto-apply:

```bash
# Set in your .env or session
export SKILL_EVOLUTION_AUTO_APPLY=true
export SKILL_EVOLUTION_MIN_CONFIDENCE=0.85
```

Then ask the agent again. Proposals with confidence ≥ 0.85 will be auto-applied via `skill_manage`.

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

| Type | Description | Auto-apply? |
|------|-------------|-------------|
| `improve_existing` | Patch a skill's description or body | ✅ (with confidence ≥ threshold) |
| `create_new` | Create a skill for a recurring pattern | ✅ (with confidence ≥ threshold) |
| `merge_skills` | Combine overlapping skills | ✅ (with confidence ≥ threshold) |
| `deprecate_skill` | Remove a stale skill | ✅ (with confidence ≥ threshold) |

## Constraints & Guardrails

Every auto-applied change must pass an evaluation gate (`scripts/evaluate.py`) before `apply_proposal()` calls `skill_manage`:

- **Size limit**: Skills ≤ 15KB (`SKILL_EVOLUTION_MAX_SKILL_SIZE_KB`)
- **Growth limit**: No more than 20% larger than baseline (`SKILL_EVOLUTION_MAX_GROWTH_PCT`)
- **Structure**: Valid YAML frontmatter with `name:` and `description:`
- **Non-empty**: Evolved artifact must not be empty
- **Rubric score**: An LLM judge scores correctness, procedure-following, and conciseness against a configurable threshold (`SKILL_EVOLUTION_LLM_JUDGE_THRESHOLD`, default 0.7)
- **No regression**: The new score must not fall below the target's last passing score in its evaluation history
- **No hub skill mutation**: Only modifies `created_by: agent` skills (Hermes curator convention)

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
| `scripts/optimize_skill.py` | Optional GEPA-lite optimizer — drafts proposals from low-scoring history (needs `dspy`) |
| `scripts/state.py` | Track processed sessions in JSON state file |
| `scripts/skill-evolution-fetch.sh` | Cron wrapper (sets CWD, calls fetch_sessions) |

## Dependencies

- Python 3.10+
- Hermes Agent (any version with `state.db`, `cronjob`, `skill_manage`, `write_file`)

No pip packages required beyond Python stdlib for the core pipeline. `dspy>=3.0.0` is an optional extra (`pip install -e ".[optimizer]"`) needed only for `scripts/optimize_skill.py`.

## Evaluation Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SKILL_EVOLUTION_EVALUATORS` | `deterministic,llm_judge,regression` | Which evaluators run |
| `SKILL_EVOLUTION_GATE_STRICTNESS` | `strict` | `strict` (all must pass) or `majority`; per-type override via `SKILL_EVOLUTION_GATE_STRICTNESS_<TYPE>` |
| `SKILL_EVOLUTION_MAX_SKILL_SIZE_KB` | `15` | Deterministic size limit |
| `SKILL_EVOLUTION_MAX_GROWTH_PCT` | `20` | Deterministic growth-vs-baseline limit |
| `SKILL_EVOLUTION_LLM_JUDGE_THRESHOLD` | `0.7` | Minimum average rubric score to pass |
| `SKILL_EVOLUTION_PROVIDER` | `claude` | `claude` or `ollama`; per-evaluator override via `SKILL_EVOLUTION_<EVALUATOR>_PROVIDER` |
| `SKILL_EVOLUTION_CLAUDE_MODEL` | `claude-sonnet-5` | Model used by the Claude provider adapter |
| `SKILL_EVOLUTION_OLLAMA_BASE_URL` / `_MODEL` | `http://localhost:11434/v1` / `llama3` | Local-model provider adapter config |
| `SKILL_EVOLUTION_HISTORY_PATH` | `docs/skill-evolution/eval_history.jsonl` | Shared eval history file |
| `SKILL_EVOLUTION_HISTORY_RETENTION` | unbounded | Max versions (bare int) or age (`90d`, `6mo`); always keeps the latest entry per target |
| `SKILL_EVOLUTION_OPTIMIZER_ENABLED` | `false` | Enables `scripts/optimize_skill.py` |

## Safety

- **Auto-apply is OFF by default.** You must explicitly enable it.
- **Proposals are markdown files** — human-readable, human-reviewable.
- **Confidence gate** — only proposals with strong evidence get auto-applied.
- **No hub skills touched** — only skills created by the agent.
- **No destructive actions** — deprecation proposals require confidence ≥ 0.85 and are reviewable.
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
