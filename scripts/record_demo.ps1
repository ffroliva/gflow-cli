# scripts/record_demo.ps1
# One-shot recording prep for the v0.5.0a1 demo (AUDIT_E1 F.6).
#
# What it does (in order):
#   1. Validates prerequisites: uv, ffmpeg, gifski, GFLOW_EXAMPLE_PROFILE.
#   2. Cleans the demo output dir so the final ls shows exactly 3 fresh PNGs.
#   3. Verifies the named Playwright profile is authenticated.
#   4. Sizes the terminal to match the OBS scene.
#   5. PAUSES: you start OBS recording manually, press ENTER, the batch runs.
#   6. PAUSES again: you stop OBS recording, place the MP4 at the expected path.
#   7. Post-processes MP4 to GIF via ffmpeg + gifski.
#
# Usage:
#   $env:GFLOW_EXAMPLE_PROFILE = "denon82"
#   pwsh scripts/record_demo.ps1
#
# Pass -SkipPostProcess to do the recording only and skip the GIF conversion.
# Pass -PostProcessOnly to skip the recording and just rebuild the GIF from an existing MP4.

[CmdletBinding()]
param(
    [string]$ProfileName = $env:GFLOW_EXAMPLE_PROFILE,
    [ValidateSet("single", "batch")]
    [string]$Mode = "single",
    [string]$Prompt = "a quiet mountain lake at dawn, cinematic photography",
    [ValidateSet("9:16", "16:9", "1:1", "4:3", "3:4")]
    [string]$Aspect = "9:16",
    [ValidateSet("nano2", "nano-pro", "image4")]
    [string]$Model = "nano2",
    [string]$BatchConfig = "examples/sample_config.json",
    [string]$MasterMp4 = "docs/assets/example-run.mp4",
    [string]$OutputGif = "docs/assets/example-run.gif",
    [string]$BatchOutputDir = "gflow-output/example-batch",
    [string]$SingleOutputDir = "gflow-output/example-single",
    [int]$GifFps = 12,
    [int]$GifWidth = 960,
    [int]$GifQuality = 80,
    [switch]$SkipPostProcess,
    [switch]$PostProcessOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok([string]$msg)   { Write-Host "    ok: $msg" -ForegroundColor Green }
function Warn([string]$msg) { Write-Host "    warn: $msg" -ForegroundColor Yellow }
function Die([string]$msg)  { Write-Host "    fatal: $msg" -ForegroundColor Red; exit 1 }

function Require-Tool([string]$name, [string]$installHint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Die "'$name' not found on PATH. Install hint: $installHint"
    }
    Ok "$name found"
}

# -------- POST-PROCESS-ONLY SHORT PATH --------
if ($PostProcessOnly) {
    Step "Post-process only: rebuilding GIF from $MasterMp4"
    Require-Tool "ffmpeg" "scoop install ffmpeg"
    Require-Tool "gifski" "scoop install gifski  (or: cargo install gifski)"
    if (-not (Test-Path $MasterMp4)) { Die "Master MP4 missing at $MasterMp4" }
    & ffmpeg -y -i $MasterMp4 -vf "fps=$GifFps,scale=${GifWidth}:-1:flags=lanczos" -f yuv4mpegpipe - `
        | & gifski -o $OutputGif --fps $GifFps --width $GifWidth --quality $GifQuality -
    if ($LASTEXITCODE -ne 0) { Die "ffmpeg | gifski pipeline failed" }
    $sizeMb = [math]::Round((Get-Item $OutputGif).Length / 1MB, 2)
    Ok "GIF written: $OutputGif ($sizeMb MB)"
    if ($sizeMb -gt 10) {
        Warn "GIF exceeds GitHub inline-embed sweet spot of 10 MB. Re-run with -PostProcessOnly -GifWidth 800 to shrink."
    }
    exit 0
}

# -------- PREREQUISITES --------
Step "Checking prerequisites"
Require-Tool "uv" "https://docs.astral.sh/uv/"
Require-Tool "gflow" "uv tool install gflow-cli  (so 'gflow' is on PATH for a clean recording)"
if (-not $SkipPostProcess) {
    Require-Tool "ffmpeg" "scoop install ffmpeg"
    Require-Tool "gifski" "scoop install gifski  (or: cargo install gifski)"
}
if (-not $ProfileName) {
    Die "Profile not set. Pass -ProfileName <name> or set `$env:GFLOW_EXAMPLE_PROFILE."
}
Ok "profile = $ProfileName"

# -------- PRE-RECORD --------
Step "Preparing demo environment"
$env:GFLOW_EXAMPLE_PROFILE = $ProfileName
$env:GFLOW_CLI_HEADLESS = "false"   # SHOW the Chromium dance, the visual hook
$env:PYTHONUNBUFFERED = "1"         # stream logs live

# Pick the output dir based on Mode. Clean it so the demo's final
# Get-ChildItem shows only the fresh PNGs from this take.
$OutputDir = if ($Mode -eq "single") { $SingleOutputDir } else { $BatchOutputDir }
if (Test-Path $OutputDir) {
    Remove-Item -Recurse -Force $OutputDir
    Ok "cleaned $OutputDir"
}

# Ensure docs/assets/ exists for the master MP4 and GIF output.
$assetsDir = Split-Path $MasterMp4 -Parent
if (-not (Test-Path $assetsDir)) {
    New-Item -ItemType Directory -Path $assetsDir | Out-Null
    Ok "created $assetsDir"
}

