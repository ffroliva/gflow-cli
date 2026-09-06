[CmdletBinding()]
param(
    [int]$Port = 9557,
    [string]$ProfileName = $env:GFLOW_CLI_PROFILE,
    [string]$ProfileDir,
    [string]$ProfileDirectory = "Default",
    [string]$Session = "gflow-cdp-spike",
    [string]$AgentBrowserVersion = "0.27.0",
    [string]$Url = "https://labs.google/fx/tools/flow?hl=en",
    [string]$CaptureId = (Get-Date -Format "yyyyMMdd-HHmmss"),
    [int]$CommandTimeoutSeconds = 45
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$ArtifactsDir = Join-Path $ProjectRoot "artifacts"
$AgentStateDir = Join-Path $ProjectRoot ".agent-browser"
$NpmCacheDir = Join-Path $ProjectRoot ".npm-cache"
$SocketDir = Join-Path $AgentStateDir "sockets"

New-Item -ItemType Directory -Force $ArtifactsDir, $AgentStateDir, $NpmCacheDir, $SocketDir | Out-Null

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

    throw "Google Chrome was not found."
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
        return (Resolve-Path -LiteralPath $ExplicitDir -ErrorAction Stop).Path
    }

    if (-not $Name) {
        $Name = "default"
    }

    foreach ($candidateHome in Get-CandidateGFlowHomes) {
        $candidate = Join-Path $candidateHome "profile_$Name"
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path
        }
    }

    throw "Could not find gflow profile '$Name'. Pass -ProfileDir explicitly."
}

function Wait-ForCdp {
    param(
        [int]$TargetPort,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$TargetPort/json/version" -TimeoutSec 1 | Out-Null
            return
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }

    throw "CDP endpoint did not become ready on port $TargetPort."
}

function Invoke-DetachedCommand {
    param(
        [string]$Name,
        [string[]]$Arguments,
        [int]$TimeoutSeconds
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
        throw "Command '$Name' timed out after $TimeoutSeconds seconds. Logs: $stdout, $stderr"
    }

    if ($process.ExitCode -ne 0) {
        throw "Command '$Name' failed with exit code $($process.ExitCode). Logs: $stdout, $stderr"
    }

    return [pscustomobject]@{
        stdout = $stdout
        stderr = $stderr
    }
}

$chrome = Find-Chrome
$resolvedProfileDir = Resolve-GFlowProfileDir -Name $ProfileName -ExplicitDir $ProfileDir
$chromeLog = Join-Path $ArtifactsDir "$CaptureId-chrome.log"
$env:CHROME_LOG_FILE = $chromeLog

$chromeArgs = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$resolvedProfileDir",
    "--profile-directory=$ProfileDirectory",
    "--no-first-run",
    "--no-default-browser-check",
    "--enable-logging=stderr",
    "--v=1",
    "--new-window",
    $Url
)

$chromeProcess = Start-Process -FilePath $chrome -ArgumentList $chromeArgs -PassThru
Wait-ForCdp -TargetPort $Port -TimeoutSeconds 30

$package = "agent-browser@$AgentBrowserVersion"
$agentPrefix = @("--yes", "--package", $package, "agent-browser", "--cdp", "$Port", "--json")

Invoke-DetachedCommand `
    -Name "open-flow" `
    -Arguments ($agentPrefix + @("open", $Url)) `
    -TimeoutSeconds $CommandTimeoutSeconds | Out-Null

Invoke-DetachedCommand `
    -Name "network-clear" `
    -Arguments ($agentPrefix + @("network", "requests", "--clear")) `
    -TimeoutSeconds $CommandTimeoutSeconds | Out-Null

Invoke-DetachedCommand `
    -Name "har-start" `
    -Arguments ($agentPrefix + @("network", "har", "start")) `
    -TimeoutSeconds $CommandTimeoutSeconds | Out-Null

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
    chromePid           = $chromeProcess.Id
    chromeLog           = $chromeLog
    profileDir          = $resolvedProfileDir
    profileDirectory    = $ProfileDirectory
}

$stateJson = $state | ConvertTo-Json -Depth 6
Set-Content -LiteralPath $statePath -Value $stateJson -Encoding UTF8
Set-Content -LiteralPath $currentPath -Value $stateJson -Encoding UTF8

if (Test-Path -LiteralPath (Join-Path $ProjectRoot "nul")) {
    Remove-Item -LiteralPath (Join-Path $ProjectRoot "nul") -Force
}

Write-Host "Background capture is recording."
Write-Host "Use the Chrome window with CDP port $Port."
Write-Host "Capture id: $CaptureId"
Write-Host "State: $statePath"
