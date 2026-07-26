#!/bin/bash
# Wrapper the skill-evolution cron job invokes, so the cron entry can stay a single
# stable command. It emits NDJSON on stdout for analyze.py to consume.
#
# Resolves fetch_sessions.py relative to THIS script's own location, because that is
# where it lives (both are in scripts/). Two earlier resolution strategies were wrong:
#
#   - Probing ${HERMES_HOME}/scripts/skill-evolution with a docs/skill-evolution/scripts
#     fallback: neither path exists in this repo, so the wrapper always failed.
#   - `if git rev-parse --show-toplevel; then` to find the project root: that command's
#     stdout is not captured, so it prepended the repo path as a stray line to the NDJSON
#     stream piped into analyze.py.
#
# Nothing here may write to stdout except fetch_sessions.py. Arguments are passed through,
# so --dry-run and --lookback-hours work via the wrapper too.

set -euo pipefail

# Resolve symlinks before taking dirname: the deployed entry point is a symlink into this
# repo (see docs), and dirname of the *link* would point at the deploy directory, which
# holds no Python. Loop rather than `readlink -f` for portability across BSD/GNU.
SELF="${BASH_SOURCE[0]}"
while [ -L "$SELF" ]; do
    TARGET="$(readlink -- "$SELF")"
    case "$TARGET" in
        /*) SELF="$TARGET" ;;
        *)  SELF="$(dirname -- "$SELF")/$TARGET" ;;   # relative link
    esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "$SELF")" && pwd)"
FETCH="$SCRIPT_DIR/fetch_sessions.py"

if [ ! -f "$FETCH" ]; then
    echo "skill-evolution-fetch.sh: fetch_sessions.py not found next to this script ($SCRIPT_DIR)" >&2
    exit 1
fi

exec "${SKILL_EVOLUTION_PYTHON:-python3}" "$FETCH" "$@"
