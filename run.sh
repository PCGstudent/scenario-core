#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

echo
echo "=== Tests ==="
python -m pytest

echo
echo "=== Final submitted model ==="
python -m xtra_takehome

echo
echo "=== Baseline vs challenger ==="
python -m xtra_takehome.compare_models

echo
echo "=== Multi-seed robustness ==="
python -m xtra_takehome.robustness

echo
echo "All checks and reports completed successfully."
