# Offline validation wrapper - run from repo root customsAI/
# Requires .venv with backend dependencies installed.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Missing $python - create venv and: pip install -r backend/requirements.txt"
}

& $python -m backend.scripts.run_validation @args
exit $LASTEXITCODE
