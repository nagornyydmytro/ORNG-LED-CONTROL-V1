#Requires -Version 5.1
<#
.SYNOPSIS
  Creates the Python virtualenv and installs backend + frontend dependencies.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Assert-Command {
    param([Parameter(Mandatory)][string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

Assert-Command python
Assert-Command npm

$PythonVersion = & python --version 2>&1
Write-Host "Using $PythonVersion"
if ($PythonVersion -notmatch "Python 3\.12") {
    Write-Warning "Canon requires Python 3.12. Detected: $PythonVersion"
}

$VenvPath = Join-Path $RepoRoot ".venv"
if (-not (Test-Path (Join-Path $VenvPath "Scripts\python.exe"))) {
    Write-Host "Creating virtual environment at .venv ..."
    & python -m venv $VenvPath
}

$Python = Join-Path $VenvPath "Scripts\python.exe"

Write-Host "Installing backend (editable + dev extras) ..."
& $Python -m pip install --upgrade pip
Push-Location (Join-Path $RepoRoot "backend")
try {
    & $Python -m pip install -e ".[dev]"
}
finally {
    Pop-Location
}

Write-Host "Installing frontend dependencies ..."
Push-Location (Join-Path $RepoRoot "frontend")
try {
    & npm install
}
finally {
    Pop-Location
}

Write-Host "Bootstrap complete."
Write-Host "Next: .\scripts\run.ps1   (production-like)"
Write-Host "  or: .\scripts\run-dev.ps1 (API + Vite)"
