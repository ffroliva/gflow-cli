# gflow-cli User Guide

> **Goal:** task-oriented walkthroughs for the most common workflows. Reference material (every flag, every env var, every error class) lives in [USAGE.md](USAGE.md), [CONFIGURATION.md](CONFIGURATION.md), [AUTHENTICATION.md](AUTHENTICATION.md), and [ARCHITECTURE.md](ARCHITECTURE.md). This guide tells you *what to do* in concrete situations.

**Audience:** Google AI Ultra / Pro subscribers who want to drive Flow (Veo + Imagen) from the terminal instead of the web UI.

**Prerequisites:**
- Python 3.11+
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

This is a ~250 MB download. It happens once per user.

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
2. It creates an ephemeral Flow project, mints a reCAPTCHA Enterprise token via the live page, POSTs to `aisandbox-pa.googleapis.com/v1/projects/{id}/flowMedia:batchGenerateVeoVideo`.
3. Polls the operation every ~5 seconds until terminal status.
4. Downloads the resulting `.mp4` to a date-partitioned path under `$GFLOW_CLI_OUTPUT_DIR/videos/<YYYY-MM-DD>/`.

**Typical wall-clock:** 60-180 seconds for an 8-second Veo clip. Faster on Imagen image jobs.

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
start_image	prompt	end_image	aspect	output_path
	a kite over a beach		16:9	clips/kite.mp4
./hero.png	a hot air balloon takes off				clips/balloon.mp4
	a candle flickering in a window		1:1	clips/candle.mp4
```

The first row is **not** a header — `parse_manifest` treats every non-blank, non-`#`-prefixed line as data. To leave a column blank, write nothing between the tabs.

Lines starting with `#` and blank lines are comments / spacing.

Save as e.g. `manifest.tsv`.

### 3.2 Run with concurrency

```bash
GFLOW_CLI_CONCURRENCY=4 gflow video batch manifest.tsv --out-dir ./out
```

`GFLOW_CLI_CONCURRENCY=4` opens 4 Playwright Pages inside the same persistent BrowserContext (they share cookies — same Google session) and fans out manifest entries via `asyncio.gather`. With 20 entries you'll see 4 in flight at any moment until the queue drains.

**Memory budget:** ~30-60 MiB per Page on Chromium. Don't raise `N` above 8 without measuring.

**Failure mode:** if any one entry fails (e.g. `AuthExpiredError` mid-batch), `asyncio.gather` cancels the siblings and the whole batch exits with the appropriate code (3, 4, 5, 6, or 7). Rerun the manifest after fixing the root cause — Flow doesn't charge for cancelled inflight calls.

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
# → media-uuid-abc-...

gflow image i2i "now at sunset" --ref media-uuid-abc-...
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

```bash
echo $?         # 3 — AuthExpiredError
gflow auth status
# cookies_present: True, but Google has invalidated the session
```

### 7.2 Refresh

```bash
gflow auth login --profile <name>
```

A Chromium window opens. Sign in again. Cookies overwrite.

### 7.3 Skip already-rendered entries

`asyncio.gather` cancels siblings on first failure, so SOME entries beyond 23 may have completed. Check `$GFLOW_CLI_OUTPUT_DIR/videos/<today>/` for what exists, then:

- Either: copy the surviving rows out of `manifest.tsv` and rerun the rest.
- Or: rerun the full manifest — Flow doesn't re-bill for previously-completed media, but `gflow video batch` doesn't skip-existing today (filed in [KNOWN_ISSUES](../KNOWN_ISSUES.md)).

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

The PyPI distribution name (`gflow-cli`) and CLI binary (`gflow`) are **unchanged**.

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
