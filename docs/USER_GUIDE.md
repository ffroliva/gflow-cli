# gflow-cli User Guide

> **Goal:** task-oriented walkthroughs for the most common workflows. Reference material (every flag, every env var, every error class) lives in [USAGE.md](USAGE.md), [CONFIGURATION.md](CONFIGURATION.md), [AUTHENTICATION.md](AUTHENTICATION.md), and [ARCHITECTURE.md](ARCHITECTURE.md). This guide tells you *what to do* in concrete situations.

**Audience:** Google AI Ultra / Pro subscribers who want to drive Flow (Veo + Imagen) from the terminal instead of the web UI.

**Prerequisites:**
- Python 3.11+ (required for `from source` installs; `uvx` / `uv tool install` ship a managed Python)
- An active Google AI Ultra or Pro subscription
- ~500 MB disk (Chromium binary via Playwright)

---

## Table of contents

- [Journey 1 — First-time setup (10 minutes)](#journey-1--first-time-setup-10-minutes)
- [Journey 2 — Your first video (single t2v)](#journey-2--your-first-video-single-t2v)
- [Journey 3 — Batch video with concurrency](#journey-3--batch-video-with-concurrency)
- [Journey 4 — Multi-image text-to-image fan-out](#journey-4--multi-image-text-to-image-fan-out)
- [Journey 5 — Image-to-image with reference images](#journey-5--image-to-image-with-reference-images)
- [Journey 6 — Reading structured logs (`jq` recipes)](#journey-6--reading-structured-logs-jq-recipes)
- [Journey 7 — Recovering from an `AuthExpiredError` mid-batch](#journey-7--recovering-from-an-authexpirederror-mid-batch)
- [Journey 8 — Switching between Google accounts (profiles)](#journey-8--switching-between-google-accounts-profiles)
- [Journey 9 — Migrating from `FLOW_CLI_*` to `GFLOW_CLI_*` (v0.3.x → v0.4.x)](#journey-9--migrating-from-flow_cli_-to-gflow_cli_-v03x--v04x)
- [Journey 10 — Budgeting credits before a batch run](#journey-10--budgeting-credits-before-a-batch-run)
- [Journey 11 — Wiring gflow outputs into a downstream pipeline](#journey-11--wiring-gflow-outputs-into-a-downstream-pipeline)
- [Journey 12 — Recovering from `ContentPolicyError` or `RateLimitError`](#journey-12--recovering-from-contentpolicyerror-or-ratelimiterror)
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

Both install `gflow` (and a `flow` alias) globally for your user.

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

A Chromium window opens. Sign in to your Google account that has the AI Ultra / Pro subscription. **Solve any captchas Google shows you** — `gflow-cli` cannot solve them; that's intentional (anti-bot detection). When the Flow dashboard loads, return to your terminal and confirm.

Your session is saved under (one of):
- Windows: `%LOCALAPPDATA%\gflow-cli\profile_default\`
- macOS: `~/Library/Application Support/gflow-cli/profile_default/`
- Linux: `~/.local/share/gflow-cli/profile_default/` (XDG)

The session cookies persist across reboots until Google invalidates them — typically weeks.

### 1.4 Verify

```bash
gflow auth status
```

Expected: `cookies_present: True` + `profile_dir: <path>`. If it says `False`, re-run `gflow auth login`.

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

**Choose your aspect ratio and seed:**

```bash
gflow video t2v "a steam locomotive at dusk" \
    --aspect 16:9 \
    --seed 4242 \
    -o ./out/locomotive.mp4
```

`--seed` is the only knob for reproducibility — same seed + prompt + model = same output (within Veo's tolerance).

---

## Journey 3 — Batch video with concurrency

You want to render 20 clips overnight from a TSV manifest.

### 3.1 Write the manifest

Five tab-separated columns, all but `prompt` optional:

```
# columns: start_image  prompt  end_image  aspect  output_path
	a kite over a beach		16:9	clips/kite.mp4
./hero.png	a hot air balloon takes off				clips/balloon.mp4
	a candle flickering in a window		1:1	clips/candle.mp4
```

`parse_manifest` treats every non-blank, non-`#`-prefixed line as data — **there is no header row.** The column-name line above is `#`-prefixed so the parser skips it. To leave a column blank, write nothing between the tabs.

Lines starting with `#` (after any leading whitespace) and blank lines are comments / spacing.

Save as e.g. `manifest.tsv`.

### 3.2 Run with concurrency

```bash
GFLOW_CLI_CONCURRENCY=4 gflow video batch manifest.tsv --out-dir ./out
```

`GFLOW_CLI_CONCURRENCY=4` opens 4 Playwright Pages inside the same persistent BrowserContext (they share cookies — same Google session) and fans out manifest entries via `asyncio.gather`. With 20 entries you'll see 4 in flight at any moment until the queue drains.

**Memory budget:** ~30-60 MiB per Page on Chromium. Don't raise `N` above 8 without measuring.

**Failure mode (today):** if any one entry fails (e.g. `AuthExpiredError` mid-batch), `asyncio.gather` cancels the siblings and the whole batch exits with the appropriate code (3, 4, 5, 6, or 7). In-flight requests that Flow had already accepted **may still consume credits** — Flow's private API does not give us a "cancel-and-refund" handshake. Inspect the output directory before rerunning; trim the manifest to only the rows whose `output_path` does not yet exist (recipe in [Journey 11](#journey-11--wiring-gflow-outputs-into-a-downstream-pipeline)).

**Soft-fail-and-continue (workaround):** if you'd rather have one bad row not torpedo the rest, drive each row from a shell loop instead of `gflow video batch`:

```bash
while IFS=$'\t' read -r start prompt _ aspect out; do
    [ -z "$prompt" ] || [ "${prompt:0:1}" = "#" ] && continue
    gflow video i2v "$start" "$prompt" -o "$out" || echo "skipped: $out (exit $?)"
done < manifest.tsv
```

Native soft-fail handling for `gflow video batch` is in the backlog (see `KNOWN_ISSUES.md`).

**Watch progress:**

```bash
GFLOW_CLI_LOG_FORMAT=json GFLOW_CLI_CONCURRENCY=4 \
    gflow video batch manifest.tsv 2> events.jsonl &
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

## Journey 7 — Recovering from an `AuthExpiredError` mid-batch

You started an overnight 50-clip batch. You wake up. The batch died on entry 23 with exit code 3.

### 7.1 Diagnose

`$?` reflects the **most recently completed foreground command**, so read it before running anything else (including `gflow auth status`):

```bash
# In the same shell the batch ran in, immediately after the batch returned:
echo "Batch exit code was: $?"      # 3 — AuthExpiredError

# Then check session state (this command will overwrite $?):
gflow auth status
# Likely output: cookies_present: True, but Google has invalidated the session.
```

If you've already lost the original `$?` (closed the terminal, ran other commands), the same information landed in the structured log: `jq -r 'select(.error_class == "AuthExpiredError")' events.jsonl`.

### 7.2 Refresh

```bash
gflow auth login --profile <name>
```

A Chromium window opens. Sign in again. Cookies overwrite.

### 7.3 Skip already-rendered entries

`asyncio.gather` cancels siblings on first failure, so SOME entries beyond 23 may have completed. Check `$GFLOW_CLI_OUTPUT_DIR/videos/<today>/` for what exists, then:

- Either: copy the surviving rows out of `manifest.tsv` and rerun the rest.
- Or: rerun the full manifest — **be aware that doing so may re-issue paid generations.** Flow's private API does not expose a "have I generated this before?" predicate, and `gflow video batch` does not yet maintain a local manifest-of-outputs to skip already-rendered rows. Filed in [KNOWN_ISSUES § `gflow video batch` does not skip already-completed entries](../KNOWN_ISSUES.md#gflow-video-batch-does-not-skip-already-completed-entries) with a one-liner `awk` workaround.

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

Your AI Ultra/Pro subscription includes a finite Veo / Imagen credit allowance — these journeys spend real money. Before kicking off a 200-row manifest, get a rough cost estimate.

### 10.1 Check the balance

Flow does not expose a credit-balance API today. Check your remaining quota at:

- <https://labs.google/fx/tools/flow> — bottom-right "Credits" badge.
- <https://gemini.google/subscriptions/> — monthly usage report.

There is no programmatic way to fetch this from `gflow-cli` yet (see [KNOWN_ISSUES § No in-CLI quota visibility](../KNOWN_ISSUES.md#no-in-cli-quota-visibility)).

### 10.2 Rule-of-thumb credit cost per call

Costs are not published by Google. The numbers below are **rough observations from `samples/captured/` runs** and may drift as Flow tunes pricing — always test with a small batch first.

| Command | Rough cost (Ultra) | Notes |
|---|---|---|
| `gflow video t2v "..."` (Veo 3.1 Fast, ~8 s clip) | ~1 credit / clip | Most expensive surface. |
| `gflow video i2v IMAGE "..."` (Veo 3.1 Fast, ~8 s clip) | ~1 credit / clip | Same as t2v in practice. |
| `gflow image upload PATH` | 0 credits | Asset upload is free; only generations bill. |
| `gflow image t2i "..." --model nano2 -n 1` | ~0 credits / image observed | Nano Banana 2 is the cheap tier. |
| `gflow image t2i "..." --model nano-pro -n 1` | ~low fractional / image | Nano Banana Pro = higher quality, higher cost. |
| `gflow image t2i "..." --model image4 -n 1` | ~low fractional / image | Imagen 4 = photoreal lean. |
| `gflow image i2i "..." --ref PATH -n 1` | Same as t2i for the model | Reference uploads are free. |
| `gflow image t2i "..." -n 4` | 4× the single-shot cost | Fan-out issues N parallel POSTs, one credit-event per shot. |

**Failed generations may still bill.** Retries inside the tenacity loop are atomic — only the final accepted attempt is what counts — but if Flow accepts a generation and then returns a `ContentPolicyError` or your manifest later cancels via `asyncio.gather`, **the credit is generally already gone**.

### 10.3 Batch-cost math

For a 50-row manifest of `gflow video i2v` clips at ~1 credit / clip:

```text
50 clips × ~1 credit/clip = ~50 credits.
+ Retries: tenacity caps at 3 attempts per row, but only the successful attempt
  is "the" generation. Budget +5-10% headroom for transient failures that did
  succeed on the first try (≈ 55 credits worst case).
```

### 10.4 Dry-run before committing the batch

The cheapest "dry run" today is to **run the manifest's first 2 rows** end-to-end and inspect the outputs + log:

```bash
head -3 manifest.tsv > manifest.preview.tsv     # 1 comment line + 2 data rows
gflow video batch manifest.preview.tsv --out-dir ./preview/
```

If the preview clips look right, run the full manifest. If they're wrong, fix the prompts before spending credits at scale.

A `--dry-run` flag that validates the manifest + estimates cost without making any paid calls is planned (not yet scheduled).

---

## Journey 11 — Wiring gflow outputs into a downstream pipeline

You want to feed `gflow-cli`'s output into `ffmpeg`, a CMS, or another automation step.

### 11.1 The deterministic output layout

Both default outputs and `--out` flags produce **predictable paths**:

```text
$GFLOW_CLI_OUTPUT_DIR/
├── videos/
│   └── <YYYY-MM-DD>/
│       └── <media_uuid>.mp4
└── images/
    └── <YYYY-MM-DD>/
        └── <media_uuid>_<n>.png        # _1 / _2 / _3 / _4 for -n>1
```

`<media_uuid>` is the asset UUID returned by Flow — globally unique. Same UUID across the operation poll response and the on-disk filename.

When you pass `-o ./custom/path.mp4` or `--out-dir ./custom/`, `gflow-cli` writes there instead.

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

### 11.5 Trim the manifest to unrendered rows only (skip-existing workaround)

`gflow video batch` does not skip-existing yet. To rerun only the rows whose `output_path` does not exist:

```bash
awk -F'\t' 'NR==1 || /^#/ || /^$/ { print; next } { if (system("test -e " $NF) != 0) print }' \
    manifest.tsv > manifest.remaining.tsv
gflow video batch manifest.remaining.tsv
```

(`$NF` is the last column = `output_path`.)

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

Once it passes, edit the manifest and rerun the batch.

### 12.2 Exit 4 — `RateLimitError`

Flow returned `429 Too Many Requests`. The `tenacity` retry layer already retried up to 3× with exponential jittered backoff (1 s → 2 s → 4 s, ±25% jitter, capped at 60 s when `Retry-After` is set). If it still failed, you're hitting either:

- **Sustained rate limit** — the API thinks you're spamming. Wait and resume.
- **Daily quota** — your Ultra/Pro allowance for the day is exhausted. Check the credit balance UI.

**Recovery steps, in order:**

```bash
# 1. Drop the concurrency to 1 (sequential) and try again — sometimes the limit
#    is per-N-in-flight rather than per-N-per-minute.
GFLOW_CLI_CONCURRENCY=1 gflow video batch manifest.remaining.tsv

# 2. If still 429: wait. 60 seconds for transient, up to a few hours for daily.
sleep 300

# 3. If a 60-second wait clears it, gradually ramp concurrency back up:
GFLOW_CLI_CONCURRENCY=2 gflow video batch manifest.remaining.tsv

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

## See also

- [USAGE.md](USAGE.md) — every flag, every output path, every command in alphabetical order.
- [CONFIGURATION.md](CONFIGURATION.md) — every env var with default, precedence chain, and security notes.
- [AUTHENTICATION.md](AUTHENTICATION.md) — full auth flow, session storage, multi-account details, refresh strategy.
- [ARCHITECTURE.md](ARCHITECTURE.md) — modular monolith layout, per-worker Page pool, RFC 9457 Problem Details, retry layer.
- [SECURITY.md](SECURITY.md) — threat model, redaction strategy, what's on disk.
- [KNOWN_ISSUES.md](../KNOWN_ISSUES.md) — workarounds for current limitations.

_Built for Google AI Ultra / Pro subscribers who'd rather automate than click. Same Veo model, same quality, same billing — without the browser._
