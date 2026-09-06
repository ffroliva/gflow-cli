[CmdletBinding()]
param(
    [string]$CaptureId = "",
    [int]$CommandTimeoutSeconds = 45
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$ArtifactsDir = Join-Path $ProjectRoot "artifacts"
$AgentStateDir = Join-Path $ProjectRoot ".agent-browser"
$NpmCacheDir = Join-Path $ProjectRoot ".npm-cache"
$SocketDir = Join-Path $AgentStateDir "sockets"

if ($CaptureId) {
    $statePath = Join-Path $ArtifactsDir "har-capture-$CaptureId.json"
}
else {
    $statePath = Join-Path $ArtifactsDir "har-capture-current.json"
}

if (-not (Test-Path -LiteralPath $statePath)) {
    throw "Capture state was not found: $statePath"
}

$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json -ErrorAction Stop
$CaptureId = [string]$state.captureId
$Port = [int]$state.port
$Session = [string]$state.session
$AgentBrowserVersion = [string]$state.agentBrowserVersion
$HostFilter = [string]$state.hostFilter
$HarPath = [string]$state.harPath
$SummaryPath = [string]$state.summaryPath

function Invoke-DetachedCommand {
    param(
        [string]$Name,
        [string[]]$Arguments,
        [int]$TimeoutSeconds,
        [switch]$AllowTimeout
    )

    $stdout = Join-Path $ArtifactsDir "$CaptureId-$Name.out.log"
    $stderr = Join-Path $ArtifactsDir "$CaptureId-$Name.err.log"
    $npx = (Get-Command npx.cmd -ErrorAction Stop).Source

    $env:npm_config_cache = $NpmCacheDir
    $env:AGENT_BROWSER_SOCKET_DIR = $SocketDir
    $env:AGENT_BROWSER_SESSION = $Session
    $env:AGENT_BROWSER_IDLE_TIMEOUT_MS = "60000"

    $process = Start-Process `
        -FilePath $npx `
        -ArgumentList $Arguments `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        if (-not $AllowTimeout) {
            throw "Command '$Name' timed out after $TimeoutSeconds seconds. Logs: $stdout, $stderr"
        }
    }
    elseif ($process.ExitCode -ne 0) {
        throw "Command '$Name' failed with exit code $($process.ExitCode). Logs: $stdout, $stderr"
    }

    return [pscustomobject]@{
        stdout = $stdout
        stderr = $stderr
    }
}

$package = "agent-browser@$AgentBrowserVersion"
$agentPrefix = @("--yes", "--package", $package, "agent-browser", "--cdp", "$Port", "--json")

Invoke-DetachedCommand `
    -Name "network-requests" `
    -Arguments ($agentPrefix + @("network", "requests", "--filter", $HostFilter)) `
    -TimeoutSeconds $CommandTimeoutSeconds `
    -AllowTimeout | Out-Null

Invoke-DetachedCommand `
    -Name "har-stop" `
    -Arguments ($agentPrefix + @("network", "har", "stop", $HarPath)) `
    -TimeoutSeconds $CommandTimeoutSeconds `
    -AllowTimeout | Out-Null

if (-not (Test-Path -LiteralPath $HarPath)) {
    throw "HAR file was not created: $HarPath"
}

$extractor = Join-Path $ProjectRoot "scripts\extract_har_summary.py"
& python $extractor $HarPath --host $HostFilter --out $SummaryPath
$pythonExitCode = $LASTEXITCODE
if ($pythonExitCode -ne 0) {
    throw "HAR summary extraction failed with exit code $pythonExitCode"
}

$state | Add-Member -NotePropertyName stoppedAt -NotePropertyValue (Get-Date).ToString("o") -Force
$state | Add-Member -NotePropertyName harPath -NotePropertyValue $HarPath -Force
$state | Add-Member -NotePropertyName summaryPath -NotePropertyValue $SummaryPath -Force
$stateJson = $state | ConvertTo-Json -Depth 6
Set-Content -LiteralPath $statePath -Value $stateJson -Encoding UTF8
Set-Content -LiteralPath (Join-Path $ArtifactsDir "har-capture-current.json") -Value $stateJson -Encoding UTF8

if (Test-Path -LiteralPath (Join-Path $ProjectRoot "nul")) {
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "nul") -Force
}

Write-Host "Capture stopped."
Write-Host "HAR: $HarPath"
Write-Host "Summary: $SummaryPath"
