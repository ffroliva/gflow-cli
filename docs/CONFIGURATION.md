# Configuration

`gflow-cli` is configured via three layers, with a strict precedence order:

```text
CLI flag (highest)  >  environment variable  >  .env file  >  built-in default (lowest)
```

Every setting validates at startup via [`pydantic-settings`](https://docs.pydantic.dev/latest/concepts/pydantic_settings/). Bad values fail loudly with the offending key + the rule it violated, never silently.

## Reference

Each variable in `.env.template` documented here:

### `GFLOW_CLI_HOME`

**What:** Root directory for Playwright persistent contexts (signed-in Google sessions).
**Default:** Per-OS user-data-dir via [`platformdirs`](https://github.com/platformdirs/platformdirs):
- Windows: `%LOCALAPPDATA%\gflow-cli` (e.g. `C:\Users\<you>\AppData\Local\gflow-cli`)
- macOS: `~/Library/Application Support/gflow-cli`
- Linux (XDG): `$XDG_DATA_HOME/gflow-cli` (typically `~/.local/share/gflow-cli`)

**Override examples:**
```bash
export GFLOW_CLI_HOME=/secure-volume/gflow-cli                       # POSIX
$env:GFLOW_CLI_HOME = "D:\gflow-cli"                                # PowerShell
```

See [AUTHENTICATION § Session storage](AUTHENTICATION.md#session-storage) for the full layout.

### `GFLOW_CLI_PROFILE`

**What:** Default profile name used when `--profile` isn't passed on the CLI.
**Default:** Resolved from `$GFLOW_CLI_HOME/config.toml` → auto-pick if exactly one profile → otherwise prompts the user to choose.
**CLI override:** `--profile <name>`

A profile maps to a directory `$GFLOW_CLI_HOME/profile_<name>/`. Profiles are isolated — different Google accounts, different cookies, different Flow project histories. See [AUTHENTICATION § Multiple accounts](AUTHENTICATION.md#multiple-accounts).

#### Default-profile resolution chain

1. CLI flag `--profile <name>` (highest)
2. Env var `GFLOW_CLI_PROFILE`
3. `$GFLOW_CLI_HOME/config.toml` → `default_profile = "..."` (set by `gflow auth use <name>`)
4. Auto: if exactly one `profile_*` dir exists, it's the de-facto default
5. Fail with the list of available profiles (lowest)

### `GFLOW_CLI_OUTPUT_DIR`

**What:** Root directory where downloaded assets land. Subfolders are created per kind/date.
**Default:** Per-OS Downloads dir + `/gflow-cli`:
- Windows: `%USERPROFILE%\Downloads\gflow-cli`
- macOS: `~/Downloads/gflow-cli`
- Linux (XDG): `$XDG_DOWNLOAD_DIR/gflow-cli` (falls back to `~/Downloads/gflow-cli`)

**CLI override:** command-specific output flags such as `--out` for image
commands and `--out-dir` for video commands.

If [`GFLOW_CLI_STORAGE_URI`](#gflow_cli_storage_uri) is set, generated asset
bytes are uploaded to the configured cloud bucket instead of this local output
root. `GFLOW_CLI_OUTPUT_DIR` still matters for local mode and for deriving the
default image/video key layout. See [EXTERNAL_STORAGE.md](EXTERNAL_STORAGE.md).

### `GFLOW_CLI_STORAGE_URI`

**What:** Optional cloud storage URI prefix for generated assets.
**Values:** `gs://bucket/prefix/` for Google Cloud Storage or
`s3://bucket/prefix/` for S3-compatible storage, including MinIO.
**Default:** unset, which means local filesystem output.
**Requires:** install the matching optional extra:

```bash
uv tool install "gflow-cli[gcs]"
uv tool install "gflow-cli[s3]"
```

When set, `gflow-cli` uploads generated assets to the cloud prefix instead of
saving local asset copies. It does not dual-write local + cloud copies. The
local SQLite catalog still records the operation and stores `storage_provider`
plus `cloud_uri` for each uploaded asset.

Examples:

```bash
# Choose one:
export GFLOW_CLI_STORAGE_URI=gs://my-gcs-bucket/gflow/
export GFLOW_CLI_STORAGE_URI=s3://my-s3-bucket/gflow/
export GFLOW_CLI_STORAGE_URI=s3://gflow-test/dev/   # MinIO local dev
```

S3 and MinIO use the standard AWS SDK environment variables:

```bash
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin
export AWS_ENDPOINT_URL=http://localhost:9000   # omit for real AWS
export AWS_DEFAULT_REGION=us-east-1
```

GCS uses Application Default Credentials, a service-account file through
`GOOGLE_APPLICATION_CREDENTIALS`, or `STORAGE_EMULATOR_HOST` for local emulator
runs.

Deep setup, verification, and security notes live in
[EXTERNAL_STORAGE.md](EXTERNAL_STORAGE.md).

### `GFLOW_CLI_PROVIDER`

**What:** Backend to use for generations.
**Values:** `flow` (default — reverse-engineered, requires Ultra/Pro) | `official` (planned v0.5+ — Phase 5 official Veo SDK swap, will require a Gemini API key).
**Default:** `flow`
**CLI override:** none yet — set the env var to switch backends once `official` is wired.

### `GFLOW_CLI_GEMINI_API_KEY`

**What:** API key for the official Veo 3.1 SDK.
**Required when:** `GFLOW_CLI_PROVIDER=official`.
**Default:** unset
**Get one:** <https://aistudio.google.com/apikey>

### `GFLOW_CLI_AUTH_LOGIN_TIMEOUT`

**What:** Maximum time (seconds) that `gflow auth login` waits for the user to complete the Google sign-in flow in the browser.
**Default:** `600` (10 minutes)
**Range:** 1–86400
**Exit code on expiry:** 12 (`AuthLoginTimeoutError`)
**Note:** Useful for CI/CD or agent pipelines where a hung login should surface as a definite failure rather than blocking indefinitely. Set to a large value (e.g. `3600`) for interactive sessions over slow connections.

```bash
GFLOW_CLI_AUTH_LOGIN_TIMEOUT=120 gflow auth login   # abort after 2 minutes
```

### `GFLOW_CLI_TIMEOUT_SECONDS`

**What:** Per-request HTTP timeout. Veo videos can take 60–180 s each.
**Default:** `600`
**Note:** This is a single-request ceiling; the *batch* timeout you experience is sum of all per-clip waits.

### `GFLOW_CLI_LOG_LEVEL`

**What:** Logging verbosity.
**Values:** `DEBUG` | `INFO` | `WARNING` | `ERROR`
**Default:** `INFO`
**CLI override:** `-v` / `--verbose` flag flips to `DEBUG`.

### `GFLOW_CLI_LOG_FORMAT`

**What:** Output format for log lines.
**Values:**
- `auto` (default) — text on TTY, JSON when stdout is piped/redirected
- `text` — always pretty (Rich-styled, colours)
- `json` — always machine-readable (one JSON object per line)

### `GFLOW_CLI_CONCURRENCY`

**What:** Per-worker Playwright Page-pool size for batch runs. `FlowApiClient.__aenter__` opens N Pages inside one shared persistent BrowserContext; operations check out a Page via an `asyncio.Queue` (FIFO, bounded by `maxsize=N`). `gflow video batch` fans out manifest entries via `asyncio.gather`.
**Values:** `1`–`16`
**Default:** `1` (no fan-out)
**Recommended starting point:** `4`. Each Page costs ~30–60 MiB of memory on Chromium headless; don't exceed `8` without measuring resident-set size. Cookies and storage state are shared at Context level, so every Page inherits the signed-in profile for free.
**Shipped in:** v0.4.0a2.

### `GFLOW_CLI_DB_PATH`

**What:** Override the path to the local SQLite operations database.
**Default:** `<GFLOW_CLI_HOME>/gflow.db`
**Override examples:**
```bash
export GFLOW_CLI_DB_PATH=/secure-volume/gflow.db       # POSIX
$env:GFLOW_CLI_DB_PATH = "D:\gflow-data\gflow.db"     # PowerShell
```

Use this when you want the DB on a different volume, outside `GFLOW_CLI_HOME`, or when running multiple isolated environments that share the same home dir.

### `GFLOW_CLI_HISTORY_PROMPTS`

**What:** Controls how prompt text is persisted in the local database.
**Values:**
- `store` (default) — the full prompt text is saved to the database alongside the operation record.
- `redacted` — only the SHA-256 hash of the prompt is stored; the prompt text itself is never written to disk. Use this when prompts may contain sensitive content.

**Default:** `store`

```bash
GFLOW_CLI_HISTORY_PROMPTS=redacted gflow image t2i "confidential brief"
```

### `GFLOW_CLI_HEADLESS`

**What:** Run Playwright in headless mode for non-`auth login` commands.
**Values:** `true` | `false`
**Default:** `true`
**When to flip to `false`:** if reCAPTCHA Enterprise refuses to mint tokens (Google's bot-detection sometimes refuses headless Chromium but accepts a visible window). Set to `false` and re-run; the browser will appear during generation but the session is still reused from the persistent profile.

### `GFLOW_CLI_LOCALE`

**What:** BCP-47 locale tag passed to Playwright's `launch_persistent_context(locale=...)` — controls the `Accept-Language` HTTP header only.
**Values:** any BCP-47 tag (e.g. `en-US`, `pt-BR`, `es-ES`, `ja-JP`)
**Default:** `en-US`
**Shipped in:** post-v0.8.1 develop (PR #51).
**When to set it:** capturing locale-invariant DOM via `scripts/dev/capture_locale_invariants.py`, or live-verifying a generation under a non-EN account language.
**Important:** Chrome's *UI* language is independently forced to `en-US` via the `--lang=en-US` launch arg (so Flow keeps serving `/fx/tools/flow/` and the editor's localized text selectors keep working). This env var only affects request headers — not the editor UI you see. See [KNOWN_ISSUES § issue #24](../KNOWN_ISSUES.md) for the path to dropping `--lang=en-US`.

## Output paths

The default output scheme keeps generated assets sortable, dated, and grouped by job:

```text
$GFLOW_CLI_OUTPUT_DIR/
├── images/<YYYY-MM-DD>/<media_name>_<index>.png
└── videos/<YYYY-MM-DD>/<media_name>.mp4
```

`<media_name>` is the per-asset UUID Flow assigns; `<index>` is the 1-based shot number for multi-image runs (`-n 2..4`).

With `GFLOW_CLI_STORAGE_URI` set, the same default layout is used under the
configured bucket prefix, and `gflow data media <media_id>` reports `cloud_uri_N`
rows instead of local paths.

### Per-call override

```bash
# Image: --out is a directory; files written flat (no date subdir)
gflow image t2i "..." --out ./shots/

# Video: --out-dir is a directory for the generated mp4
gflow video t2v "..." --out-dir ./out/

# Video batch: --out-dir overrides the videos/<date>/ root
gflow video batch ./manifest.tsv --out-dir ./batch-out/

# Video chain: --out-dir holds the per-link mp4s + seed frames
gflow video chain ./story.jsonl --out-dir ./chain-out/ --yes
```

For images, `--out DIR` writes flat as `<DIR>/<media_name>_<n>.png` — file paths are not accepted (rename after the fact if needed). For videos, `--out-dir DIR` controls the local output directory.

## `gflow video chain` flags

`gflow video chain` (last-frame I2V chaining — see
[USAGE § gflow video chain](USAGE.md#gflow-video-chain)) is configured entirely
by command-line flags; it adds **no new environment variables**. It reuses
`GFLOW_CLI_OUTPUT_DIR` (default output root), `GFLOW_CLI_PROFILE`,
`GFLOW_CLI_DB_PATH` (chain links are recorded for `--resume-from`), and
`GFLOW_CLI_TIMEOUT_SECONDS` (per-link generation ceiling — the chain waits for
each link in turn, so total wallclock is the sum of all link waits).

| Flag | Default | Notes |
|---|---|---|
| `--model` | `veo-lite` | `veo-lite` / `veo-fast` / `veo-quality` / `veo-lite-lp`. `omni-flash` is rejected — it can't seed i2v links (issue #125). |
| `--max-links N` | unset | Cap link count; exit 11 (`ConfigurationError`) if the manifest has more. A spend guardrail. |
| `-y` / `--yes` | off | Skip the per-credit cost confirmation prompt. |
| `--dry-run` | off | Print the plan + credit estimate and spend nothing. |
| `--resume-from CHAIN_ID` | unset | Resume a prior chain by its id; already-paid links are skipped (not re-billed). |
| `--jitter F` | `0.0` | Random `0..F` second pause **between** links (anti-bot cadence; never before link 0). |
| `--seed-offset MS` | `0` | Extract the seed frame this many ms before EOF (fade-to-black guard). |
| `--aspect` | `9:16` | `9:16` / `16:9`. Applied uniformly to every link (continuity requirement). |
| `--out-dir DIR` | output root | Directory for the link mp4s + `linkN_lastframe.jpg` seed frames. |
| `--profile NAME` | default profile | Per-subcommand profile override. |
| `--json` | off | Emit a machine-readable JSON result. |

The last-frame extractor needs the **`chain` optional extra** (PyAV — no system
ffmpeg required):

```bash
pip install 'gflow-cli[chain]'
# or:  uv tool install 'gflow-cli[chain]'
```

Per-call local output flags are not intended as bucket-prefix controls. For
predictable external storage keys, set the bucket prefix in
`GFLOW_CLI_STORAGE_URI` and leave per-command output flags unset.

## .env loading

`gflow-cli` (via [`pydantic-settings`](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)) loads a single `.env` from the **current working directory** at startup. There is no second load from `$GFLOW_CLI_HOME` — keep your `.env` next to where you invoke `gflow`.

Variables already set in the actual environment always beat any `.env` file. Anything explicitly passed on the CLI beats everything else.

Use [`.env.template`](../.env.template) as your starting point:

```bash
cp .env.template .env
$EDITOR .env
```

## Worked examples

### "I want all output on a different drive"

```bash
# .env in CWD or $GFLOW_CLI_HOME
GFLOW_CLI_OUTPUT_DIR=/mnt/big-disk/flow-output
```

### "I want generated assets in S3 or GCS"

```bash
GFLOW_CLI_STORAGE_URI=s3://my-bucket/gflow/ \
AWS_ACCESS_KEY_ID=... \
AWS_SECRET_ACCESS_KEY=... \
AWS_DEFAULT_REGION=us-east-1 \
gflow image t2i "product photo on a white sweep"
```

See [EXTERNAL_STORAGE.md](EXTERNAL_STORAGE.md) for MinIO and GCS examples.

### "I'm running in CI — I want JSON logs and a strict timeout"

```bash
GFLOW_CLI_LOG_FORMAT=json \
GFLOW_CLI_TIMEOUT_SECONDS=300 \
gflow video batch ./manifest.tsv
```

### "I want to test against the official Veo SDK"

```bash
GFLOW_CLI_PROVIDER=official \
GFLOW_CLI_GEMINI_API_KEY=AIza... \
gflow video t2v "test"
```

(planned v0.5+ — current scaffold accepts but ignores `GFLOW_CLI_PROVIDER=official`.)

### "I want a sandbox profile that doesn't pollute my main one"

```bash
gflow auth login --profile experiments
gflow image t2i "test idea" --profile experiments
# sandbox dir lives at $GFLOW_CLI_HOME/profile_experiments/
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ValidationError: GFLOW_CLI_TIMEOUT_SECONDS must be a positive integer` | Bad `.env` value | Set to a number ≥ 1 |
| `FileNotFoundError: $GFLOW_CLI_HOME/profile_default not found` | First run, no auth yet | `gflow auth login` |
| `AuthExpiredError` | Cookies expired or revoked | `gflow auth login --profile <name>` |
| Output files don't appear where I expect | Flag > env > .env > default — check actual resolved path | `gflow image t2i ... --verbose` shows the resolved output path |
| Two concurrent calls fail with "Chromium profile locked" | Same profile used twice | Use `--profile other` for the second call |
