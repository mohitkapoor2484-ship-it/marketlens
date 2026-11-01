$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$recordPath = Join-Path $projectRoot 'data/server-process.json'
if (-not (Test-Path -LiteralPath $recordPath)) { throw 'No background-server record. Use Ctrl+C in the console that started MarketLens.' }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.process_id)"
if (-not $serverProcess) { Write-Host 'Recorded MarketLens process is already stopped.'; exit 0 }
if ($serverProcess.CommandLine -notmatch 'marketlens\.api:app') { throw 'Recorded PID belongs to a different process; refusing to stop it.' }
$listener = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Where-Object { $_.RemotePort -eq 0 -and $_.OwningProcess -eq $record.process_id }
if (-not $listener) { throw 'Recorded process does not own the MarketLens port; refusing to stop it.' }
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/data/health' -TimeoutSec 10
if ($health.jobs | Where-Object { $_.status -eq 'running' -and [datetime]$_.started_at -gt (Get-Date).ToUniversalTime().AddMinutes(-10) }) { throw 'A research/upload job is running. Wait for it to finish, then stop the server.' }
Stop-Process -Id $record.process_id
Write-Host 'MarketLens background server stopped.'
