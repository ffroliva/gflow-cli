<#
.SYNOPSIS
  Capture before/after media-DOM evidence around ONE agentic-UI generation.

.DESCRIPTION
  The open question blocking AgenticFlowUiDriver.await_images is: when a generation
  completes in the agentic cohort, do the resulting assets appear as countable DOM
  nodes with a *remote* https src that can be scraped — or only as blob:/data: URIs,
  background-image divs, or canvas pixels that defeat count-delta scraping?

  This script attaches to an already-running CDP Chrome (launched via
  launch-flow-chrome.ps1), snapshots all media nodes BEFORE a generation, waits for
  you to trigger exactly one generation in the Chrome window, then snapshots AFTER and
  computes the delta. It also records any dialog / warning text so we can validate the
  content-policy fail-fast path.

  Eval expressions are base64-wrapped (eval(atob('...'))) because multi-line eval args
  get mangled through npx (see docs/AGENT_UI_RECON.md "How it was captured").

.EXAMPLE
  .\scripts\launch-flow-chrome.ps1 -ProfileName default -Port 9334
  .\scripts\capture-media-scrape.ps1 -ProjectUrl "https://labs.google/fx/tools/flow/project/<uuid>" -ProfileName default -Port 9334
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectUrl,
    [string]$ProfileName = $env:GFLOW_CLI_PROFILE,
    [string]$Locale = "en",
    [int]$Port = 9334,
    [string]$Session = "gflow-media-scrape",
    [string]$AgentBrowserVersion = "0.27.0"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$ArtifactsDir = Join-Path $ProjectRoot "artifacts"
$AgentStateDir = Join-Path $ProjectRoot ".agent-browser"
$NpmCacheDir = Join-Path $ProjectRoot ".npm-cache"

New-Item -ItemType Directory -Force $ArtifactsDir, $AgentStateDir, $NpmCacheDir | Out-Null

$env:npm_config_cache = $NpmCacheDir
$env:AGENT_BROWSER_SOCKET_DIR = Join-Path $AgentStateDir "sockets"
$env:AGENT_BROWSER_SESSION = $Session
$env:AGENT_BROWSER_IDLE_TIMEOUT_MS = "180000"

New-Item -ItemType Directory -Force $env:AGENT_BROWSER_SOCKET_DIR | Out-Null

function Invoke-AgentBrowser {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CommandArgs)
    $package = "agent-browser@$AgentBrowserVersion"
    $output = & npx --yes --package $package agent-browser @CommandArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) { throw "agent-browser failed (exit $exitCode) for: $($CommandArgs -join ' ')" }
    return ($output -join [Environment]::NewLine)
}

function Get-EvalResult {
    # Base64-wrap the expression so multi-line JS survives npx arg parsing.
    param([string]$Expr)
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Expr))
    $wrapped = "eval(atob('$b64'))"
    $json = Invoke-AgentBrowser @("--cdp", "$Port", "--json", "eval", $wrapped)
    $parsed = $json | ConvertFrom-Json
    if (-not $parsed.PSObject.Properties['data']) {
        throw "Unexpected agent-browser response (no .data field): $json"
    }
    return $parsed.data.result
}

# ---------------------------------------------------------------------------
# Eval expressions
# ---------------------------------------------------------------------------

# Cohort signal (same rule as capture-agent-ui.ps1) so the artifact records whether
# this was genuinely the agentic UI.
$signalsExpr = @'
(() => {
  const all = Array.from(document.querySelectorAll('i.google-symbols, i.material-symbols-outlined'))
      .map(e => (e.textContent||'').trim());
  const cropPresent = Array.from(document.querySelectorAll("button i.google-symbols"))
      .some(e => (e.textContent||'').trim().startsWith('crop_'));
  const agentPill = /\bAgent\b/.test(document.body.innerText||'') && !!document.querySelector("div[role='textbox']");
  const chatPanel = all.includes('edit_square') && all.includes('close');
  return { cropPresent, agentPill, chatPanel, agentic: (!cropPresent && (agentPill || chatPanel)) };
})()
'@

