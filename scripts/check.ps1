#Requires -Version 5.1
<#
.SYNOPSIS
  Runs formatter/lint/typecheck/test commands for HOME acceptance.
#>
[CmdletBinding()]
param(
    [switch]$SkipFrontendLint
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Virtualenv not found. Run .\scripts\bootstrap.ps1 first."
}

Write-Host "== backend ruff format (check) =="
& $Python -m ruff format --check (Join-Path $RepoRoot "backend")
if ($LASTEXITCODE -ne 0) { throw "ruff format check failed" }

Write-Host "== backend ruff lint =="
& $Python -m ruff check (Join-Path $RepoRoot "backend")
if ($LASTEXITCODE -ne 0) { throw "ruff check failed" }

Write-Host "== frontend typecheck =="
Push-Location (Join-Path $RepoRoot "frontend")
try {
    & npm run typecheck
    if ($LASTEXITCODE -ne 0) { throw "frontend typecheck failed" }

    if (-not $SkipFrontendLint) {
        Write-Host "== frontend lint =="
        & npm run lint
        if ($LASTEXITCODE -ne 0) { throw "frontend lint failed" }
    }

    Write-Host "== frontend unit tests =="
    & npm run test
    if ($LASTEXITCODE -ne 0) { throw "frontend tests failed" }

    Write-Host "== frontend production build =="
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
}
finally {
    Pop-Location
}

# Pytest after frontend build so SPA integration smoke can see frontend/dist.
Write-Host "== backend pytest =="
Push-Location (Join-Path $RepoRoot "backend")
try {
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
}
finally {
    Pop-Location
}

Write-Host "All check commands passed."
