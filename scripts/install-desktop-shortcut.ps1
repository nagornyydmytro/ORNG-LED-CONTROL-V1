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
# Official mark → ICO: transparent bg + orange ring + black circle on top.
# Prefer newest cache-busting filename.
$iconCandidates = @(
    (Join-Path $RepoRoot "branding\orng-led-control-desktop-v5.ico")
    (Join-Path $RepoRoot "branding\orng-led-control-desktop-v4.ico")
    (Join-Path $RepoRoot "branding\orng-led-control-desktop.ico")
    (Join-Path $RepoRoot "branding\orng-led-control.ico")
)
$icon = $iconCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $icon) {
    throw "Missing branding desktop icon under branding\"
}
$sc.IconLocation = "$icon,0"
$sc.Save()

Write-Host "Created: $ShortcutPath" -ForegroundColor Green
Write-Host "Also:    $TargetCmd" -ForegroundColor Green
Write-Host ""
Write-Host "Double-click the desktop shortcut after plugging Type-C." -ForegroundColor Cyan
Write-Host "If IP 2.0.0.10 cannot be set, right-click -> Run as administrator once." -ForegroundColor Yellow
