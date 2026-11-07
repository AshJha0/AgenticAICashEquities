# Build .venv from the hashed lock on Windows. Usage: .\scripts\bootstrap.ps1 [-Python py]
param([string]$Python = "py")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
& $Python -m venv .venv
if (-not $?) { throw "venv creation failed" }
$vpy = ".\.venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip
& $vpy -m pip install --require-hashes -r requirements.lock
if (-not $?) { throw "locked install failed" }
& $vpy -m pip install -e . --no-deps
& $vpy -m pytest --co -q | Select-Object -Last 1
Write-Output "Bootstrap done. Activate with: .\.venv\Scripts\Activate.ps1"
