#Requires -Version 5.1
<#
.SYNOPSIS
  Starts backend API and Vite frontend for local development.
#>
[CmdletBinding()]
param(
    [string]$ApiHost = "127.0.0.1",
    [int]$ApiPort = 8000,
    [string]$ViteHost = "127.0.0.1",
    [int]$VitePort = 5173
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
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
    throw "Frontend dependencies missing. Run .\scripts\bootstrap.ps1 first."
}

function Test-PortInUse {
    param([int]$BindPort)
    try {
        $listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalPort -eq $BindPort }
        return [bool]$listeners
    }
    catch {
        $lines = netstat -ano | Select-String -Pattern ":$BindPort\s+.*LISTENING"
        return [bool]$lines
    }
}

if (Test-PortInUse -BindPort $ApiPort) {
    Write-Host "ERROR: API port $ApiPort is already in use." -ForegroundColor Red
    Write-Host "Stop the existing process before starting run-dev.ps1." -ForegroundColor Yellow
    exit 3
}
if (Test-PortInUse -BindPort $VitePort) {
    Write-Host "ERROR: Vite port $VitePort is already in use." -ForegroundColor Red
    Write-Host "Stop the existing process before starting run-dev.ps1." -ForegroundColor Yellow
    exit 3
}

Write-Host "Starting API on http://${ApiHost}:${ApiPort}/api/health"
Write-Host "Dev UI will be on http://${ViteHost}:${VitePort}/ (not the API port)."
$api = Start-Process -FilePath $Python -ArgumentList @(
    "-m", "uvicorn", "orng_led.main:app",
    "--host", $ApiHost,
    "--port", "$ApiPort",
    "--reload"
) -PassThru -NoNewWindow

$viteCmd = "npm run dev -- --host $ViteHost --port $VitePort"
Write-Host "Starting Vite on http://${ViteHost}:${VitePort}/"
$vite = Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-Command",
    "Set-Location -LiteralPath '$FrontendDir'; $viteCmd"
) -PassThru -NoNewWindow

Write-Host "Press Ctrl+C to stop both processes."
try {
    Wait-Process -Id @($api.Id, $vite.Id)
}
finally {
    foreach ($proc in @($api, $vite)) {
        if ($null -ne $proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
