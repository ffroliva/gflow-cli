# gflow-cli User Guide

> **Goal:** task-oriented walkthroughs for the most common workflows. Reference material (every flag, every env var, every error class) lives in [USAGE.md](USAGE.md), [CONFIGURATION.md](CONFIGURATION.md), [AUTHENTICATION.md](AUTHENTICATION.md), and [ARCHITECTURE.md](ARCHITECTURE.md). This guide tells you *what to do* in concrete situations.

**Audience:** anyone with a Google account and Flow access who wants to drive Flow (Veo + Imagen) from the terminal instead of the web UI.

**Prerequisites:**
- Python 3.11+ (required for `from source` installs; `uvx` / `uv tool install` ship a managed Python)
- A Google account with access to Google Flow (a paid AI Pro/Ultra plan only affects credit allowances)
- ~500 MB disk (Chromium binary via Playwright)

---

## Table of contents

- [Journey 1 — First-time setup (10 minutes)](#journey-1--first-time-setup-10-minutes)
- [Journey 2 — Your first video (single t2v)](#journey-2--your-first-video-single-t2v)
- [Journey 3 — Batch video from a prompt list (shell loop)](#journey-3--batch-video-from-a-prompt-list-shell-loop)
- [Journey 4 — Multi-image text-to-image fan-out](#journey-4--multi-image-text-to-image-fan-out)
- [Journey 5 — Image-to-image with reference images](#journey-5--image-to-image-with-reference-images)
- [Journey 6 — Reading structured logs (`jq` recipes)](#journey-6--reading-structured-logs-jq-recipes)
- [Journey 7 — Recovering from an `AuthExpiredError` mid-run](#journey-7--recovering-from-an-authexpirederror-mid-run)
- [Journey 8 — Switching between Google accounts (profiles)](#journey-8--switching-between-google-accounts-profiles)
- [Journey 9 — Migrating from `FLOW_CLI_*` to `GFLOW_CLI_*` (v0.3.x → v0.4.x)](#journey-9--migrating-from-flow_cli_-to-gflow_cli_-v03x--v04x)
- [Journey 10 — Budgeting credits before a batch run](#journey-10--budgeting-credits-before-a-batch-run)
- [Journey 11 — Wiring gflow outputs into a downstream pipeline](#journey-11--wiring-gflow-outputs-into-a-downstream-pipeline)
- [Journey 12 — Recovering from `ContentPolicyError` or `RateLimitError`](#journey-12--recovering-from-contentpolicyerror-or-ratelimiterror)
- [Journey 13 — Diagnosing a persistent `AuthExpiredError` after a successful re-login](#journey-13--diagnosing-a-persistent-authexpirederror-after-a-successful-re-login)
- [Journey 14 — Embedding `FlowApiClient` in a long-lived worker](#journey-14--embedding-flowapiclient-in-a-long-lived-worker)
- [Journey 15 — Sending generated assets to S3, MinIO, or GCS](#journey-15--sending-generated-assets-to-s3-minio-or-gcs)
- [Journey 16 — Consistent characters across a video (`@Name` mentions)](#journey-16--consistent-characters-across-a-video-name-mentions)
- [Common errors quick-reference](#common-errors-quick-reference)

---

## Journey 1 — First-time setup (10 minutes)

You've just installed `gflow-cli` and need to get to your first successful API call.

### 1.1 Install

Pick one — they're equivalent for getting `gflow` on your `PATH`:

```bash
# Zero-install (try before committing):
uvx --from gflow-cli gflow --help

# Install as a user tool (recommended for regular use):
uv tool install gflow-cli
```

Both install `gflow` (and a `flow` alias) globally for your user. Later, `gflow update`
upgrades in place through whichever installer you used, and every command shows a banner
when a newer release is on PyPI — see [USAGE § `gflow update`](USAGE.md#gflow-update).

### 1.2 One-time browser dependency

Playwright needs a Chromium binary. Run once after installation:

```bash
uv tool run --from gflow-cli playwright install chromium
# or via uvx:
uvx --from gflow-cli playwright install chromium
```

This is a ~150 MB download. It happens once per user.

### 1.3 Authenticate

```bash
gflow auth login
```

A Chromium window opens. Sign in to the Google account you use for Flow. **Solve any captchas Google shows you** — `gflow-cli` cannot solve them; that's intentional (anti-bot detection). When the Flow dashboard loads, return to your terminal and confirm.

Your session is saved under (one of):
- Windows: `%LOCALAPPDATA%\gflow-cli\profile_default\`
- macOS: `~/Library/Application Support/gflow-cli/profile_default/`
- Linux: `~/.local/share/gflow-cli/profile_default/` (XDG)

The session cookies persist across reboots until Google invalidates them — typically weeks.

### 1.4 Verify

```bash
gflow auth status
```

Expected: `Flow session verified as <your account>.` and exit code 0 — the command probes the live Flow session (no browser, no credits). If it exits 1, follow the printed hint (usually re-run `gflow auth login`).

You're done. Skip to [Journey 2](#journey-2--your-first-video-single-t2v).

---

## Journey 2 — Your first video (single t2v)

```bash
gflow video t2v "a hot air balloon over Tokyo at sunrise"
```

What happens:

1. `gflow-cli` reuses the saved Playwright session — no browser opens (headless).
2. It creates an ephemeral Flow project, mints a reCAPTCHA Enterprise token via the live page, POSTs to `aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText` (see [`samples/README.md`](../samples/README.md) for the captured request/response shapes).
3. Polls the operation every ~5 seconds via `POST /v1/video:batchCheckAsyncVideoGenerationStatus` until a terminal status.
4. Downloads the resulting `.mp4` to a date-partitioned path under `$GFLOW_CLI_OUTPUT_DIR/videos/<YYYY-MM-DD>/`.

**Typical wall-clock:** 60–180 seconds for an 8-second Veo clip. Imagen image jobs (`gflow image t2i` / `i2i`) finish in 10–30 seconds — closer to a single shot.

**Cost:** ~1 credit per ~8-second clip on Ultra. Watch your credits at <https://labs.google/fx/tools/flow>.

**Choose your aspect ratio:**

```bash
gflow video t2v "a steam locomotive at dusk" \
    --aspect 16:9 \
    --out-dir ./out
```

There is no CLI seed knob — an identical prompt + model can still vary between runs (Veo is non-deterministic).

---

## Journey 3 — Batch video from a prompt list (shell loop)

You want to render 20 clips overnight. `gflow video` has no `batch`
subcommand — a manifest-driven runner was scaffolded early on but never
worked and has been removed. (`gflow image batch` is a different, working
command — see [Journey 4](#journey-4--multi-image-text-to-image-fan-out) and
[Journey 10](#journey-10--budgeting-credits-before-a-batch-run).) For video,
drive each generation from a plain shell loop instead — one `gflow`
invocation per prompt, sequential, so a bad row doesn't torpedo the rest.

### 3.1 Write the prompt list

One prompt per line:

```
a kite over a beach
a hot air balloon takes off
a candle flickering in a window
```

Save as e.g. `prompts.txt`.

### 3.2 Run the loop

```bash
# bash / WSL / macOS
while IFS= read -r prompt; do
    [ -z "$prompt" ] && continue
    gflow video t2v "$prompt" --aspect 16:9 --out-dir ./out || echo "failed: $prompt (exit $?)"
done < prompts.txt
```

```powershell
# PowerShell
Get-Content prompts.txt | Where-Object { $_ -ne "" } | ForEach-Object {
    gflow video t2v $_ --aspect 16:9 --out-dir ./out
    if ($LASTEXITCODE -ne 0) { Write-Warning "failed: $_ (exit $LASTEXITCODE)" }
}
```

Each iteration is its own process, so one bad prompt (e.g. `AuthExpiredError`
mid-run) exits with the appropriate code (3, 4, 5, 6, or 7) without
cancelling the rest of the loop the way a single fanned-out batch would.
In-flight requests that Flow had already accepted when a failure occurs
**may still consume credits** — Flow's private API does not give us a
"cancel-and-refund" handshake. Inspect the output directory before
rerunning; skip prompts whose expected `output_path` already exists (recipe
in [Journey 11](#journey-11--wiring-gflow-outputs-into-a-downstream-pipeline)).

By default each call opens its own Flow project; pass the same `--project
<id>` to every call if you want the clips to land in one project instead
(see [`docs/USAGE.md` § Sharing one project across calls](USAGE.md#sharing-one-project-across-calls)).

**Watch progress:**

```bash
GFLOW_CLI_LOG_FORMAT=json bash -c '
while IFS= read -r prompt; do
    gflow video t2v "$prompt" --aspect 16:9 --out-dir ./out
done < prompts.txt
' 2> events.jsonl &
tail -f events.jsonl | jq -c 'select(.event != "")'
```

Every operation emits structured events with `correlation_id`, `cli_command`, and timing.

---

## Journey 4 — Multi-image text-to-image fan-out

You want 4 variations of a single prompt.

```bash
gflow image t2i "a peaceful mountain lake at dawn" -n 4 --model nano-pro
```

What happens:

1. One shared `batch_id` is minted.
2. Four parallel POSTs to `batchGenerateImages`, each with its own random seed and a freshly-minted reCAPTCHA token (the tokens are single-use — minting per-shot is required).
3. Four signed `fifeUrl` responses arrive (each is a CDN URL on `flow-content.google` valid for ~5 minutes).
4. Four files land at `$GFLOW_CLI_OUTPUT_DIR/images/<YYYY-MM-DD>/<media_name>_1.png` through `_4.png`.

**Model aliases:**
- `nano2` → `NARWHAL` (Nano Banana 2; default, fastest)
- `nano-pro` → `GEM_PIX_2` (Nano Banana Pro; higher quality)
- `image4` → `IMAGEN_3_5` (Imagen 4; photoreal lean)

**Aspect ratios:** `9:16` (default), `16:9`, `1:1`, `4:3`, `3:4`.

**Custom output dir:**

```bash
gflow image t2i "..." -n 4 --out ./hero_concepts/
```

With `--out`, files write FLAT (no `images/<date>/` suffix).

---

## Journey 5 — Image-to-image with reference images

Refine an existing image, OR seed a generation from a previous upload.

### 5.1 Auto-upload a local file as a reference

```bash
gflow image i2i "make it stormy" --ref ./hero.png
```

`./hero.png` is uploaded once and used as the reference. The returned UUID could be reused on subsequent calls.

### 5.2 Reuse a previously-uploaded asset

```bash
gflow image upload ./hero.png
# → ddb6ef97-262d-49f4-8269-4a28c0fae6a2

gflow image i2i "now at sunset" --ref ddb6ef97-262d-49f4-8269-4a28c0fae6a2
```

`--ref` accepts EITHER a local path OR a 32-char hex UUID (with hyphens) — `gflow-cli` classifies by regex and skips the upload if it's already a UUID.

### 5.3 Mix and match

```bash
gflow image i2i "in the style of a vintage poster" \
    --ref ./hero.png \
    --ref media-uuid-existing \
    -n 4
```

The first `--ref` triggers a single upload; the second is used verbatim. Both flow into `imageInputs[]` on the API call in the order given.

---

## Journey 6 — Reading structured logs (`jq` recipes)

Every error event is machine-parseable JSON. Use `GFLOW_CLI_LOG_FORMAT=json` to force JSON even on a TTY.

### 6.1 Capture a session to disk

```bash
GFLOW_CLI_LOG_FORMAT=json gflow video t2v "..." 2> events.jsonl
```

### 6.2 Filter by event type

```bash
# All errors that were raised:
jq -c 'select(.event == "error_raised")' events.jsonl

# Only auth-expired:
jq -c 'select(.event == "error_raised" and .error_class == "AuthExpiredError")' events.jsonl
```

### 6.3 Investigate a WireFormatError

`WireFormatError` carries discovery fields that let you see what Flow returned without exposing tokens:

```bash
jq -c '
    select(.error_class == "WireFormatError") |
    {route: .discovery.route_name,
     status: .discovery.http_status,
     content_type: .discovery.content_type,
     keys: .discovery.top_level_keys,
     prefix: .discovery.body_prefix_redacted}
' events.jsonl
```

`body_prefix_redacted` is the first 200 chars of the body **after** `_redact_for_log` strips known token patterns. File a bug at <https://github.com/ffroliva/gflow-cli/issues> and paste this output — it's the actionable diagnostic, NOT your auth cookies.

### 6.4 Group recurring unhandled errors

`error_unhandled` events carry SHA-256 hashes (not raw messages) so you can group across runs without leaking PII:

```bash
jq -c 'select(.event == "error_unhandled") | .message_hash' events.jsonl | sort | uniq -c | sort -rn
```

---

## Journey 7 — Recovering from an `AuthExpiredError` mid-run

You started an overnight 50-clip shell loop (see [Journey 3](#journey-3--batch-video-from-a-prompt-list-shell-loop)). You wake up. Prompt 23 onward all failed with exit code 3.

### 7.1 Diagnose

The loop from Journey 3 prints `failed: <prompt> (exit $?)` for each failing iteration, so scroll back to find where it started. If you've lost the terminal scrollback, the same information is in the structured log:

```bash
jq -r 'select(.error_class == "AuthExpiredError")' events.jsonl
```

Then check current session state:

```bash
gflow auth status
# A dead session exits 1 and says so, e.g.:
#   "Signed in to Google, but not to the Flow app." + a gflow auth login hint.
```

### 7.2 Refresh

```bash
gflow auth login --profile <name>
```

A Chromium window opens. Sign in again. Cookies overwrite.

### 7.3 Skip already-rendered entries

A shell loop doesn't cancel remaining iterations on failure — once auth expires, every remaining prompt fails the same way (fast, before submission, so no extra credits burn) but the loop keeps grinding to the end of the list. Check `$GFLOW_CLI_OUTPUT_DIR/videos/<today>/` for what already rendered, then:

- Either: trim `prompts.txt` down to the prompts with no matching output file, and rerun just those.
- Or: rerun the full prompt list — **be aware that doing so may re-issue paid generations** for prompts that already succeeded. gflow-cli does not maintain a local manifest-of-outputs to skip already-rendered prompts, so dedup is on you (compare against `$GFLOW_CLI_OUTPUT_DIR` before rerunning).

### 7.4 Prevent recurrence

Run `gflow auth login` weekly as a cron / scheduled task. Sessions on a long-running automation box drift faster than on interactive machines.

---

## Journey 8 — Switching between Google accounts (profiles)

You have a personal Google AI Ultra account and a work one. Keep them separate.

```bash
# Set up the second profile:
gflow auth login --profile work

# Per-call selection:
gflow image t2i "..." --profile work

# Or set the default:
gflow auth use work

# Verify the active default:
gflow auth list
```

Each profile is a fully-independent Playwright persistent context with its own cookies, localStorage, IndexedDB, and download state. No cross-contamination.

---

## Journey 9 — Migrating from `FLOW_CLI_*` to `GFLOW_CLI_*` (v0.3.x → v0.4.x)

`v0.4.0a1` renamed the env var prefix from `FLOW_CLI_*` to `GFLOW_CLI_*` to match the PyPI distribution name. **Legacy names still work in v0.4.x** with a `DeprecationWarning` to stderr; they're removed in v0.5.0.

### 9.1 What to change in `.env`

```diff
- FLOW_CLI_HOME=/secure/path
- FLOW_CLI_CONCURRENCY=4
- FLOW_CLI_LOG_FORMAT=json
+ GFLOW_CLI_HOME=/secure/path
+ GFLOW_CLI_CONCURRENCY=4
+ GFLOW_CLI_LOG_FORMAT=json
```

All other vars follow the same `FLOW_CLI_` → `GFLOW_CLI_` rename. The full set:
`HOME`, `OUTPUT_DIR`, `PROFILE`, `HEADLESS`, `LOG_LEVEL`, `LOG_FORMAT`, `PROVIDER`, `TIMEOUT_SECONDS`, `CONCURRENCY`.

### 9.2 What to change in scripts

```diff
- export FLOW_CLI_PROFILE=work
+ export GFLOW_CLI_PROFILE=work
```

### 9.3 Python imports (only if you used the SDK directly)

```diff
- from flow_cli.api.client import FlowApiClient
+ from gflow_cli.api.client import FlowApiClient
```

Only the **Python import path** changed (`flow_cli` → `gflow_cli`). The PyPI distribution name (`gflow-cli`), the CLI binary (`gflow`), and the user data directory (`gflow-cli/` under `platformdirs`) are **unchanged**.

---

## Journey 10 — Budgeting credits before a batch run

Your Google plan includes a finite Veo credit allowance — the video journeys spend real money. Before kicking off a 200-prompt run, get a rough cost estimate.

### 10.1 Check the balance

Check the current Veo video-credit balance for the selected profile. The normal path uses direct
HTTP and does not open a browser; a browser remains the fallback when saved-cookie decryption or
the HTTP path fails:

```bash
gflow credits user

# Or inspect every saved profile while preserving partial results.
gflow credits list
```

Both commands accept `--json` for automation. You can also inspect the balance in Flow directly:

- <https://labs.google/fx/tools/flow> — bottom-right "Credits" badge.
- <https://gemini.google/subscriptions/> — monthly usage report.

This balance is drawn down by Veo video generation only. Image generation uses separate per-model
daily quotas and is not represented by this number.

### 10.2 Rule-of-thumb credit cost per call

Costs are not published by Google. The numbers below are **rough observations from `samples/captured/` runs** and may drift as Flow tunes pricing — always test with a small batch first.

| Command | Rough cost (Ultra) | Notes |
|---|---|---|
| `gflow video t2v "..."` (Veo 3.1 Fast, ~8 s clip) | ~1 credit / clip | Most expensive surface. |
| `gflow video i2v IMAGE "..."` (Veo 3.1 Fast, ~8 s clip) | ~1 credit / clip | Same as t2v in practice. |
| `gflow image upload PATH` | 0 Veo credits | Asset upload is free. |
| `gflow image t2i "..." --model nano2 -n 1` | Separate image quota | Uses that model's daily image allowance, not the displayed Veo balance. |
| `gflow image t2i "..." --model nano-pro -n 1` | Separate image quota | Uses that model's daily image allowance. |
| `gflow image t2i "..." --model image4 -n 1` | Separate image quota | Uses that model's daily image allowance. |
| `gflow image i2i "..." --ref PATH -n 1` | Separate image quota | Reference upload is free; generation uses the model quota. |
| `gflow image t2i "..." -n 4` | 4 image-quota operations | Fan-out issues N generation requests. |

**Failed generations may still bill.** Retries inside the tenacity loop are atomic — only the final accepted attempt is what counts — but if Flow accepts a generation and then returns a `ContentPolicyError`, **the credit is generally already gone**.

### 10.3 Batch-cost math

For a 50-line `prompts.txt` of `gflow video i2v` clips (driven from the shell loop in [Journey 3](#journey-3--batch-video-from-a-prompt-list-shell-loop)) at ~1 credit / clip:

```text
50 clips × ~1 credit/clip = ~50 credits.
+ Retries: tenacity caps at 3 attempts per prompt, but only the successful attempt
  is "the" generation. Budget +5-10% headroom for transient failures that did
  succeed on the first try (≈ 55 credits worst case).
```

### 10.4 Dry-run before committing the batch

The cheapest "dry run" today is to **run the first 2 lines of `prompts.txt`** end-to-end and inspect the outputs + log:

```bash
head -2 prompts.txt > prompts.preview.txt
while IFS= read -r prompt; do
    gflow video t2v "$prompt" --aspect 16:9 --out-dir ./preview/
done < prompts.preview.txt
```

If the preview clips look right, run the full prompt list. If they're wrong, fix the prompts before spending credits at scale.

A `--dry-run` flag that validates prompts + estimates cost without making any paid calls is planned (not yet scheduled).

---

## Journey 11 — Wiring gflow outputs into a downstream pipeline

You want to feed `gflow-cli`'s output into `ffmpeg`, a CMS, or another automation step.

### 11.1 Explicit output path with `-o` / `--output`

Pass `-o` / `--output` to land the generated file directly at a custom destination:

```bash
# Save directly to a specific file (auto-creates parent directories)
gflow image t2i "cinematic hero scene" -o ./assets/hero.png
gflow video t2v "sunset over ocean" -o ./render/clip.mp4

# Multi-count generations automatically receive _1, _2 stem suffixes
gflow image t2i "concept art" -n 2 -o ./concepts/art.png
# → ./concepts/art_1.png and ./concepts/art_2.png
```

Silent overwrite semantics: `-o` overwrites any existing file at the target path. Extension correction: if `-o asset.jpg` receives a PNG payload, `gflow` corrects the output key extension appropriately.

### 11.2 The deterministic default layout

When `-o` is omitted, default outputs and `--out-dir` flags produce **predictable paths**:

If `GFLOW_CLI_STORAGE_URI` is set, generated asset bytes go to the configured
bucket prefix instead of local disk. Use [Journey 15](#journey-15--sending-generated-assets-to-s3-minio-or-gcs)
for the storage setup, then use `gflow data media <media-id>` to retrieve the
recorded `cloud_uri_N` values.

### 11.2 Enumerate today's renders (POSIX shell)

```bash
TODAY=$(date +%F)
find "$GFLOW_CLI_OUTPUT_DIR/videos/$TODAY/" -name '*.mp4' -newer ./manifest.tsv -print
```

### 11.3 Enumerate today's renders (PowerShell)

```powershell
$today = Get-Date -Format 'yyyy-MM-dd'
Get-ChildItem "$env:GFLOW_CLI_OUTPUT_DIR\videos\$today\" -Filter *.mp4 |
    Where-Object { $_.LastWriteTime -gt (Get-Item .\manifest.tsv).LastWriteTime }
```

### 11.4 Chain into ffmpeg (concat all of today's clips)

```bash
TODAY=$(date +%F)
DIR="$GFLOW_CLI_OUTPUT_DIR/videos/$TODAY"
( cd "$DIR" && for f in *.mp4; do echo "file '$f'"; done > concat.txt )
ffmpeg -f concat -safe 0 -i "$DIR/concat.txt" -c copy "$DIR/_compiled.mp4"
```

### 11.5 Trim the prompt list to unrendered prompts only (skip-existing workaround)

The shell loop from [Journey 3](#journey-3--batch-video-from-a-prompt-list-shell-loop) doesn't skip-existing yet. To rerun only the prompts whose expected output doesn't exist yet, keep a sidecar mapping of prompt → expected filename and filter against it:

```bash
awk -F'\t' 'NR==FNR { done[$1]; next } !($0 in done)' rendered.txt prompts.txt > prompts.remaining.txt
while IFS= read -r prompt; do
    gflow video t2v "$prompt" --aspect 16:9 --out-dir ./out
done < prompts.remaining.txt
```

(`rendered.txt` is whatever record you keep of prompts already rendered — e.g. `ls ./out/*.mp4` mapped back to the prompt that produced each file, or a log you append to after each successful call.)

### 11.6 Subscribe a Python pipeline to new files (inotify / FSEvents / watchdog)

```python
# pip install watchdog
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

class OnNewClip(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory or not event.src_path.endswith(".mp4"):
            return
        clip = Path(event.src_path)
        # ... hand off to your next step (upload, transcode, post to CMS) ...

obs = Observer()
obs.schedule(OnNewClip(), path=str(Path.home() / "Downloads" / "gflow-cli" / "videos"),
             recursive=True)
obs.start()
```

---

## Journey 12 — Recovering from `ContentPolicyError` or `RateLimitError`

You see exit code 4 or 5. Different mitigations apply.

### 12.1 Exit 5 — `ContentPolicyError`

Flow's safety classifier blocked the prompt or generated output. Common triggers:

- **Named real people** (e.g. "Brad Pitt eating ramen"). Flow blocks identifiable likenesses by default.
- **Brand and product names** ("Pepsi can rolling down a hill"). Trademark / IP filters fire here.
- **Explicit content** (sexual, graphic violence, self-harm). Hard-blocked.
- **Children** (any reference to "kids", "a child", named ages under 18). Blocked.
- **Political figures** / electoral content. Flow blocks these in many jurisdictions.

**Retry is futile until you rewrite the prompt.** The retry loop sees `ContentPolicyError` as non-retryable and exits immediately.

**Rewrite patterns that often work:**

| Failing prompt | Try instead |
|---|---|
| "Brad Pitt eating ramen" | "A handsome 40-year-old man with blond hair eating ramen" |
| "A Pepsi can rolling" | "A red soda can rolling" |
| "Two children playing" | "Two stylized cartoon characters playing" |

Re-run the single failing row in isolation while iterating:

```bash
gflow video t2v "your rewritten prompt" -o ./debug/test.mp4
```

Once it passes, fix the prompt in `prompts.txt` and rerun the loop.

### 12.2 Exit 4 — `RateLimitError`

Flow returned `429 Too Many Requests`. The `tenacity` retry layer already retried up to 3× with exponential jittered backoff (1 s → 2 s → 4 s, ±25% jitter, capped at 60 s when `Retry-After` is set). If it still failed, you're hitting either:

- **Sustained rate limit** — the API thinks you're spamming. Wait and resume.
- **Daily quota** — your plan's allowance for the day is exhausted. Check the credit balance UI.

**Recovery steps, in order:**

```bash
# 1. A shell loop is already sequential — the lever here is spacing out
#    requests. Add a sleep between prompts and rerun the remaining ones
#    (see Journey 11.5 for building prompts.remaining.txt):
while IFS= read -r prompt; do
    gflow video t2v "$prompt" --aspect 16:9 --out-dir ./out
    sleep 5
done < prompts.remaining.txt

# 2. If still 429: wait. 60 seconds for transient, up to a few hours for daily.
sleep 300

# 3. If the wait clears it, shrink or drop the inter-prompt sleep again.

# 4. If the wait does not clear it: check Flow's web UI. If credits are zero or
#    the dashboard shows quota exhausted, you must wait for the quota window
#    to reset.
```

The structured event lets you spot the underlying cause:

```bash
jq -r 'select(.error_class == "RateLimitError") | {detail, route, retry_after: .problem.retry_after}' events.jsonl
```

If `retry_after` is present and reasonable, the retry-loop wait that's used per attempt; if absent, treat as daily-quota.

### 12.3 When neither pattern applies

If exit 4 or 5 keeps firing on a prompt that looks tame:

1. Capture the full `error_raised` event with `2> events.jsonl`.
2. Open an issue at <https://github.com/ffroliva/gflow-cli/issues> with the redacted prompt + the event dict.
3. While waiting, work around with a paraphrased prompt and lower concurrency.

---

## Journey 13 — Diagnosing a persistent `AuthExpiredError` after a successful re-login

**Symptom:** `gflow image t2i` (or any other command) exits with code 3 and the message
`Authentication expired: HTTP 401` on route `project.createProject`, even though
`gflow auth login` reported **`[OK] Session captured and verified.`** moments before.
`gflow auth list` shows `Session: present` for the profile. Nothing seems wrong — yet
every API call dies immediately.

This is a distinct failure mode from the ordinary session-expiry case (Journey 7). The
cause is a mismatch between the browser that *created* the profile cookies and the browser
that the API client later uses to *read* them.

---

### 13.1 Background: why two browsers matter on Windows

Chrome 130 + on Windows encrypts its cookie store with **app-bound AES-256** on top of
DPAPI. Only a Chrome binary launched with the matching `user-data-dir` can decrypt those
bytes. Playwright's bundled Chromium does not share Chrome's encryption key; if it opens
a profile written by Chrome it reads zero auth cookies and therefore sends no `Cookie:`
header, causing every authenticated API call to return HTTP 401.

`gflow-cli` avoids this by writing a `.gflow_browser_strategy` marker file into each
profile directory when `RealChromeStrategy` creates the session. `FlowApiClient` reads the
marker at startup and passes `channel="chrome"` to `launch_persistent_context`, ensuring
the same Chrome binary opens the profile for API calls.

If the marker is absent (profile created before v0.6.0a2, marker manually deleted, or the
profile directory was seeded from a Chrome user-data-dir by hand), the API client falls
back to bundled Playwright Chromium and cannot decrypt the cookies.

---

### 13.2 Confirm the diagnosis

**Step 1 — check the marker:**

```bash
# Linux / macOS
cat ~/.local/share/gflow-cli/profile_<name>/.gflow_browser_strategy
# → "chrome" means OK; no file or "internal" means the client will use bundled Chromium

# Windows PowerShell
type $env:LOCALAPPDATA\ffroliva\gflow-cli\profile_<name>\.gflow_browser_strategy
```

If the file is missing or its content is not `chrome`, the marker is the problem.

**Step 2 — count the auth cookies the API client actually sees:**

```python
# uv run python  (or python3 in your venv)
import asyncio
from playwright.async_api import async_playwright

async def probe(profile_dir: str, channel: str | None = None):
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir, channel=channel, headless=True
        )
        cookies = await ctx.cookies(["https://labs.google"])
        auth = [c for c in await ctx.cookies() if c["name"] in
                {"SAPISID", "SID", "SSID", "__Secure-1PSID",
                 "__Secure-next-auth.session-token"}]
        print(f"labs.google cookies: {len(cookies)}, auth cookies: {len(auth)}")
        for c in auth:
            print(f"  {c['name']:40} domain={c['domain']}")
        await ctx.close()

asyncio.run(probe(r"C:\Users\<you>\AppData\Local\ffroliva\gflow-cli\profile_<name>"))
```

- **`auth cookies: 0` with `channel=None`** → Playwright cannot decrypt; this is the bug.
- **`auth cookies: N` with `channel="chrome"`** → cookies are valid, just unreadable
  without the marker.
- **`auth cookies: 0` with both** → the session is genuinely not authenticated; skip
  straight to step 13.3.

**Step 3 — inspect the raw SQLite database (quick sanity check):**

```bash
# Windows PowerShell
$db = "$env:LOCALAPPDATA\ffroliva\gflow-cli\profile_<name>\Default\Network\Cookies"
# (fallback path: profile_<name>\Default\Cookies  or  profile_<name>\Cookies)
uv run python -c "
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
rows = conn.execute('SELECT name, host_key FROM cookies').fetchall()
print(f'{len(rows)} cookies total')
for name, host in rows: print(f'  {name:35} {host}')
" "$db"
```

Four or fewer rows — all `__Secure-next-auth.csrf-token`, `__Host-next-auth.csrf-token`,
`NID`, or `_GRECAPTCHA` — means only pre-authentication cookies were saved. The sign-in
was never completed, or the cookies were never written/have been cleared.

---

### 13.3 Fix: re-authenticate with `--browser chrome`

```bash
gflow auth login --profile <name>
# "auto" mode picks real Chrome when it's installed — the usual case.
# Explicit form if you want to be certain:
gflow auth login --profile <name> --browser chrome
```

1. Chrome opens to `https://labs.google/fx/tools/flow?hl=en`.
2. Sign in to the Google account you use for Flow.
3. When the Flow editor loads, **close Chrome**.
4. `gflow auth login` probes the profile with `channel="chrome"`, verifies SAPISID is
   present, and writes `.gflow_browser_strategy = "chrome"` to the profile directory.
5. Subsequent `gflow image` / `gflow video` calls will use Chrome to open the profile and
   can decrypt the cookies.

Verify recovery:

```bash
gflow auth list
# Session column should say "present"

gflow image t2i "a single red apple on a white table" -n 1 --out /tmp/smoke
# Should complete without AuthExpiredError
```

---

### 13.4 Manual marker repair (when the session IS valid but the marker is missing)

If step 2 confirmed auth cookies are present with `channel="chrome"` (N > 0), you can
skip re-login and just write the marker:

```bash
# Linux / macOS
echo -n "chrome" > ~/.local/share/gflow-cli/profile_<name>/.gflow_browser_strategy

# Windows PowerShell
"chrome" | Set-Content `
    "$env:LOCALAPPDATA\ffroliva\gflow-cli\profile_<name>\.gflow_browser_strategy" `
    -Encoding utf8 -NoNewline
```

Verify with a smoke test as above.

---

### 13.5 Prevent recurrence

- **Always use `gflow auth login` (not raw Chrome) to create or refresh profiles.** The
  CLI handles marker creation; manual browser runs do not.
- **After upgrading from pre-v0.6.0a2:** run `gflow auth login --profile <name>` once per
  existing profile; the marker will be written and stays current.
- **On shared / CI machines:** store the profile directory in a version-controlled
  secrets store and avoid letting any other Chrome process touch it — Chrome
  re-encrypts cookies on first open, which silently breaks profiles accessed by other
  tools.

---

## Common errors quick-reference

| You see… | Exit | Likely cause | Fix |
|---|---|---|---|
| `Authentication expired: HTTP 401` | 3 | Session cookies invalidated | `gflow auth login --profile <name>` |
| `Rate limit or quota hit: HTTP 429` | 4 | Burst exceeded Flow's quota | Wait 1 min; reduce `GFLOW_CLI_CONCURRENCY`; check credits |
| `Content policy rejection: empty media[]` | 5 | Flow rejected the prompt | Soften wording; avoid disallowed entities |
| `Network failure persisted across retries` | 6 | Transient infrastructure or upstream | Check connectivity; retry after a few minutes |
| `Unexpected response shape from Flow` | 7 | Flow API changed schema | File a bug with the `body_prefix_redacted` discovery payload |
| `Unexpected error.` | 1 | Anything not derived from `GFlowError` | Re-run with `--verbose`; if persistent, file a bug |
| `SIGINT (Ctrl-C)` | 130 | You interrupted | — |

Full exit-code table with shell-script branching examples: [USAGE § Exit codes](USAGE.md#exit-codes).

---

## Journey 14 — Embedding `FlowApiClient` in a long-lived worker

**Goal:** run `FlowApiClient` inside a service or background worker that handles many jobs over its lifetime without restarting the browser between calls.

### 14.1 The minimal worker loop

```python
import asyncio
from pathlib import Path
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import GenerateImageRequest, Model
from gflow_cli.exceptions import GFlowError   # standard alias for gflow_cli.errors
from gflow_cli.config import get_settings

async def worker(job_queue: asyncio.Queue) -> None:
    settings = get_settings()
    profile_dir = settings.profile_subdir("default")

    async with FlowApiClient(profile_dir=profile_dir, headless=True) as client:
        while True:
            job = await job_queue.get()
            if job is None:
                break  # poison pill — graceful shutdown

            if not await client.health_check():
                # Browser context died (OS killed the process, GPU crash, etc.)
                # Re-raise so the supervisor can restart this worker.
                raise RuntimeError("FlowApiClient health check failed — browser context dead")

            try:
                image = await client.generate_image(req=job.req)
                # project_id omitted → a new Flow project is created automatically
                await client.download_image(image, job.out_path)
                job.result_queue.put_nowait(image)
            except GFlowError as exc:
                job.result_queue.put_nowait(exc)
```

Key points:
- `health_check()` is a cheap liveness probe. Call it before every dispatch — it returns `False` (never raises) so you can branch without try/except.
- `project_id` is omitted from `generate_image()`. Each job gets its own auto-created Flow project, which matches Flow's one-project-per-generation model.
- Import from `gflow_cli.exceptions` or `gflow_cli.errors` — both resolve to identical classes.

### 14.2 Catching typed errors in a worker

All gflow errors inherit from `GFlowError`. Use `EXIT_CODE_MAP` to get the canonical exit code for any error class — useful for reporting or retry decisions:

```python
from gflow_cli.exceptions import GFlowError, AuthExpiredError, RateLimitError
from gflow_cli.errors import EXIT_CODE_MAP

try:
    image = await client.generate_image(req=req)
except AuthExpiredError:
    # Exit code 3 — session expired; re-authenticate and retry
    await re_login(profile_dir)
except RateLimitError as exc:
    # Exit code 4 — back off by exc.retry_after seconds (or a safe default)
    await asyncio.sleep(exc.retry_after or 60)
except GFlowError as exc:
    code = EXIT_CODE_MAP.get(type(exc), 1)
    logger.error("generation_failed", error_class=type(exc).__name__, exit_code=code)
```

### 14.3 Checking health after a long idle period

A browser context that was idle for several minutes may have its page navigated away by a background Flow JS update. `health_check()` confirms the page is still on a Google domain before trusting the session:

```python
IDLE_THRESHOLD = 300  # seconds

last_used = time.monotonic()

async def dispatch(client: FlowApiClient, req: GenerateImageRequest) -> ...:
    if time.monotonic() - last_used > IDLE_THRESHOLD:
        if not await client.health_check():
            raise RuntimeError("session lost after idle — restart worker")
    ...
```

### 14.4 What `health_check()` does NOT cover

- It does **not** verify that the Google session (cookies) is still valid. A page can be alive and on `labs.google` with an expired session cookie — the next API call will raise `AuthExpiredError` (exit 3).
- It does **not** refresh the session. On `False`, restart the worker and re-enter `async with FlowApiClient(...)`.

---

## Journey 15 — Sending generated assets to S3, MinIO, or GCS

**Goal:** run the same image/video commands, but store generated asset bytes in
a bucket instead of the local output directory.

### 15.1 Install the storage extra

```bash
uv tool install --force "gflow-cli[s3]"   # S3 or MinIO
uv tool install --force "gflow-cli[gcs]"  # Google Cloud Storage
```

### 15.2 Configure the target

S3 or MinIO:

```bash
export GFLOW_CLI_STORAGE_URI=s3://gflow-test/prod/
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1
```

For MinIO, also set:

```bash
export AWS_ENDPOINT_URL=http://localhost:9000
```

GCS:

```bash
export GFLOW_CLI_STORAGE_URI=gs://my-gcs-bucket/gflow/
gcloud auth application-default login
```

### 15.3 Generate normally

```bash
gflow image t2i "blue cube on a white sweep" -n 2
gflow video t2v "slow dolly-in on the same blue cube" --aspect 16:9
```

With `GFLOW_CLI_STORAGE_URI` set, these commands write to the bucket instead of
local asset files. This is not a dual-write mode.

### 15.4 Verify the catalog and bucket

```bash
gflow data media <media-id>
```

Expected: one or more `cloud_uri_N` rows. Then inspect the bucket with
`aws s3 ls`, `gsutil ls`, or the MinIO console.

Deep details, object layout, and provider-specific notes live in
[EXTERNAL_STORAGE.md](EXTERNAL_STORAGE.md).

---

## Journey 16 — Consistent characters across a video (`@Name` mentions)

**Goal:** keep the *same* people on-model across a shot — or a whole sequence — by
referencing saved Characters **by name, right in the prompt**. No per-shot image
refs, no id juggling. Character mentions work on both image and video paths (media
mentions are image-only) — full rules in
[USAGE.md](USAGE.md#referencing-saved-assets-by-name-name-mentions).

### 16.1 Create the Characters once

Mint each recurring subject as a project-scoped Character. `character create`
generates its reference image, which a mention needs to resolve.

```bash
PROJECT=7fa97443-…   # a real project you own

gflow character create --project "$PROJECT" --name Zoro \
  --face-prompt "weathered swordsman, green bandana, calm eyes"
gflow character create --project "$PROJECT" --name Mika \
  --face-prompt "young duelist, silver hair, sharp jaw"
```

### 16.2 Mention them by name in the prompt

Prefix each name with `@`. Both are staged as references (checked against the
model's reference cap) and stripped from the text the model actually sees.

```bash
gflow video r2v "@Zoro hands @Mika the sword at dawn, slow push-in" \
  --project "$PROJECT"
```

`@Zoro` and `@Mika` resolve to their Characters, so the two subjects stay
recognizably themselves. Reuse the same names in every shot for continuity:

```bash
gflow video r2v "@Mika parries, @Zoro steps back — wide shot" --project "$PROJECT"
gflow video t2v "@Zoro alone on the cliff at night, rain" --project "$PROJECT"
```

### 16.3 When a mention doesn't resolve

- `Unknown mention '@Zora'. Available assets: Zoro, Mika` — typo or wrong project.
  Names are case-insensitive but must exist **in `--project`**. Exit **11**.
- `@Zoro has no reference images …` — the Character was created without one;
  re-create it (or add a reference image) before tagging.
- Need a literal `@` in the prompt (a handle, a time)? Double it: `@@` → `@`. An
  `@` glued to a word (`me@host`) is never a mention, so emails are safe.

> On the **image** paths you can also mention a saved media asset by name (`@logo`)
> and combine a `@Name` with `--ref look.png` for "this identity, in this look."
> Media mentions on the video path are not supported yet (Phase 3).

---

## See also

- [USAGE.md](USAGE.md) — every flag, every output path, every command in alphabetical order.
- [CONFIGURATION.md](CONFIGURATION.md) — every env var with default, precedence chain, and security notes.
- [EXTERNAL_STORAGE.md](EXTERNAL_STORAGE.md) — S3, MinIO, and GCS output setup.
- [AUTHENTICATION.md](AUTHENTICATION.md) — full auth flow, session storage, multi-account details, refresh strategy.
- [ARCHITECTURE.md](ARCHITECTURE.md) — modular monolith layout, per-worker Page pool, RFC 9457 Problem Details, retry layer.
- [SECURITY.md](SECURITY.md) — threat model, redaction strategy, what's on disk.
- [KNOWN_ISSUES.md](../KNOWN_ISSUES.md) — workarounds for current limitations.

_Built for Flow users who'd rather automate than click. Same Veo model, same quality, same billing — without the browser._