# -------- AUTH PRECHECK --------
Step "Verifying auth on profile '$ProfileName' (no quota burn yet)"
# Note: `gflow auth status` only checks for an exported state.json (used by
# HTTP transports). The default UiAutomationTransport reads cookies straight
# from the Playwright profile dir's Chromium store, which `auth status`
# cannot see. So "no session" here is often a false negative. We surface it
# as an advisory and let the real `gflow run` be the actual test.
$statusOutput = (& uv run gflow auth status 2>&1) | Out-String
Write-Host $statusOutput
if ($LASTEXITCODE -ne 0) {
    Die "gflow auth status exited $LASTEXITCODE. Cannot continue."
}
$profileDir = "$env:LOCALAPPDATA\ffroliva\gflow-cli\profile_$ProfileName"
if (-not (Test-Path $profileDir)) {
    Die "Profile dir not found: $profileDir. Run 'uv run gflow auth login --profile $ProfileName' first."
}
if ($statusOutput -match 'cookies_present:\s*False' -or
    $statusOutput -match 'has no session') {
    Warn "auth status reports no exported session - that's normal for UiAutomationTransport."
    Warn "  Cookies live INSIDE the profile dir, not in an export, so status cannot see them."
    Warn "  If the batch later fails with HTTP 401, redo:"
    Warn "    uv run gflow auth login --profile $ProfileName"
    Warn "  IMPORTANT: in the opened window, navigate to the Flow editor BEFORE closing it."
} else {
    Ok "profile reports an exported session"
}
Ok "profile dir present at $profileDir"

# Size the terminal to match the OBS scene (120 cols x 30 rows).
try {
    $Host.UI.RawUI.WindowSize = New-Object Management.Automation.Host.Size(120, 30)
    Ok "terminal sized to 120x30"
} catch {
    Warn "could not resize terminal automatically; set it manually to about 120x30 before recording"
}

# -------- RECORD --------
$modeBlurb = if ($Mode -eq "single") { "1 prompt, 1 image (~60-90s)" } else { "3 prompts, 3 ratios, 1 session (~3-4 min)" }
Write-Host ""
Write-Host "================ READY TO RECORD ================" -ForegroundColor Green
Write-Host " Mode: $Mode -- $modeBlurb"
Write-Host " 1. Position your OBS scene: terminal (left) + headed Chromium (right)."
Write-Host " 2. Start OBS recording NOW."
Write-Host " 3. Press ENTER below. The screen will clear and the demo begins."
Write-Host " 4. The recording captures ONLY the gflow command and its output -- no wrapper noise."
Write-Host " 5. After the PNGs land, the script will pause again."
Write-Host " 6. Stop OBS, save the file as: $MasterMp4"
Write-Host "==================================================" -ForegroundColor Green
Read-Host "Press ENTER once OBS is recording"

# CRITICAL: clear the screen so the recording captures ONLY gflow-cli output,
# not the prep / precheck noise above.
Clear-Host

if ($Mode -eq "single") {
    # Compose the command we want the viewer to see (as if a user typed it).
    $cmdLine = "gflow image t2i `"$Prompt`" --aspect $Aspect --model $Model --out $SingleOutputDir"
    Write-Host "PS> $cmdLine" -ForegroundColor White
    Start-Sleep -Milliseconds 1200
    & gflow image t2i $Prompt --aspect $Aspect --model $Model --out $SingleOutputDir
} else {
    # Show the config first as if `cat`-ed, then run the batch.
    Write-Host "PS> cat $BatchConfig" -ForegroundColor White
    Start-Sleep -Milliseconds 600
    Get-Content $BatchConfig
    Start-Sleep -Seconds 2

    Write-Host ""
    $cmdLine = "gflow run --config $BatchConfig"
    Write-Host "PS> $cmdLine" -ForegroundColor White
    Start-Sleep -Milliseconds 1200
    & gflow run --config $BatchConfig
}
$batchExit = $LASTEXITCODE

# Show the output as if listed manually.
Write-Host ""
Write-Host "PS> ls $OutputDir" -ForegroundColor White
Start-Sleep -Milliseconds 600
if (Test-Path $OutputDir) {
    Get-ChildItem $OutputDir | Format-Table Name, Length, LastWriteTime
} else {
    Warn "output dir not found at $OutputDir"
}

Write-Host ""
Write-Host "================ STOP OBS RECORDING ==============" -ForegroundColor Yellow
Write-Host " Save the recording as: $MasterMp4"
Write-Host "==================================================" -ForegroundColor Yellow
Read-Host "Press ENTER once the MP4 is at the path above"

if ($batchExit -ne 0) {
    Warn "batch exited with code $batchExit; recording may be unusable (re-run with the same script)"
}

# -------- POST-PROCESS --------
if ($SkipPostProcess) {
    Write-Host ""
    Write-Host "Done (recording only). Master: $MasterMp4" -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $MasterMp4)) {
    Die "Master MP4 missing at $MasterMp4. Save it there and re-run with -PostProcessOnly"
}

Step "ffmpeg + gifski: $MasterMp4 -> $OutputGif"
& ffmpeg -y -i $MasterMp4 -vf "fps=$GifFps,scale=${GifWidth}:-1:flags=lanczos" -f yuv4mpegpipe - `
    | & gifski -o $OutputGif --fps $GifFps --width $GifWidth --quality $GifQuality -
if ($LASTEXITCODE -ne 0) { Die "ffmpeg | gifski pipeline failed" }

$sizeMb = [math]::Round((Get-Item $OutputGif).Length / 1MB, 2)
Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Master:  $MasterMp4"
Write-Host "  GIF:     $OutputGif ($sizeMb MB)"
if ($sizeMb -gt 10) {
    Warn "GIF exceeds GitHub inline-embed sweet spot of 10 MB. Re-run with -PostProcessOnly -GifWidth 800 to shrink."
}
