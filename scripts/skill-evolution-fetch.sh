#!/bin/bash
# Wrapper script for the skill-evolution cron job.
# Sets the working directory and calls fetch_sessions.
# Usage: ./skill-evolution-fetch.sh

set -euo pipefail

# Use project root if in a repo, otherwise home directory
if git rev-parse --show-toplevel 2>/dev/null; then
    PROJECT_ROOT=$(git rev-parse --show-toplevel)
else
    PROJECT_ROOT=$HOME
fi

cd "$PROJECT_ROOT"

# Find the scripts directory
SCRIPT_DIR="${HERMES_HOME:-$HOME/.hermes}/scripts/skill-evolution"
if [ ! -d "$SCRIPT_DIR" ]; then
    SCRIPT_DIR="$PROJECT_ROOT/docs/skill-evolution/scripts"
fi

python3 "$SCRIPT_DIR/fetch_sessions.py"
