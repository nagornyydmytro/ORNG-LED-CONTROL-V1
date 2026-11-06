#Requires -Version 5.1
<#
.SYNOPSIS
  Point the desktop shortcut at the managed app only after current.json and
  the release runtime exist. The previous shortcut is copied aside first.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$AppRoot = Join-Path $env:LOCALAPPDATA "ORNG-LED-CONTROL"
$CurrentPath = Join-Path $AppRoot "current.json"
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "ORNG LED CONTROL.lnk"
$Launcher = Join-Path $PSScriptRoot "orng-product-launch.ps1"

if (-not (Test-Path -LiteralPath $CurrentPath)) {
    Write-Host "No managed release (current.json). Desktop shortcut was not changed."
    exit 2
}

$current = Get-Content -Raw -LiteralPath $CurrentPath | ConvertFrom-Json
$python = Join-Path ([string]$current.path) "runtime\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = Join-Path ([string]$current.path) "runtime\bin\python.exe"
}
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "Release runtime python is missing. Desktop shortcut was not changed."
    exit 2
}

$destLauncher = Join-Path $AppRoot "bootstrap\orng-product-launch.ps1"
New-Item -ItemType Directory -Force -Path (Split-Path $destLauncher) | Out-Null
Copy-Item -LiteralPath $Launcher -Destination $destLauncher -Force

if (Test-Path -LiteralPath $ShortcutPath) {
    $backupDir = Join-Path $Desktop "ORNG-LED-CONTROL-shortcut-backup"
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Copy-Item -LiteralPath $ShortcutPath -Destination (Join-Path $backupDir "ORNG LED CONTROL-$stamp.lnk") -Force
}

$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($ShortcutPath)
$sc.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$destLauncher`""
$sc.WorkingDirectory = $AppRoot
$sc.WindowStyle = 7
$sc.Description = "ORNG LED CONTROL"
$sc.Save()
Write-Host "Shortcut now opens the managed app."
exit 0
