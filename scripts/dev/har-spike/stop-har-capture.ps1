[CmdletBinding()]
param(
    [int]$Port = 9334,
    [string]$Session = "",
    [string]$AgentBrowserVersion = "0.27.0",
    [string]$CaptureId = "",
    [string]$HarPath = "",
    [string]$HostFilter = "aisandbox-pa.googleapis.com"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$ArtifactsDir = Join-Path $ProjectRoot "artifacts"
$AgentStateDir = Join-Path $ProjectRoot ".agent-browser"
$NpmCacheDir = Join-Path $ProjectRoot ".npm-cache"
$SocketDir = Join-Path $AgentStateDir "sockets"

New-Item -ItemType Directory -Force $ArtifactsDir, $AgentStateDir, $NpmCacheDir, $SocketDir | Out-Null

$state = $null
$statePath = $null

if ($CaptureId) {
    $statePath = Join-Path $ArtifactsDir "har-capture-$CaptureId.json"
}
else {
    $statePath = Join-Path $ArtifactsDir "har-capture-current.json"
}

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json -ErrorAction Stop
    if (-not $CaptureId -and $state.captureId) {
        $CaptureId = [string]$state.captureId
    }
}

if (-not $CaptureId) {
    $CaptureId = Get-Date -Format "yyyyMMdd-HHmmss"
}

if ($state) {
    if (-not $PSBoundParameters.ContainsKey("Port") -and $state.port) {
        $Port = [int]$state.port
    }
    if (-not $PSBoundParameters.ContainsKey("Session") -and $state.session) {
        $Session = [string]$state.session
    }
    if (-not $PSBoundParameters.ContainsKey("AgentBrowserVersion") -and $state.agentBrowserVersion) {
        $AgentBrowserVersion = [string]$state.agentBrowserVersion
    }
    if (-not $PSBoundParameters.ContainsKey("HostFilter") -and $state.hostFilter) {
        $HostFilter = [string]$state.hostFilter
    }
}

if (-not $Session) {
    $Session = "gflow-cdp-spike"
}

if (-not $HarPath) {
    if ($state -and $state.harPath) {
        $HarPath = [string]$state.harPath
    }
    else {
        $HarPath = Join-Path $ArtifactsDir "flow-generation-$CaptureId.har"
    }
}

if ($state -and $state.summaryPath) {
    $summaryPath = [string]$state.summaryPath
}
else {
    $summaryPath = Join-Path $ArtifactsDir "flow-generation-$CaptureId.summary.json"
}

$env:npm_config_cache = $NpmCacheDir
$env:AGENT_BROWSER_SOCKET_DIR = $SocketDir
$env:AGENT_BROWSER_SESSION = $Session
$env:AGENT_BROWSER_IDLE_TIMEOUT_MS = "60000"

function Invoke-AgentBrowser {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$CommandArgs
    )

    $package = "agent-browser@$AgentBrowserVersion"
    $output = & npx --yes --package $package agent-browser @CommandArgs
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        throw "agent-browser failed with exit code $exitCode for: $($CommandArgs -join ' ')"
    }

    return ($output -join [Environment]::NewLine)
}

function Save-Text {
    param(
        [string]$Name,
        [string]$Text
    )

    $path = Join-Path $ArtifactsDir $Name
    Set-Content -LiteralPath $path -Value $Text -Encoding UTF8
    return $path
}

function Invoke-AgentBrowserJson {
    param(
        [string]$Name,
        [string[]]$CommandArgs
    )

    $json = Invoke-AgentBrowser @CommandArgs
    $path = Save-Text -Name $Name -Text $json
    try {
        $parsed = $json | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "Could not parse JSON from agent-browser command '$($CommandArgs -join ' ')'. Saved raw output to $path."
    }
    return [pscustomobject]@{
        Path = $path
        Json = $parsed
        Raw  = $json
    }
}

Write-Host "Stopping HAR capture:"
Write-Host "  project:     $ProjectRoot"
Write-Host "  session:     $Session"
Write-Host "  CDP target:  127.0.0.1:$Port"
Write-Host "  capture id:  $CaptureId"
Write-Host "  HAR path:    $HarPath"
Write-Host ""

$requests = Invoke-AgentBrowserJson `
    -Name "network-requests-$CaptureId.json" `
    -CommandArgs @("--cdp", "$Port", "--json", "network", "requests", "--filter", $HostFilter)
Write-Host "filtered network requests saved: $($requests.Path)"

$stopped = Invoke-AgentBrowserJson `
    -Name "har-stop-$CaptureId.json" `
    -CommandArgs @("--cdp", "$Port", "--json", "network", "har", "stop", $HarPath)
Write-Host "HAR stop response saved: $($stopped.Path)"

if (-not (Test-Path -LiteralPath $HarPath)) {
    throw "HAR file was not created: $HarPath"
}

$extractor = Join-Path $ProjectRoot "scripts\extract_har_summary.py"
& python $extractor $HarPath --host $HostFilter --out $summaryPath
$pythonExitCode = $LASTEXITCODE
if ($pythonExitCode -ne 0) {
    throw "HAR summary extraction failed with exit code $pythonExitCode"
}

if ($state) {
    $state | Add-Member -NotePropertyName stoppedAt -NotePropertyValue (Get-Date).ToString("o") -Force
    $state | Add-Member -NotePropertyName harPath -NotePropertyValue $HarPath -Force
    $state | Add-Member -NotePropertyName summaryPath -NotePropertyValue $summaryPath -Force
    $stateJson = $state | ConvertTo-Json -Depth 6
    Set-Content -LiteralPath $statePath -Value $stateJson -Encoding UTF8

    $captureStatePath = Join-Path $ArtifactsDir "har-capture-$CaptureId.json"
    if ($captureStatePath -ne $statePath) {
        Set-Content -LiteralPath $captureStatePath -Value $stateJson -Encoding UTF8
    }
}

if (Test-Path -LiteralPath (Join-Path $ProjectRoot "nul")) {
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "nul") -Force
}

Write-Host ""
Write-Host "HAR saved: $HarPath"
Write-Host "Redacted summary saved: $summaryPath"
Write-Host "Treat raw HAR and request artifacts as sensitive; do not commit or share them."
