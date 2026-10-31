$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed' }
}
& ./.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
Push-Location -LiteralPath frontend
try {
    npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed' }
} finally { Pop-Location }
& ./.venv/Scripts/python.exe -X utf8 -m pip freeze | Set-Content -LiteralPath 'backend/resolved-requirements.txt' -Encoding utf8
$env:PYTHONPATH = Join-Path $projectRoot 'backend'
& ./.venv/Scripts/python.exe -X utf8 -m marketlens init
if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed' }
Write-Host 'Ready. Run .\scripts\start.ps1, then open http://127.0.0.1:8000'
