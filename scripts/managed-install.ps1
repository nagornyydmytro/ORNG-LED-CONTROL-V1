# Managed install bootstrap for Windows (no Node.js required at runtime).
# Creates app/data layout under %LOCALAPPDATA%\ORNG-LED-CONTROL\
# Does not copy secrets into release directories. Does not require admin.
# Does NOT point current.json at a git checkout.
# PowerShell 5.1: write JSON/CMD without UTF-8 BOM.

$ErrorActionPreference = "Stop"
$AppRoot = if ($env:ORNG_APP_ROOT) { $env:ORNG_APP_ROOT } else { Join-Path $env:LOCALAPPDATA "ORNG-LED-CONTROL" }
$DataRoot = if ($env:ORNG_CONFIG_ROOT) { $env:ORNG_CONFIG_ROOT } else { Join-Path $AppRoot "data" }

New-Item -ItemType Directory -Force -Path (Join-Path $AppRoot "bootstrap") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $AppRoot "releases") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $AppRoot "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot "scenes") | Out-Null

$RepoRoot = Split-Path -Parent $PSScriptRoot
foreach ($name in @("updater_helper.py", "helper_lib.py")) {
  $HelperSrc = Join-Path $RepoRoot "bootstrap\$name"
  if (Test-Path $HelperSrc) {
    Copy-Item -Force $HelperSrc (Join-Path $AppRoot "bootstrap\$name")
  }
}

$CurrentPath = Join-Path $AppRoot "current.json"
if (Test-Path $CurrentPath) {
  Write-Host "current.json already present - leaving unchanged"
} else {
  Write-Host "No current.json yet. Stage a signed release under $AppRoot\releases\ first."
  Write-Host "Installer never points current.json at the git repository."
}

# Example launcher stub (not registered as a Windows service here).
# Avoid @"..."@ here-strings: @echo confuses PS 5.1 parser.
$Launcher = Join-Path $AppRoot "bootstrap\run-orng.cmd"
$LauncherBody = (
  '@echo off' + [Environment]::NewLine +
  ('set ORNG_APP_ROOT={0}' -f $AppRoot) + [Environment]::NewLine +
  ('set ORNG_CONFIG_ROOT={0}' -f $DataRoot) + [Environment]::NewLine +
  'set ORNG_INSTALL_MODE=managed' + [Environment]::NewLine +
  'python "%ORNG_APP_ROOT%\bootstrap\updater_helper.py" run --host 127.0.0.1 --port 8000' + [Environment]::NewLine
)
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($Launcher, $LauncherBody, $utf8NoBom)

Write-Host "Managed layout ready:"
Write-Host "  app:  $AppRoot"
Write-Host "  data: $DataRoot"
Write-Host "Launcher stub: $Launcher (not registered as a service)"
