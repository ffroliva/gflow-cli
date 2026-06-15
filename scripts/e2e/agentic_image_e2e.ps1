<#
.SYNOPSIS
  Deterministic, repeatable live e2e for the Agentic UI image path.

.DESCRIPTION
  The Agentic UI is a server-assigned A/B cohort that can't be forced from the
  client (docs/AGENT_UI_RECON.md) — so it can't be requested directly. BUT the
  classic composer exposes an in-input "Agent" toggle that switches it into the
  *same* agentic layout (Slate box + ``tune`` settings, ``crop_*`` removed —
  confirmed live via scripts/e2e/capture_agent_toggle.py). Setting
  ``GFLOW_CLI_FORCE_AGENT_UI=1`` makes the transport click that toggle after
  entering the editor, so ``gflow image`` deterministically drives the agentic
  path on *any* load.

  This harness runs that forced-agentic generation and verifies the full chain:
    1. ``agent_mode_forced activated=true``  (the toggle flipped to agentic)
    2. ``ui_driver.bound mode=agentic``      (the agentic driver was selected)
    3. exit 0 and exactly ``-Count`` image files written (scrape → dedup by
       media UUID → redirect-URL download all succeeded).

  Image generation is free, so this is safe to run repeatedly / in CI-lite.

.PARAMETER Profile  Logged-in gflow profile. Default: denon82.
.PARAMETER Count    Images to request (validates dedup when >1). Default: 1.
.PARAMETER Prompt   Image prompt (keep benign). Default: a still life.

.EXAMPLE
  .\scripts\e2e\agentic_image_e2e.ps1 -Profile denon82 -Count 3
#>
[CmdletBinding()]
param(
    [string]$Profile = "denon82",
    [int]$Count = 1,
    [string]$Prompt = "a single ripe banana on a white plate, studio lighting",
    [string]$Aspect = "16:9"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:GFLOW_CLI_FORCE_AGENT_UI = "1"  # the deterministic trigger

$RunDir = Join-Path $env:TEMP ("gflow-agentic-e2e-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Force $RunDir | Out-Null
$log = Join-Path $RunDir "run.log"

Write-Host "Deterministic agentic image e2e (GFLOW_CLI_FORCE_AGENT_UI=1)"
Write-Host "  profile: $Profile   count: $Count   aspect: $Aspect"
Write-Host "  artifacts: $RunDir`n"

uv run gflow image t2i $Prompt --profile $Profile --count $Count --aspect $Aspect --out $RunDir --json *> $log
$code = $LASTEXITCODE

$forced = [bool](Select-String -Path $log -Pattern '"event":\s*"ui_automation.agent_mode_forced".*"activated":\s*true' -Quiet)
$agentic = [bool](Select-String -Path $log -Pattern '"mode":\s*"agentic"' -Quiet)
$jpgs = @(Get-ChildItem -Path $RunDir -Filter *.jpg -ErrorAction SilentlyContinue)

Write-Host "==================== RESULT ===================="
Write-Host "exit code:           $code"
Write-Host "agent_mode_forced:   $forced"
Write-Host "bound mode agentic:  $agentic"
Write-Host "images written:      $($jpgs.Count) / $Count"
Write-Host "log:                 $log"
Write-Host "================================================"

$pass = $forced -and $agentic -and ($code -eq 0) -and ($jpgs.Count -eq $Count)
if ($pass) {
    Write-Host "PASS: agentic drive validated end to end (force -> scrape -> download)." -ForegroundColor Green
    exit 0
}
Write-Host "FAIL: see $log. (If 'agent_mode_forced' is false the toggle selector may have" -ForegroundColor Red
Write-Host "drifted; if 'bound mode agentic' is false detection regressed; if images < count," -ForegroundColor Red
Write-Host "the scrape/dedup/download path needs inspection.)" -ForegroundColor Red
exit 1