# Media probe: every img/video + background-image div, classified by src kind, plus any
# dialog / warning text for the content-policy fail-fast path.
$mediaExpr = @'
(() => {
  const pick = (e) => {
    const raw = e.currentSrc || e.getAttribute('src') || '';
    let kind = 'other';
    if (/^blob:/.test(raw)) kind = 'blob';
    else if (/^data:/.test(raw)) kind = 'data';
    else if (/^https?:/.test(raw)) kind = 'remote';
    return {
      tag: e.tagName.toLowerCase(), kind, src: raw.slice(0, 300),
      w: e.naturalWidth || e.videoWidth || 0, h: e.naturalHeight || e.videoHeight || 0,
      alt: (e.getAttribute('alt') || '').slice(0, 80),
      cls: (e.className || '').toString().slice(0, 140),
      testid: e.getAttribute('data-testid') || ''
    };
  };
  const imgs = Array.from(document.querySelectorAll('img')).map(pick);
  const vids = Array.from(document.querySelectorAll('video')).map(pick);
  const bg = Array.from(document.querySelectorAll('[style*="background-image"]')).map((e) => {
    const m = (e.getAttribute('style') || '').match(/url\((["']?)([^"')]+)\1\)/);
    return { url: (m ? m[2] : '').slice(0, 300), cls: (e.className || '').toString().slice(0, 140) };
  }).filter((x) => x.url && !/^data:/.test(x.url));
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"],[role="alert"],[aria-live="assertive"],[aria-live="polite"]'))
    .map((e) => (e.innerText || '').trim()).filter(Boolean).map((t) => t.slice(0, 240));
  const warnSymbols = Array.from(document.querySelectorAll('i.google-symbols, i.material-symbols-outlined'))
    .map((e) => (e.textContent || '').trim()).filter((t) => ['warning', 'error', 'report', 'flag', 'block'].includes(t));
  return {
    url: location.href,
    counts: { img: imgs.length, video: vids.length, bg: bg.length },
    remoteImg: imgs.filter((x) => x.kind === 'remote').length,
    blobImg: imgs.filter((x) => x.kind === 'blob').length,
    canvasCount: document.querySelectorAll('canvas').length,
    imgs, vids, bg, dialogs, warnSymbols
  };
})()
'@

# ---------------------------------------------------------------------------
# Capture flow
# ---------------------------------------------------------------------------

$ts = Get-Date -Format "yyyyMMdd-HHmmss"

Write-Host "Isolated workspace:"
Write-Host "  artifacts:  $ArtifactsDir"
Write-Host "  CDP target: 127.0.0.1:$Port"
Write-Host "  profile:    $ProfileName"
Write-Host "  projectURL: $ProjectUrl"
Write-Host ""

Write-Host "Opening project URL in attached Chrome..."
Invoke-AgentBrowser @("--cdp", "$Port", "--json", "open", $ProjectUrl) | Out-Null
Start-Sleep -Seconds 4

$signals = Get-EvalResult $signalsExpr
if ($signals.agentic) {
    Write-Host "Cohort: AGENTIC (cropPresent=$($signals.cropPresent) agentPill=$($signals.agentPill) chatPanel=$($signals.chatPanel))" -ForegroundColor Green
}
else {
    Write-Host "Cohort: CLASSIC (cropPresent=$($signals.cropPresent)). Re-run until the agentic UI is served — the cohort flaps per page load." -ForegroundColor Yellow
}

Write-Host "Capturing media snapshot BEFORE generation..."
$before = Get-EvalResult $mediaExpr
Write-Host "  before: img=$($before.counts.img) (remote=$($before.remoteImg) blob=$($before.blobImg)) video=$($before.counts.video) bg=$($before.counts.bg) canvas=$($before.canvasCount)"

Write-Host ""
Write-Host "Now trigger exactly ONE image generation through the agentic UI (image gen is free)."
Write-Host "Wait until the generated image is fully visible (or an error/warning appears), THEN continue."
Read-Host "Press Enter once the generation has completed (or failed)"

Write-Host "Capturing media snapshot AFTER generation..."
$after = Get-EvalResult $mediaExpr
Write-Host "  after:  img=$($after.counts.img) (remote=$($after.remoteImg) blob=$($after.blobImg)) video=$($after.counts.video) bg=$($after.counts.bg) canvas=$($after.canvasCount)"

# ---------------------------------------------------------------------------
# Delta analysis — the actual evidence for/against count-delta DOM scraping
# ---------------------------------------------------------------------------

$beforeSrcs = @($before.imgs | ForEach-Object { $_.src })
$newImgs = @($after.imgs | Where-Object { $beforeSrcs -notcontains $_.src })
$newRemote = @($newImgs | Where-Object { $_.kind -eq 'remote' })
$newBlob = @($newImgs | Where-Object { $_.kind -eq 'blob' })

$beforeBg = @($before.bg | ForEach-Object { $_.url })
$newBg = @($after.bg | Where-Object { $beforeBg -notcontains $_.url })

$verdict =
if ($newRemote.Count -gt 0) { "SCRAPEABLE: $($newRemote.Count) new <img> with remote https src" }
elseif ($newBlob.Count -gt 0) { "PARTIAL: $($newBlob.Count) new <img> but blob: src (needs different extraction)" }
elseif ($newBg.Count -gt 0) { "BG-IMAGE: assets surface as background-image divs, not <img>" }
elseif (($after.canvasCount - $before.canvasCount) -gt 0) { "CANVAS: assets rendered to <canvas> — count-delta scraping will NOT work" }
else { "INCONCLUSIVE: no new media nodes detected — check dialogs/warnings for a block/error" }

Write-Host ""
Write-Host "VERDICT: $verdict" -ForegroundColor Cyan

$capture = [ordered]@{
    profile      = $ProfileName
    locale       = $Locale
    capturedAt   = $ts
    projectUrl   = $ProjectUrl
    cohort       = if ($signals.agentic) { "agentic" } else { "classic" }
    signals      = $signals
    scrapeVerdict = $verdict
    delta        = [ordered]@{
        imgCountBefore = $before.counts.img
        imgCountAfter  = $after.counts.img
        newImgTotal    = $newImgs.Count
        newImgRemote   = $newRemote.Count
        newImgBlob     = $newBlob.Count
        newBgImages    = $newBg.Count
        canvasDelta    = ($after.canvasCount - $before.canvasCount)
        newRemoteSrcs  = @($newRemote | ForEach-Object { $_.src })
        newImgClasses  = @($newImgs | ForEach-Object { $_.cls } | Select-Object -Unique)
    }
    before       = $before
    after        = $after
}

$outPath = Join-Path $ArtifactsDir "media-scrape-$ProfileName-$Locale-$ts.json"
$capture | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $outPath -Encoding UTF8
Write-Host "capture written: $outPath"
