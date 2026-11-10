#!/usr/bin/env bash
# Finds, applies and tracks jobs automatically, starting a new round 1 minute after each one ends.
# Daily limits in config/profile.toml still apply. Press Ctrl+C to stop.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi
python -m jobagent run --auto-apply --loop-minutes 1
