#!/bin/bash
# Cron wrapper for skill quality tracking
# Resolves skill_quality.py relative to this script's location, following symlinks

# Resolve the directory this script is in, following symlinks
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Report output directory. Default is `<repo>/reports/`; deployments can override
# via SKILL_EVOLUTION_QUALITY_REPORT_DIR when they want reports stored elsewhere
# (e.g. inside a user-owned data dir that the host agent also reads).
OUTPUT_DIR="${SKILL_EVOLUTION_QUALITY_REPORT_DIR:-$SCRIPT_DIR/../reports}"
mkdir -p "$OUTPUT_DIR"
OUTPUT_FILE="${OUTPUT_DIR}/skill-quality-$(date +%Y-%m-%d).md"

# Run the quality tracker
python3 "$SCRIPT_DIR/skill_quality.py" --output "$OUTPUT_FILE"

# Report completion
echo "Skill quality report generated: $OUTPUT_FILE" >&2
