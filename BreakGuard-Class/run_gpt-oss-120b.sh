#!/usr/bin/env bash
# Run the BreakGuard-Class pipeline (all 5 phases) for the gpt-oss-120b model.
#
# Usage:
#   ./run_gpt-oss-120b.sh <DATA_ROOT> [extra run_experiment.py args...]
#
# Example:
#   ./run_gpt-oss-120b.sh /Volumes/Rachna-HD
#   ./run_gpt-oss-120b.sh /Volumes/Rachna-HD --stages breaking-single breaking-multi
#   ./run_gpt-oss-120b.sh /Volumes/Rachna-HD --results-root /Volumes/RachnaPSSD
set -euo pipefail

DATA_ROOT="${1:?Usage: $0 <DATA_ROOT> [extra run_experiment.py args...]}"
shift || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/run_experiment.py" --model gpt-oss-120b --data-root "$DATA_ROOT" "$@"
