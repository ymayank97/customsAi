$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& "$root/.venv/Scripts/python.exe" -m pytest backend/tests @args
exit $LASTEXITCODE
