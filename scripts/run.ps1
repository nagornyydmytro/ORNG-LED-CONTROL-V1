#Requires -Version 5.1
<#
.SYNOPSIS
  Builds the frontend and starts the production-like local app (FastAPI + SPA).
#>
[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Virtualenv not found. Run .\scripts\bootstrap.ps1 first."
}

$FrontendDir = Join-Path $RepoRoot "frontend"
$DistIndex = Join-Path $FrontendDir "dist\index.html"

if (-not $SkipBuild -or -not (Test-Path $DistIndex)) {
    Write-Host "Building frontend production bundle ..."
    Push-Location $FrontendDir
    try {
        & npm run build
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend production build failed."
        }
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-Path $DistIndex)) {
    throw "frontend\dist\index.html missing after build."
}

Write-Host "Starting ORNG LED CONTROL at http://${HostAddress}:${Port}/"
Write-Host "Health: http://${HostAddress}:${Port}/api/health"
& $Python -m uvicorn orng_led.main:app --host $HostAddress --port $Port
