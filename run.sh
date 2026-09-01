#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

# Call the interpreter directly rather than relying on activation, so the steps
# below cannot silently run against the wrong Python.
VENV_PYTHON=".venv/bin/python"
if [ ! -x "$VENV_PYTHON" ]; then
  echo "Expected a virtual environment interpreter at $VENV_PYTHON" >&2
  exit 1
fi

"$VENV_PYTHON" -m pip install --upgrade pip --quiet
"$VENV_PYTHON" -m pip install -e ".[dev]" --quiet

echo
echo "=== Tests ==="
"$VENV_PYTHON" -m pytest

echo
echo "=== Final submitted model ==="
"$VENV_PYTHON" -m xtra_takehome

echo
echo "=== Baseline vs challenger ==="
"$VENV_PYTHON" -m xtra_takehome.compare_models

echo
echo "=== Multi-seed robustness ==="
"$VENV_PYTHON" -m xtra_takehome.robustness

echo
echo "All checks and reports completed successfully."
