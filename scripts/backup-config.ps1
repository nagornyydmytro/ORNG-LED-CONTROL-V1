#Requires -Version 5.1
<#
.SYNOPSIS
  Copies the versioned config tree into a timestamped local backup folder.
  Backups are local-only and must not be committed.
#>
[CmdletBinding()]
param(
    [string]$DestinationRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$ConfigDir = Join-Path $RepoRoot "config"
if (-not (Test-Path -LiteralPath $ConfigDir)) {
    throw "config/ directory not found."
}

if (-not $DestinationRoot) {
    $DestinationRoot = Join-Path $RepoRoot "backups"
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Target = Join-Path $DestinationRoot "config-$stamp"
New-Item -ItemType Directory -Path $Target -Force | Out-Null
Copy-Item -LiteralPath $ConfigDir -Destination (Join-Path $Target "config") -Recurse -Force

Write-Host "Config backup written to: $Target"
Write-Host "Restore manually by copying YAML files back into config/ (keep schema_version)."
