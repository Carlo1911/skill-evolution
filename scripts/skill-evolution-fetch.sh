#!/bin/bash
set -euo pipefail

# Real script lives under the skill directory within ~/.hermes/
SKILL_SCRIPT="$HOME/.hermes/skills/general-skills/hermes-skill-evolution/scripts/skill-evolution-fetch.sh"

if [ ! -f "$SKILL_SCRIPT" ]; then
    echo "skill-evolution-fetch.sh: target not found at $SKILL_SCRIPT" >&2
    exit 1
fi

exec "$SKILL_SCRIPT" "$@"
