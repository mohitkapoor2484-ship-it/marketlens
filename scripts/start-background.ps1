$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Run scripts/setup.ps1 first' }
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { throw 'Build the frontend first: npm run build in frontend' }
if (Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Where-Object RemotePort -eq 0) { throw 'Port 8000 is already in use. Stop the existing MarketLens console/server first.' }
New-Item -ItemType Directory -Path 'data' -Force | Out-Null
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
$serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-X', 'utf8', '-m', 'uvicorn', 'marketlens.api:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', '8000', '--workers', '1') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot 'data/server.stdout.log') -RedirectStandardError (Join-Path $projectRoot 'data/server.stderr.log') -PassThru
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $status = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/status' -TimeoutSec 2
        if ($status.app -eq 'MarketLens') {
            $listener = Get-NetTCPConnection -LocalPort 8000 | Where-Object RemotePort -eq 0 | Select-Object -First 1
            @{ process_id = $listener.OwningProcess; launcher_id = $serverProcess.Id; started_at = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath 'data/server-process.json' -Encoding utf8
            Write-Host 'MarketLens running at http://127.0.0.1:8000. Stop with scripts/stop-background.ps1. Logs: data/server.stderr.log'
            exit 0
        }
    } catch { }
}
throw 'Server did not become ready. Inspect data/server.stderr.log.'
