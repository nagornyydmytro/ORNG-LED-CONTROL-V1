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

function Test-PortInUse {
    param([string]$BindHost, [int]$BindPort)
    try {
        $listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalPort -eq $BindPort }
        if (-not $listeners) {
            return $false
        }
        foreach ($item in $listeners) {
            $addr = [string]$item.LocalAddress
            if ($addr -eq $BindHost -or $addr -eq "0.0.0.0" -or $addr -eq "::" -or $addr -eq "*") {
                return $true
            }
        }
        return $false
    }
    catch {
        # Fallback when Get-NetTCPConnection is unavailable.
        $lines = netstat -ano | Select-String -Pattern ":$BindPort\s+.*LISTENING"
        return [bool]$lines
    }
}

if (Test-PortInUse -BindHost $HostAddress -BindPort $Port) {
    $owner = netstat -ano | Select-String -Pattern ":$Port\s+.*LISTENING" | Select-Object -First 3
    Write-Host ""
    Write-Host "ERROR: Port $Port is already in use on $HostAddress." -ForegroundColor Red
    Write-Host "Another ORNG LED CONTROL / uvicorn instance is likely still running." -ForegroundColor Yellow
    Write-Host "Stop the existing process (Task Manager or Stop-Process) and retry." -ForegroundColor Yellow
    Write-Host "Do not start a second conflicting instance on the same port." -ForegroundColor Yellow
    if ($owner) {
        Write-Host "Listener detail:" -ForegroundColor Yellow
        $owner | ForEach-Object { Write-Host "  $_" }
    }
    Write-Host ""
    exit 3
}

$FrontendDir = Join-Path $RepoRoot "frontend"
$DistIndex = Join-Path $FrontendDir "dist\index.html"
$DistAssets = Join-Path $FrontendDir "dist\assets"

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
if (-not (Test-Path $DistAssets)) {
    throw "frontend\dist\assets missing after build. Re-run without -SkipBuild."
}

# Verify hashed assets referenced by index.html exist on disk.
$indexHtml = Get-Content -LiteralPath $DistIndex -Raw -Encoding UTF8
$assetRefs = [regex]::Matches($indexHtml, '/assets/([^"\s>]+)')
foreach ($match in $assetRefs) {
    $name = $match.Groups[1].Value
    $path = Join-Path $DistAssets $name
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Referenced asset missing: assets\$name. Re-run .\scripts\run.ps1 without -SkipBuild."
    }
}

Write-Host "Starting ORNG LED CONTROL at http://${HostAddress}:${Port}/"
Write-Host "Health: http://${HostAddress}:${Port}/api/health"
& $Python -m uvicorn orng_led.main:app --host $HostAddress --port $Port
