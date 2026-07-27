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

Write-Host "Starting API on http://${ApiHost}:${ApiPort}/api/health"
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
    "Set-Location '$FrontendDir'; $viteCmd"
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
