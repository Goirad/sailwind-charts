#!/usr/bin/env bash
# Create a virtual environment with the pinned dependencies, next to this script.
#   ./setup_env.sh && ./venv/bin/python -I sailwind_charts.py selftest
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt
echo "ready. Try:"
echo "  ./venv/bin/python -I sailwind_charts.py list"
echo "  ./venv/bin/python -I sailwind_charts.py selftest"
