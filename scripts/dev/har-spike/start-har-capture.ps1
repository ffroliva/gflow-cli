[CmdletBinding()]
param(
    [int]$Port = 9334,
    [string]$Session = "gflow-cdp-spike",
    [string]$AgentBrowserVersion = "0.27.0",
    [string]$Url = "https://labs.google/fx/tools/flow?hl=en",
    [string]$CaptureId = (Get-Date -Format "yyyyMMdd-HHmmss"),
    [switch]$SkipSnapshot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$ArtifactsDir = Join-Path $ProjectRoot "artifacts"
$AgentStateDir = Join-Path $ProjectRoot ".agent-browser"
$NpmCacheDir = Join-Path $ProjectRoot ".npm-cache"
$SocketDir = Join-Path $AgentStateDir "sockets"

New-Item -ItemType Directory -Force $ArtifactsDir, $AgentStateDir, $NpmCacheDir, $SocketDir | Out-Null

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

Write-Host "Starting HAR capture:"
Write-Host "  project:     $ProjectRoot"
Write-Host "  npm cache:   $NpmCacheDir"
Write-Host "  sockets:     $SocketDir"
Write-Host "  artifacts:   $ArtifactsDir"
Write-Host "  session:     $Session"
Write-Host "  CDP target:  127.0.0.1:$Port"
Write-Host "  capture id:  $CaptureId"
Write-Host ""

$version = Invoke-AgentBrowser @("--version")
$versionPath = Save-Text -Name "agent-browser-version.txt" -Text $version
Write-Host "agent-browser: $version"
Write-Host "version saved: $versionPath"

$open = Invoke-AgentBrowserJson `
    -Name "open-flow-$CaptureId.json" `
    -CommandArgs @("--cdp", "$Port", "--json", "open", $Url)
Write-Host "open saved: $($open.Path)"

$webdriver = Invoke-AgentBrowserJson `
    -Name "navigator-webdriver-$CaptureId.json" `
    -CommandArgs @("--cdp", "$Port", "--json", "eval", "navigator.webdriver")
Write-Host "navigator.webdriver: $($webdriver.Json.data.result)"

$pageProbeExpression = '({url:location.href,title:document.title,host:location.host,onAccounts:location.host.includes("accounts.google.com"),bodyIncludesFlow:document.body.innerText.includes("Flow"),bodyIncludesSignIn:document.body.innerText.toLowerCase().includes("sign in")})'
$pageProbe = Invoke-AgentBrowserJson `
    -Name "flow-page-probe-$CaptureId.json" `
    -CommandArgs @("--cdp", "$Port", "--json", "eval", $pageProbeExpression)
Write-Host "page probe saved: $($pageProbe.Path)"

if (-not $SkipSnapshot) {
    $snapshot = Invoke-AgentBrowser @("--cdp", "$Port", "snapshot", "-i")
    $snapshotPath = Save-Text -Name "snapshot-interactive-$CaptureId.txt" -Text $snapshot
    Write-Host "snapshot saved: $snapshotPath"
}

$requestsClear = Invoke-AgentBrowserJson `
    -Name "network-requests-cleared-$CaptureId.json" `
    -CommandArgs @("--cdp", "$Port", "--json", "network", "requests", "--clear")
Write-Host "network request tracker cleared: $($requestsClear.Path)"

$started = Invoke-AgentBrowserJson `
    -Name "har-start-$CaptureId.json" `
    -CommandArgs @("--cdp", "$Port", "--json", "network", "har", "start")
Write-Host "HAR recording started: $($started.Path)"

$harPath = Join-Path $ArtifactsDir "flow-generation-$CaptureId.har"
$summaryPath = Join-Path $ArtifactsDir "flow-generation-$CaptureId.summary.json"
$statePath = Join-Path $ArtifactsDir "har-capture-$CaptureId.json"
$currentPath = Join-Path $ArtifactsDir "har-capture-current.json"

$state = [ordered]@{
    captureId           = $CaptureId
    port                = $Port
    session             = $Session
    url                 = $Url
    startedAt           = (Get-Date).ToString("o")
    agentBrowserVersion = $AgentBrowserVersion
    hostFilter          = "aisandbox-pa.googleapis.com"
    harPath             = $harPath
    summaryPath         = $summaryPath
}

$stateJson = $state | ConvertTo-Json -Depth 6
Set-Content -LiteralPath $statePath -Value $stateJson -Encoding UTF8
Set-Content -LiteralPath $currentPath -Value $stateJson -Encoding UTF8

if (Test-Path -LiteralPath (Join-Path $ProjectRoot "nul")) {
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "nul") -Force
}

Write-Host ""
Write-Host "Capture is recording. Use the Chrome window to trigger one Flow generation."
Write-Host "Then stop and summarize it with:"
Write-Host "  .\scripts\stop-har-capture.ps1 -CaptureId $CaptureId"
Write-Host ""
Write-Host "State saved: $statePath"
