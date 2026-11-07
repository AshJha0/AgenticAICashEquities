#!/usr/bin/env sh
# Build .venv from the hashed lock (Linux / macOS / Git Bash). Usage: scripts/bootstrap.sh [python]
set -eu
PY="${1:-python3}"
cd "$(dirname "$0")/.."
"$PY" -m venv .venv
if [ -x .venv/bin/python ]; then VPY=.venv/bin/python; else VPY=.venv/Scripts/python.exe; fi
"$VPY" -m pip install --upgrade pip
"$VPY" -m pip install --require-hashes -r requirements.lock
"$VPY" -m pip install -e . --no-deps
"$VPY" -m pytest --co -q | tail -n 1
echo "Bootstrap done. Activate with: . .venv/bin/activate (or .venv/Scripts/activate on Git Bash)"
