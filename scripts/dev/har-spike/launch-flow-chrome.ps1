[CmdletBinding()]
param(
    [string]$ProfileName = $env:GFLOW_CLI_PROFILE,
    [string]$ProfileDir,
    [string]$ProfileDirectory = "Default",
    [int]$Port = 9334,
    [string]$Url = "https://labs.google/fx/tools/flow?hl=en",
    [switch]$NoNavigate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Find-Chrome {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    $cmd = Get-Command chrome.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    throw "Google Chrome was not found. Pass a Chrome path manually by editing this script, or install Chrome."
}

function Get-CandidateGFlowHomes {
    $candidateHomes = @()

    if ($env:GFLOW_CLI_HOME) {
        $candidateHomes += $env:GFLOW_CLI_HOME
    }

    if ($env:LOCALAPPDATA) {
        $candidateHomes += (Join-Path $env:LOCALAPPDATA "gflow-cli")
        $candidateHomes += (Join-Path $env:LOCALAPPDATA "ffroliva\gflow-cli")
    }

    if ($HOME) {
        $candidateHomes += (Join-Path $HOME ".local\share\gflow-cli")
    }

    return $candidateHomes | Where-Object { $_ } | Select-Object -Unique
}

function Resolve-GFlowProfileDir {
    param(
        [string]$Name,
        [string]$ExplicitDir
    )

    if ($ExplicitDir) {
        $resolved = Resolve-Path -LiteralPath $ExplicitDir -ErrorAction Stop
        return $resolved.Path
    }

    if (-not $Name) {
        $Name = "default"
    }

    foreach ($candidateHome in Get-CandidateGFlowHomes) {
        $candidate = Join-Path $candidateHome "profile_$Name"
        if (Test-Path -LiteralPath $candidate) {
            $resolved = Resolve-Path -LiteralPath $candidate -ErrorAction Stop
            return $resolved.Path
        }
    }

    $searched = (Get-CandidateGFlowHomes | ForEach-Object { Join-Path $_ "profile_$Name" }) -join "`n  "
    throw "Could not find gflow profile '$Name'. Searched:`n  $searched`nPass -ProfileDir explicitly."
}

$chrome = Find-Chrome
$resolvedProfileDir = Resolve-GFlowProfileDir -Name $ProfileName -ExplicitDir $ProfileDir

$argsList = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$resolvedProfileDir",
    "--profile-directory=$ProfileDirectory",
    "--no-first-run",
    "--no-default-browser-check"
)

if (-not $NoNavigate) {
    $argsList += $Url
}

Write-Host "Launching Chrome:"
Write-Host "  chrome:  $chrome"
Write-Host "  profile: $resolvedProfileDir"
Write-Host "  dir:     $ProfileDirectory"
Write-Host "  CDP:     http://127.0.0.1:$Port"
Write-Host ""
Write-Host "Keep this Chrome window open while running scripts\run-cdp-smoke.ps1."

Start-Process -FilePath $chrome -ArgumentList $argsList | Out-Null
