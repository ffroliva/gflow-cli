[CmdletBinding()]
param(
    [int]$Port = 9334,
    [string]$Session = "gflow-cdp-spike",
    [string]$AgentBrowserVersion = "0.27.0",
    [string]$Url = "https://labs.google/fx/tools/flow?hl=en",
    [switch]$ManualGeneration,
    [switch]$SkipSnapshot
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
$env:AGENT_BROWSER_IDLE_TIMEOUT_MS = "60000"

New-Item -ItemType Directory -Force $env:AGENT_BROWSER_SOCKET_DIR | Out-Null

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

Write-Host "Using isolated workspace:"
Write-Host "  project:     $ProjectRoot"
Write-Host "  npm cache:   $NpmCacheDir"
Write-Host "  sockets:     $env:AGENT_BROWSER_SOCKET_DIR"
Write-Host "  artifacts:   $ArtifactsDir"
Write-Host "  session:     $Session"
Write-Host "  CDP target:  127.0.0.1:$Port"
Write-Host ""

$version = Invoke-AgentBrowser @("--version")
$versionPath = Save-Text -Name "agent-browser-version.txt" -Text $version
Write-Host "agent-browser: $version"
Write-Host "version saved: $versionPath"

$open = Invoke-AgentBrowserJson `
    -Name "open-flow.json" `
    -CommandArgs @("--cdp", "$Port", "--json", "open", $Url)
Write-Host "open saved: $($open.Path)"

$webdriver = Invoke-AgentBrowserJson `
    -Name "navigator-webdriver.json" `
    -CommandArgs @("--cdp", "$Port", "--json", "eval", "navigator.webdriver")
$webdriverValue = $webdriver.Json.data.result
Write-Host "navigator.webdriver: $webdriverValue"

$pageProbeExpression = '({url:location.href,title:document.title,host:location.host,onAccounts:location.host.includes("accounts.google.com"),bodyIncludesFlow:document.body.innerText.includes("Flow"),bodyIncludesSignIn:document.body.innerText.toLowerCase().includes("sign in")})'

$pageProbe = Invoke-AgentBrowserJson `
    -Name "flow-page-probe.json" `
    -CommandArgs @("--cdp", "$Port", "--json", "eval", $pageProbeExpression)
Write-Host "page probe saved: $($pageProbe.Path)"

if (-not $SkipSnapshot) {
    $snapshot = Invoke-AgentBrowser @("--cdp", "$Port", "snapshot", "-i")
    $snapshotPath = Save-Text -Name "snapshot-interactive.txt" -Text $snapshot
    Write-Host "snapshot saved: $snapshotPath"
}

$requestsClear = Invoke-AgentBrowserJson `
    -Name "network-requests-cleared.json" `
    -CommandArgs @("--cdp", "$Port", "--json", "network", "requests", "--clear")
Write-Host "network request tracker cleared: $($requestsClear.Path)"

if ($ManualGeneration) {
    $started = Invoke-AgentBrowserJson `
        -Name "har-start.json" `
        -CommandArgs @("--cdp", "$Port", "--json", "network", "har", "start")
    Write-Host "HAR recording started: $($started.Path)"
    Write-Host ""
    Write-Host "Use the Chrome window to trigger one Flow generation now."
    Read-Host "Press Enter here after the generation request has fired"

    $requests = Invoke-AgentBrowserJson `
        -Name "network-requests-after-generation.json" `
        -CommandArgs @("--cdp", "$Port", "--json", "network", "requests", "--filter", "aisandbox-pa")
    Write-Host "filtered network requests saved: $($requests.Path)"

    $harPath = Join-Path $ArtifactsDir ("flow-generation-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".har")
    $stopped = Invoke-AgentBrowserJson `
        -Name "har-stop.json" `
        -CommandArgs @("--cdp", "$Port", "--json", "network", "har", "stop", $harPath)
    Write-Host "HAR stop response saved: $($stopped.Path)"
    Write-Host "HAR file target: $harPath"
}

if (Test-Path -LiteralPath (Join-Path $ProjectRoot "nul")) {
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "nul") -Force
}

Write-Host ""
Write-Host "Done. Evidence is under: $ArtifactsDir"
