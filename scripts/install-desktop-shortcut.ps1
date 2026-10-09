#Requires -Version 5.1
<#
.SYNOPSIS
  Creates a Desktop shortcut that runs venue-start.ps1.
#>
[CmdletBinding()]
param(
    [string]$ShortcutName = "ORNG LED START.lnk"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$TargetCmd = Join-Path $RepoRoot "ORNG-LED-START.cmd"
$VenuePs1 = Join-Path $PSScriptRoot "venue-start.ps1"

if (-not (Test-Path $VenuePs1)) {
    throw "Missing $VenuePs1"
}

$cmdLines = @(
    "@echo off"
    "title ORNG LED venue start"
    "cd /d `"%~dp0`""
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"%~dp0scripts\venue-start.ps1`""
    "if errorlevel 1 pause"
)
$cmdLines | Set-Content -LiteralPath $TargetCmd -Encoding ASCII

$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop $ShortcutName

$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($ShortcutPath)
$sc.TargetPath = $TargetCmd
$sc.WorkingDirectory = $RepoRoot
$sc.WindowStyle = 1
$sc.Description = "ORNG LED CONTROL: Type-C Art-Net + server + Blackout + Arm + Chrome"
$icon = Join-Path $env:SystemRoot "System32\shell32.dll"
$sc.IconLocation = "$icon,137"
$sc.Save()

Write-Host "Created: $ShortcutPath" -ForegroundColor Green
Write-Host "Also:    $TargetCmd" -ForegroundColor Green
Write-Host ""
Write-Host "Double-click the desktop shortcut after plugging Type-C." -ForegroundColor Cyan
Write-Host "If IP 2.0.0.10 cannot be set, right-click -> Run as administrator once." -ForegroundColor Yellow
