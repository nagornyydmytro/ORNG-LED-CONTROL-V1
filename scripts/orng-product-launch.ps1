#Requires -Version 5.1
<#
.SYNOPSIS
  Open the managed ORNG desk. Does not configure the network and does not
  stop another program. A second launch reuses the running desk.
#>
[CmdletBinding()]
param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$AppRoot = if ($env:ORNG_APP_ROOT) { $env:ORNG_APP_ROOT } else { Join-Path $env:LOCALAPPDATA "ORNG-LED-CONTROL" }
$DataRoot = if ($env:ORNG_CONFIG_ROOT) { $env:ORNG_CONFIG_ROOT } else { Join-Path $AppRoot "data" }
$CurrentPath = Join-Path $AppRoot "current.json"
$BaseUrl = "http://127.0.0.1:$Port/"

function Open-Desk {
    Start-Process $BaseUrl | Out-Null
}

function Test-OurHealth {
    param($Health)
    if (-not $Health) { return $false }
    if ($Health.service -ne "orng-led-control") { return $false }
    if (-not $Health.ready) { return $false }
    if (-not $Health.instance_id) { return $false }
    $instancePath = Join-Path $DataRoot "instance.json"
    if (-not (Test-Path -LiteralPath $instancePath)) { return $false }
    $saved = Get-Content -Raw -LiteralPath $instancePath | ConvertFrom-Json
    return [string]$saved.instance_id -eq [string]$Health.instance_id
}

function Test-OurCommandLine {
    param([string]$CommandLine)
    if (-not $CommandLine) { return $false }
    return $CommandLine -match [regex]::Escape($AppRoot) -and $CommandLine -match "updater_helper|orng_led"
}

$mutex = New-Object System.Threading.Mutex($false, "Local\ORNG-LED-CONTROL")
$ownsMutex = $false
try {
    $ownsMutex = $mutex.WaitOne(0)
}
catch {
    $ownsMutex = $false
}

try {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        if (Test-OurHealth $health) {
            Open-Desk
            exit 0
        }
    }
    catch { }

    if (-not $ownsMutex) {
        Write-Host "ORNG is already starting. The existing window will be used."
        exit 0
    }

    if (-not (Test-Path -LiteralPath $CurrentPath)) {
        Write-Host "Installed ORNG app is not staged yet (no current.json)."
        Write-Host "The desktop shortcut is not switched until a signed release is in place."
        exit 2
    }

    $current = Get-Content -Raw -LiteralPath $CurrentPath | ConvertFrom-Json
    $release = [string]$current.path
    $python = Join-Path $release "runtime\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        $python = Join-Path $release "runtime\bin\python.exe"
    }
    $helper = Join-Path $AppRoot "bootstrap\updater_helper.py"
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $helper)) {
        Write-Host "Installed runtime is incomplete. Shortcut stays on the previous launcher."
        exit 2
    }

    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($item in $listeners) {
        $owner = [int]$item.OwningProcess
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue
        $cmd = [string]$proc.CommandLine
        if (-not (Test-OurCommandLine $cmd)) {
            Write-Host "Port $Port is used by another program (PID $owner). It was left running."
            exit 3
        }
    }

    $env:ORNG_APP_ROOT = $AppRoot
    $env:ORNG_CONFIG_ROOT = $DataRoot
    $env:ORNG_INSTALL_MODE = "managed"
    $env:ORNG_CLOUD_DEVICE_MODE = "desktop"
    $env:ORNG_DESKTOP_OUTPUT = "raspberry"
    $env:ORNG_OUTPUT_POLICY = "manual"

    Start-Process -FilePath $python -ArgumentList @(
        $helper, "run", "--host", "127.0.0.1", "--port", "$Port"
    ) -WorkingDirectory $AppRoot -WindowStyle Hidden | Out-Null

    $deadline = (Get-Date).AddSeconds(40)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
            if (Test-OurHealth $health) {
                Open-Desk
                exit 0
            }
        }
        catch { }
        Start-Sleep -Seconds 1
    }
    Write-Host "The installed app did not become ready. No other process was stopped."
    exit 4
}
finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() | Out-Null }
    $mutex.Dispose()
}
