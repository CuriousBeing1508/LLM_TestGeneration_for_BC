#!/usr/bin/env bash
# Run the BreakGuard-Method pipeline (all 5 phases) for the qwen3-480b model.
#
# Usage:
#   ./run_qwen3-480b.sh <DATA_ROOT> [extra run_experiment.py args...]
#
# Example:
#   ./run_qwen3-480b.sh /Volumes/Rachna-HD
#   ./run_qwen3-480b.sh /Volumes/Rachna-HD --stages breaking-single breaking-multi
#   ./run_qwen3-480b.sh /Volumes/Rachna-HD --results-root /Volumes/RachnaPSSD
set -euo pipefail

DATA_ROOT="${1:?Usage: $0 <DATA_ROOT> [extra run_experiment.py args...]}"
shift || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/run_experiment.py" --model qwen3-480b --data-root "$DATA_ROOT" "$@"
