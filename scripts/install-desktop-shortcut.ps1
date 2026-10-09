#Requires -Version 5.1
<#
.SYNOPSIS
  Creates a Desktop shortcut that runs venue-start.ps1.
#>
[CmdletBinding()]
param(
    [string]$ShortcutName = "ORNG LED CONTROL.lnk"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$TargetCmd = Join-Path $RepoRoot "ORNG-LED-CONTROL.cmd"
$VenuePs1 = Join-Path $PSScriptRoot "venue-start.ps1"

if (-not (Test-Path $VenuePs1)) {
    throw "Missing $VenuePs1"
}

$cmdLines = @(
    "@echo off"
    "title ORNG LED CONTROL"
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
# Prefer the opaque desktop ICO (black background). New filename busts Win icon cache.
$icon = Join-Path $RepoRoot "branding\orng-led-control-desktop.ico"
if (-not (Test-Path -LiteralPath $icon)) {
    $icon = Join-Path $RepoRoot "branding\orng-led-control.ico"
}
if (-not (Test-Path -LiteralPath $icon)) {
    throw "Missing branding icon: $icon"
}
$sc.IconLocation = "$icon,0"
$sc.Save()

Write-Host "Created: $ShortcutPath" -ForegroundColor Green
Write-Host "Also:    $TargetCmd" -ForegroundColor Green
Write-Host ""
Write-Host "Double-click the desktop shortcut after plugging Type-C." -ForegroundColor Cyan
Write-Host "If IP 2.0.0.10 cannot be set, right-click -> Run as administrator once." -ForegroundColor Yellow
