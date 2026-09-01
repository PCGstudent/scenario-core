$ErrorActionPreference = "Stop"

if (-Not (Test-Path ".venv")) {
    python -m venv .venv
}

& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

Write-Host "`n=== Tests ==="
python -m pytest

Write-Host "`n=== Final submitted model ==="
python -m xtra_takehome

Write-Host "`n=== Baseline vs challenger ==="
python -m xtra_takehome.compare_models

Write-Host "`n=== Multi-seed robustness ==="
python -m xtra_takehome.robustness

Write-Host "`nAll checks and reports completed successfully."
