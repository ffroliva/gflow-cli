[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectUrl,
    [string]$ProfileName = $env:GFLOW_CLI_PROFILE,
    [string]$Locale = "en",
    [string]$Engine = "cdp-real-chrome",
    [int]$Port = 9334,
    [string]$Session = "gflow-agent-ui",
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
$env:AGENT_BROWSER_IDLE_TIMEOUT_MS = "120000"

New-Item -ItemType Directory -Force $env:AGENT_BROWSER_SOCKET_DIR | Out-Null

function Invoke-AgentBrowser {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$CommandArgs
    )

    $package = "agent-browser@$AgentBrowserVersion"
    $output = & npx --yes --package $package agent-browser @CommandArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) { throw "agent-browser failed with exit code $exitCode for: $($CommandArgs -join ' ')" }
    return ($output -join [Environment]::NewLine)
}

function Get-EvalResult {
    param([string]$Expr)
    $json = Invoke-AgentBrowser @("--cdp", "$Port", "--json", "eval", $Expr)
    $parsed = $json | ConvertFrom-Json
    if (-not $parsed.PSObject.Properties['data']) {
        throw "Unexpected agent-browser response (no .data field): $json"
    }
    return $parsed.data.result
}

# ---------------------------------------------------------------------------
# Startup banner
# ---------------------------------------------------------------------------

Write-Host "Using isolated workspace:"
Write-Host "  project:     $ProjectRoot"
Write-Host "  artifacts:   $ArtifactsDir"
Write-Host "  sockets:     $env:AGENT_BROWSER_SOCKET_DIR"
Write-Host "  session:     $Session"
Write-Host "  CDP target:  127.0.0.1:$Port"
Write-Host "  profile:     $ProfileName"
Write-Host "  project URL: $ProjectUrl"
Write-Host ""

# ---------------------------------------------------------------------------
# Eval expressions
# ---------------------------------------------------------------------------

$signalsExpr = @'
(() => {
  const lig = (s) => Array.from(document.querySelectorAll(s)).map(e => (e.textContent||'').trim());
  const all = lig('i.google-symbols, i.material-symbols-outlined');
  const cropPresent = Array.from(document.querySelectorAll("button i.google-symbols"))
      .some(e => (e.textContent||'').trim().startsWith('crop_'));
  const agentPill = /\bAgent\b/.test(document.body.innerText||'') && !!document.querySelector("div[role='textbox']");
  const chatPanel = all.includes('edit_square') && all.includes('close');
  return { cropPresent, agentPill, chatPanel, url: location.href,
           ligatures: Array.from(new Set(all)).filter(Boolean).sort() };
})()
'@

$gatingExpr = @'
(() => {
  const next = document.getElementById('__NEXT_DATA__');
  let keys = [];
  try { const p = JSON.parse(next ? next.textContent : '{}');
        keys = Object.keys((p.props && p.props.pageProps) || {}).sort(); } catch (e) { keys = ['<unparseable>']; }
  return {
    localStorage: Object.fromEntries(Object.entries(localStorage)),
    sessionStorage: Object.fromEntries(Object.entries(sessionStorage)),
    documentCookieNames: (document.cookie || '').split('; ').map(c => c.split('=')[0]).filter(Boolean).sort(),
    nextDataPagePropKeys: keys
  };
})()
'@

# ---------------------------------------------------------------------------
# Capture flow
# ---------------------------------------------------------------------------

$ts = Get-Date -Format "yyyyMMdd-HHmmss"

Write-Host "Opening project URL in attached Chrome..."
Invoke-AgentBrowser @("--cdp", "$Port", "--json", "open", $ProjectUrl) | Out-Null
Start-Sleep -Seconds 4

Write-Host "Probing navigator.webdriver..."
$webdriver = Get-EvalResult 'navigator.webdriver'

Write-Host "Capturing signals (before recovery attempt)..."
$signalsBefore = Get-EvalResult $signalsExpr

Write-Host "Capturing gating data..."
$gating = Get-EvalResult $gatingExpr

Write-Host "Taking accessibility snapshot..."
$snapPath = Join-Path $ArtifactsDir "agentui-snapshot-$ts.txt"
Invoke-AgentBrowser @("--cdp", "$Port", "snapshot", "-i") | Set-Content -LiteralPath $snapPath -Encoding UTF8
Write-Host "snapshot written: $snapPath"

Write-Host ""
Write-Host "Try to leave Agent mode in the Chrome window (click the Agent pill / close the chat panel)."
Read-Host "Press Enter after attempting to reach the classic image controls"

Write-Host "Capturing signals (after recovery attempt)..."
$signalsAfter = Get-EvalResult $signalsExpr

Write-Host "Starting HAR recording..."
Invoke-AgentBrowser @("--cdp", "$Port", "--json", "network", "requests", "--clear") | Out-Null
Invoke-AgentBrowser @("--cdp", "$Port", "--json", "network", "har", "start") | Out-Null

Write-Host ""
Write-Host "Now trigger exactly ONE image generation through the agentic UI (image gen is free)."
Read-Host "Press Enter after the generation request has fired"

$harFile = "flow-generation-$ts.har"
Invoke-AgentBrowser @("--cdp", "$Port", "--json", "network", "har", "stop", (Join-Path $ArtifactsDir $harFile)) | Out-Null
Write-Host "HAR written: $harFile"

# ---------------------------------------------------------------------------
# Assemble combined output JSON
# ---------------------------------------------------------------------------

$capture = [ordered]@{
    profile            = $ProfileName
    locale             = $Locale
    engine             = $Engine
    capturedAt         = $ts
    projectUrl         = $ProjectUrl
    navigatorWebdriver = $webdriver
    signals            = [ordered]@{
        cropPresent     = [bool]$signalsBefore.cropPresent
        agentPill       = [bool]$signalsBefore.agentPill
        chatPanel       = [bool]$signalsBefore.chatPanel
        cropRecoverable = [bool]$signalsAfter.cropPresent
    }
    gating             = $gating
    ligatures          = $signalsBefore.ligatures
    harFile            = $harFile
}

$outPath = Join-Path $ArtifactsDir "agentui-capture-$ProfileName-$Locale-$ts.json"
$capture | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $outPath -Encoding UTF8
Write-Host "capture written: $outPath"
