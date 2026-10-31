param([ValidateSet('init','refresh','scan','cycle','report','weekly','health')][string]$Command = 'cycle')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONPATH = Join-Path $projectRoot 'backend'
& ./.venv/Scripts/python.exe -X utf8 -m marketlens $Command
if ($LASTEXITCODE -ne 0) { throw "Research command failed: $Command" }
