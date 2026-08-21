<#
.SYNOPSIS
    Register the nightly gflow live-e2e canary as a Windows Scheduled Task (#502).

.DESCRIPTION
    Hosted CI cannot run the live tiers (real Chrome profile + Google bot
    detection), so the canary runs here, where the warm profile lives, and
    publishes a sanitized verdict to a rolling GitHub issue.

    Point -RepoRoot at a DEDICATED clone, not your working tree. The runner's
    --pull refuses to touch a dirty checkout, so a shared tree would simply
    never run.

    The task is user-level (no elevation) and runs only when you are logged on:
    it needs your profile and your authenticated `gh`. A machine that was off
    produces a visibly stale timestamp on the issue — which is the intended
    signal, not a failure.

.EXAMPLE
    # one-time, from an ordinary PowerShell prompt
    .\scripts\canary\register_task.ps1 -CanaryProfile denon82 -Issue 600 `
        -RepoRoot C:\development\canary\gflow-cli

.EXAMPLE
    # verify it end to end without waiting for 03:00
    Start-ScheduledTask -TaskName 'gflow-canary'
    Get-ScheduledTaskInfo -TaskName 'gflow-canary'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$CanaryProfile,
    [Parameter(Mandatory)][int]$Issue,
    [string]$RepoRoot = (Resolve-Path "$PSScriptRoot\..\.."),
    [string]$Time = '03:00',
    [string]$TaskName = 'gflow-canary',
    [string]$Markers = 'e2e_auth'
)

$ErrorActionPreference = 'Stop'

$python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$runner = Join-Path $RepoRoot 'scripts\canary\run_canary.py'

foreach ($path in @($python, $runner)) {
    if (-not (Test-Path $path)) { throw "Not found: $path" }
}

# `gh` must already be authenticated for this user — the canary ships no token.
& gh auth status *> $null
if ($LASTEXITCODE -ne 0) { throw 'gh is not authenticated. Run: gh auth login' }

# Every value is quoted: -Markers 'e2e_auth or e2e_scene' (the documented
# fast-follow) contains spaces and would otherwise split into three argv tokens
# that argparse rejects.
$arguments = @(
    "`"$runner`""
    '--profile', "`"$CanaryProfile`""
    '--issue', "$Issue"
    '--markers', "`"$Markers`""
    '--pull'
) -join ' '

$action    = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $RepoRoot
$trigger   = New-ScheduledTaskTrigger -Daily -At $Time
# Never wake or start the machine: an off machine is a stale issue, by design.
# StartWhenAvailable catches up after a missed night rather than skipping it.
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -Priority 5 `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Nightly gflow live-e2e canary (#502)' -Force | Out-Null

Write-Host "Registered '$TaskName' — daily at $Time"
Write-Host "  repo:    $RepoRoot"
Write-Host "  profile: $CanaryProfile   issue: #$Issue   markers: $Markers"
Write-Host ''
Write-Host "Smoke it now:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Remove it:     Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
