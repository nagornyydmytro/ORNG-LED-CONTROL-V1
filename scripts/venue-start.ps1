#Requires -Version 5.1
<#
.SYNOPSIS
  One-click venue boot: wait for Type-C Art-Net NIC, start server,
  Blackout + activate Art-Net + Arm, then open Chrome on the control UI.

.DESCRIPTION
  After success you only need to turn Blackout OFF in the UI.
  Blackout stays ON through the whole boot so lights stay dark.
#>
[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [string]$LaptopIp = "2.0.0.10",
    [string]$ControllerIp = "2.0.0.11",
    [int]$PrefixLength = 8,
    [string]$PreferredAdapter = "Ethernet 2",
    [int]$NetworkTimeoutSec = 90,
    [int]$HealthTimeoutSec = 120,
    [switch]$SkipBuild,
    [switch]$NoChrome
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BaseUrl = "http://${HostAddress}:${Port}"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$RunScript = Join-Path $PSScriptRoot "run.ps1"
$LogDir = Join-Path $RepoRoot ".venue-logs"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogFile = Join-Path $LogDir "venue-start-$Stamp.log"

function Write-Step {
    param([string]$Message, [string]$Color = "Cyan")
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    Write-Host $line -ForegroundColor $Color
    Add-Content -LiteralPath $LogFile -Value $line -ErrorAction SilentlyContinue
}

function Invoke-JsonPost {
    param(
        [string]$Path,
        [hashtable]$Body = @{}
    )
    $uri = "$BaseUrl$Path"
    $json = $Body | ConvertTo-Json -Compress
    return Invoke-RestMethod -Method Post -Uri $uri -ContentType "application/json" -Body $json -TimeoutSec 10
}

function Get-ArtNetAdapter {
    param([string]$PreferName)
    $up = Get-NetAdapter -ErrorAction SilentlyContinue |
        Where-Object { $_.Status -eq "Up" }
    if (-not $up) { return $null }

    if ($PreferName) {
        $named = $up | Where-Object { $_.Name -eq $PreferName } | Select-Object -First 1
        if ($named) { return $named }
    }

    foreach ($adapter in $up) {
        $addrs = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
        if ($addrs | Where-Object { $_.IPAddress -like "2.*" }) {
            return $adapter
        }
    }

    $usb = $up | Where-Object {
        $_.InterfaceDescription -match "Realtek.*USB|USB.*Ethernet|USB.*GbE|AX88179|RTL815"
    } | Select-Object -First 1
    if ($usb) { return $usb }

    return $null
}

function Ensure-ArtNetNetwork {
    Write-Step "Waiting for Type-C / Art-Net adapter (up to ${NetworkTimeoutSec}s) ..."
    $deadline = (Get-Date).AddSeconds($NetworkTimeoutSec)
    $adapter = $null
    while ((Get-Date) -lt $deadline) {
        $adapter = Get-ArtNetAdapter -PreferName $PreferredAdapter
        if ($adapter) { break }
        Start-Sleep -Seconds 2
    }
    if (-not $adapter) {
        throw "Art-Net NIC not found. Plug Type-C Ethernet, wait for link, retry."
    }
    Write-Step ("NIC: {0} ({1})" -f $adapter.Name, $adapter.InterfaceDescription) "Green"

    $addrs = @(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue)
    $hasLaptop = $addrs | Where-Object { $_.IPAddress -eq $LaptopIp }

    if (-not $hasLaptop) {
        Write-Step "Configuring $LaptopIp/$PrefixLength on '$($adapter.Name)' ..."
        try {
            foreach ($old in ($addrs | Where-Object { $_.IPAddress -like "2.*" -and $_.IPAddress -ne $LaptopIp })) {
                Remove-NetIPAddress -IPAddress $old.IPAddress -InterfaceIndex $adapter.ifIndex -Confirm:$false -ErrorAction SilentlyContinue
            }
            New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress $LaptopIp -PrefixLength $PrefixLength -ErrorAction Stop | Out-Null
            Write-Step "Laptop IP set to $LaptopIp/$PrefixLength" "Green"
        }
        catch {
            $hasSubnet = $addrs | Where-Object { $_.IPAddress -like "2.*" }
            if (-not $hasSubnet) {
                throw ("Cannot set $LaptopIp on '$($adapter.Name)'. Run as Administrator once, or set static IP manually. Detail: {0}" -f $_.Exception.Message)
            }
            Write-Step ("Could not force $LaptopIp; using existing 2.x address. Run as Admin to force it. {0}" -f $_.Exception.Message) "Yellow"
        }
    }
    else {
        Write-Step "Laptop already has $LaptopIp" "Green"
    }

    Write-Step "Controller target: $ControllerIp (Art-Net UDP 6454)"
    try {
        $ping = Test-Connection -ComputerName $ControllerIp -Count 1 -Quiet -ErrorAction SilentlyContinue
        if ($ping) {
            Write-Step "Controller $ControllerIp answers ping" "Green"
        }
        else {
            Write-Step "No ICMP from $ControllerIp (often normal for Art-Net nodes) - continuing" "Yellow"
        }
    }
    catch {
        Write-Step "Ping skipped - continuing" "Yellow"
    }
}

function Stop-PortListener {
    param([int]$BindPort)
    try {
        $listeners = Get-NetTCPConnection -LocalPort $BindPort -State Listen -ErrorAction SilentlyContinue
        foreach ($item in @($listeners)) {
            if ($item.OwningProcess) {
                Write-Step "Stopping old process on port $BindPort (PID $($item.OwningProcess))" "Yellow"
                Stop-Process -Id $item.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        }
    }
    catch { }
    Start-Sleep -Seconds 1
}

function Test-ApiReady {
    try {
        $health = Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 2
        return [bool]$health.ready
    }
    catch {
        return $false
    }
}

function Start-OrngServer {
    if (Test-ApiReady) {
        Write-Step "Server already ready at $BaseUrl" "Green"
        return
    }

    if (-not (Test-Path $Python)) {
        throw "Virtualenv missing. Run .\scripts\bootstrap.ps1 first."
    }
    if (-not (Test-Path $RunScript)) {
        throw "Missing $RunScript"
    }

    Stop-PortListener -BindPort $Port

    $dist = Join-Path $RepoRoot "frontend\dist\index.html"
    $useSkip = $SkipBuild -or (Test-Path $dist)

    Write-Step "Starting ORNG server ..."
    $argList = @(
        "-NoProfile"
        "-ExecutionPolicy", "Bypass"
        "-File", $RunScript
        "-HostAddress", $HostAddress
        "-Port", "$Port"
    )
    if ($useSkip) { $argList += "-SkipBuild" }

    Start-Process -FilePath "powershell.exe" -ArgumentList $argList -WorkingDirectory $RepoRoot -WindowStyle Minimized | Out-Null

    $deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-ApiReady) {
            Write-Step "API ready" "Green"
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "Server did not become ready within ${HealthTimeoutSec}s. See other PowerShell window / $LogFile"
}

function Get-AppState {
    return Invoke-RestMethod -Uri "$BaseUrl/api/state" -TimeoutSec 5
}

function Initialize-ShowSafe {
    $state = Get-AppState
    $alreadyReady = (
        [bool]$state.engine.blackout -and
        [bool]$state.output.armed -and
        ([bool]$state.output.udp_active -or [bool]$state.output.network_allowed) -and
        ([string]$state.output.transport -eq "artnet")
    )
    if ($alreadyReady) {
        Write-Step "Already READY (Art-Net + Armed + Blackout) - skipping re-activate" "Green"
        return
    }

    Write-Step "Face OFF + Blackout ON ..."
    try {
        Invoke-JsonPost -Path "/api/commands/face" -Body @{
            enabled = $false
            client_command_id = "venue-face-off-$Stamp"
        } | Out-Null
    }
    catch {
        Write-Step ("Face OFF skipped: {0}" -f $_.Exception.Message) "Yellow"
    }

    Invoke-JsonPost -Path "/api/commands/blackout" -Body @{
        enabled = $true
        client_command_id = "venue-blackout-$Stamp"
    } | Out-Null

    # Activate requires disarmed state.
    $state = Get-AppState
    if ([bool]$state.output.armed) {
        Write-Step "Disarming before Art-Net (re)activate ..." "Yellow"
        Invoke-JsonPost -Path "/api/output/disarm" -Body @{
            client_command_id = "venue-disarm-$Stamp"
        } | Out-Null
        Invoke-JsonPost -Path "/api/commands/blackout" -Body @{
            enabled = $true
            client_command_id = "venue-blackout2-$Stamp"
        } | Out-Null
    }

    $state = Get-AppState
    $needActivate = -not (
        ([string]$state.output.transport -eq "artnet") -and
        ([bool]$state.output.udp_active -or [bool]$state.output.network_allowed)
    )
    if ($needActivate) {
        Write-Step "Activating Art-Net ..."
        try {
            Invoke-JsonPost -Path "/api/output/activate-artnet" -Body @{
                confirmed = $true
                client_command_id = "venue-activate-$Stamp"
            } | Out-Null
        }
        catch {
            $detail = $_.Exception.Message
            try {
                $resp = $_.ErrorDetails.Message
                if ($resp) { $detail = $resp }
            }
            catch { }
            throw "Art-Net activate failed: $detail"
        }
    }
    else {
        Write-Step "Art-Net already active" "Green"
    }

    $state = Get-AppState
    if (-not [bool]$state.output.armed) {
        Write-Step "Arming output (Blackout stays ON) ..."
        try {
            Invoke-JsonPost -Path "/api/output/arm" -Body @{
                confirmed = $true
                client_command_id = "venue-arm-$Stamp"
            } | Out-Null
        }
        catch {
            $detail = $_.Exception.Message
            try {
                $blockers = Invoke-RestMethod -Uri "$BaseUrl/api/output/arm-blockers" -TimeoutSec 5
                if ($blockers.blockers) {
                    $detail = ($blockers.blockers -join "; ")
                }
            }
            catch { }
            throw "Arm failed: $detail"
        }
    }
    else {
        Write-Step "Already armed" "Green"
    }

    $state = Get-AppState
    $armed = [bool]$state.output.armed
    $blackout = [bool]$state.engine.blackout
    $udp = [bool]$state.output.udp_active
    $net = [bool]$state.output.network_allowed

    if (-not $blackout) { throw "Blackout is OFF after boot - aborting (unexpected)." }
    if (-not $armed) { throw "Arm failed - output not armed." }
    if (-not ($udp -or $net)) {
        Write-Step "Warning: UDP/network flag not clearly true in state - check Setup" "Yellow"
    }

    Write-Step ("READY: Art-Net up, Armed=true, Blackout=ON, target={0}" -f $ControllerIp) "Green"
}

function Open-AdminUi {
    if ($NoChrome) { return }
    $url = "$BaseUrl/"
    Write-Step "Opening Chrome: $url"
    $chromeCandidates = @(
        "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe"
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
        "${env:LocalAppData}\Google\Chrome\Application\chrome.exe"
    )
    $chrome = $chromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($chrome) {
        Start-Process -FilePath $chrome -ArgumentList @("--new-window", $url) | Out-Null
    }
    else {
        Write-Step "Chrome not found - opening default browser" "Yellow"
        Start-Process $url | Out-Null
    }
}

# --- main ---
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Write-Host ""
Write-Host "=== ORNG LED - venue start ===" -ForegroundColor White
Write-Host "Log: $LogFile"
Write-Host ""

try {
    Set-Location $RepoRoot
    Ensure-ArtNetNetwork
    Start-OrngServer
    Initialize-ShowSafe
    Open-AdminUi
    Write-Host ""
    Write-Host "All set. In the browser: turn Blackout OFF, then you are live." -ForegroundColor Green
    Write-Host "Server runs in a separate PowerShell window - do not close it." -ForegroundColor DarkGray
    Write-Host ""
    exit 0
}
catch {
    Write-Step $_.Exception.Message "Red"
    Write-Host ""
    Write-Host "Boot failed. Log: $LogFile" -ForegroundColor Red
    Write-Host "Check Type-C / Ethernet 2 / IP $LaptopIp and controller $ControllerIp." -ForegroundColor Yellow
    Write-Host ""
    if (-not $env:ORNG_VENUE_NO_PAUSE) {
        Read-Host "Press Enter to close"
    }
    exit 1
}
