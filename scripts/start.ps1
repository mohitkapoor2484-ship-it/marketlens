$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Run scripts/setup.ps1 first' }
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { throw 'Build frontend first: cd frontend; npm run build' }
& ./.venv/Scripts/python.exe -X utf8 -m uvicorn marketlens.api:app --app-dir backend --host 127.0.0.1 --port 8000 --workers 1
