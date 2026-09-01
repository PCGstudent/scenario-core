$ErrorActionPreference = "Stop"

# $ErrorActionPreference does not apply to native executables: a failing python or
# pip call sets $LASTEXITCODE and otherwise continues silently. Without this the
# wrapper would report success after a broken install and empty reports.
function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )
    Write-Host "`n=== $Name ==="
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE."
    }
}

if (-Not (Test-Path ".venv")) {
    Invoke-Step "Create virtual environment" { python -m venv .venv }
}

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-Not (Test-Path $Python)) {
    throw "Expected a virtual environment interpreter at $Python."
}

Invoke-Step "Upgrade pip" { & $Python -m pip install --upgrade pip --quiet }
Invoke-Step "Install pinned dependencies" { & $Python -m pip install -e ".[dev]" --quiet }
Invoke-Step "Tests" { & $Python -m pytest }
Invoke-Step "Final submitted model" { & $Python -m xtra_takehome }
Invoke-Step "Baseline vs challenger" { & $Python -m xtra_takehome.compare_models }
Invoke-Step "Multi-seed robustness" { & $Python -m xtra_takehome.robustness }

Write-Host "`nAll checks and reports completed successfully."
