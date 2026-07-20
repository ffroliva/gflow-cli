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
`$GFLOW_CLI_HOME` also holds `config.toml`, the SQLite catalog (`gflow.db`), and — if you
create it — a `tools/` directory of user-authored "My Tools" TOMLs
(`<GFLOW_CLI_HOME>/tools/*.toml`, auto-loaded; see [TOOLS.md § My Tools](TOOLS.md)).

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

**What:** Public Gemini API key. Used by the **`creative-director`** prompt tool
(`--tool creative-director` / `gflow tools run creative-director`, see
[TOOLS.md](TOOLS.md) and [PROMPT_EXPANSION.md](PROMPT_EXPANSION.md)) and, in future, by the
official Veo 3.1 SDK.
**Required when:** you apply the `creative-director` tool (via `--tool` on any generation
command, or `gflow tools run`), or `GFLOW_CLI_PROVIDER=official`.
**Default:** unset
**Behavior when unset:** the tool is a no-op — gflow logs an `INFO` notice and generates
from your original prompt (it never fails the run). API errors (rate limit, network) fall
back the same way after a short exponential-backoff retry, bounded by an overall ~60s
wall-clock budget per call.
**Get one:** <https://aistudio.google.com/apikey>

### `GFLOW_CLI_GEMINI_MODEL`

**What:** Gemini model used by the `creative-director` prompt tool.
**Default:** `gemini-2.5-flash`
**Note:** Override to select a newer Flash revision without upgrading gflow-cli, e.g.
`GFLOW_CLI_GEMINI_MODEL=gemini-2.5-flash-lite`.

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

### `GFLOW_CLI_JITTER_RANGE`

**What:** Anti-bot pause between prompt submissions in multi-prompt image runs (`gflow image batch`, `gflow image t2i --prompts-file` / `--stdin` / multiple positional prompts, and `gflow run` image batches). Spaces out the *submission clicks* only — generations still run in parallel inside Flow.
**Values:** `MIN-MAX` seconds (e.g. `10-30`), a single number `N` (uniform `0`–`N`, mirrors `video chain --jitter`), or `0` to disable.
**Default:** `0.5-1.5` — deliberately small so runs don't waste wall-clock. **Widen (e.g. `10-30`) when runs start hitting WAF 403s**, then dial back once the score decays.
**CLI override:** `--jitter` on `image t2i` and `image batch` (flag beats env).
**Why:** Flow's WAF reacts to cumulative submission cadence — see [DEBUGGING § WAF cadence](DEBUGGING.md#waf-cadence).

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

**What:** Per-worker Playwright Page-pool size. `FlowApiClient.__aenter__` opens N Pages inside one shared persistent BrowserContext; operations check out a Page via an `asyncio.Queue` (FIFO, bounded by `maxsize=N`). No current CLI command fans out multiple generations concurrently through this pool — `gflow image batch` processes prompts sequentially, and the manifest-driven video runner that used to fan out via `asyncio.gather` was removed as nonfunctional — so raising this above `1` has no effect until a concurrent caller exists.
**Values:** `1`–`16`
**Default:** `1` (no fan-out)
**Recommended starting point:** `1` until a concurrent caller lands. Each additional Page would cost ~30–60 MiB of memory on Chromium headless; don't exceed `8` without measuring resident-set size. Cookies and storage state are shared at Context level, so every Page would inherit the signed-in profile for free.
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

**Tool provenance:** when a prompt tool (e.g. `creative-director`) rewrites a prompt, `store`
mode also records the submitted `expanded_prompt` and a full `metadata_json.tool` descriptor;
`redacted` withholds the expanded prompt and reduces the descriptor to
`{name, version, params_hash, config_hash}`. See [PROMPT_EXPANSION.md](PROMPT_EXPANSION.md).

```bash
GFLOW_CLI_HISTORY_PROMPTS=redacted gflow image t2i "confidential brief"
```

### `GFLOW_CLI_HEADLESS`

**What:** Run Playwright in headless mode for non-`auth login` commands.
**Values:** `true` | `false`
**Default:** `false` — **headed real Chrome is the production default**, not an opt-in fallback. The `ui_automation` transport (gflow-cli's only production transport) requires a headed browser: reCAPTCHA Enterprise rejects headless Chromium with an immediate 403, so `headless=true` is not a WAF workaround — it only exists for CI/CD environments running a non-`ui_automation` transport (e.g. `bearer`/`sapisidhash`, experimental).
**WAF-sensitive runs:** set `GFLOW_CLI_HEADLESS=false` explicitly (it is already the default, but pin it in CI/CD env files or scripts that also set `headless=true` for a different transport, so a transport switch back to `ui_automation` doesn't silently regress to a rejected headless launch).

### `GFLOW_CLI_BROWSER_ENGINE`

**What:** Selects the browser-automation engine backing the Playwright API.
**Values:** `playwright` | `patchright`
**Default:** `playwright`
**What `patchright` is:** an opt-in, drop-in patched Playwright (Chromium) that runs page evaluations in an isolated execution context to avoid the `Runtime.enable` CDP leak, for stronger reCAPTCHA-Enterprise evasion on the **headed** path. It is **not** a headless unlock — Google still detects headless Chromium regardless of engine.
**Install:** `pip install 'gflow-cli[patchright]'` (or `pip install patchright`). Selecting `patchright` without it installed fails fast with exit code 24 and a pip remediation hint. When using system Chrome (the gflow default, `channel=chrome`) you do **not** need `patchright install chromium`.
**Reverting:** unset the variable (or set `playwright`) — the default path is byte-identical to a build without this feature, with no profile migration.
**Security note:** the `patchright` extra ships a *patched Chromium driver* that handles your live Google session cookies; it is exact-pinned and treated as a security-review-required dependency. See [SECURITY.md § Dependencies](SECURITY.md).

### `GFLOW_CLI_UI_MODE`

**What:** Which Flow UI arm to use for generation. Flow serves a **classic** composer (hard crop/aspect controls) or an **agentic** chat cohort, server-assigned and flapping per page load ([#299](https://github.com/ffroliva/gflow-cli/issues/299)).
**Values:**
- `auto` (default) — bind whatever the composer renders.
- `classic` — require the classic composer (hard aspect controls). gflow switches to it, re-probes to verify, and if the arm is still agentic **aborts before submitting** with `UiModeUnavailableError` (**exit 28**) — no credits spent.
- `agentic` — require the agentic chat surface (needed for `-i` agent instructions). gflow switches to it, verifies, and aborts (exit 28) if it can't be reached.
**Default:** `auto`
**How it works:** before each generation, gflow determines the required arm, clicks the classic↔agentic toggle as a **prerequisite**, **verifies** with a DOM re-probe, then binds — or fails fast. The required arm is also **inferred**: `-i` instructions are agentic-only, so they force `agentic` automatically (and `--ui-mode classic` + `-i` is a hard conflict, not a silent drop).
**Why `classic`:** the agentic cohort treats aspect ratio as a soft prompt hint (portrait 9:16 can come back landscape). `classic` enforces it or fails fast instead of silently degrading.
**Retry note:** the cohort flaps per load, so an exit-28 abort is **retryable** — a re-run often lands the wanted arm. A server experiment can pin the arm, in which case it's unreachable from the client (the abort still saves the credits).
**Supersedes:** `GFLOW_CLI_PREFER_CLASSIC` and `GFLOW_CLI_FORCE_AGENT_UI` (below).

### `GFLOW_CLI_PREFER_CLASSIC` *(deprecated — use `GFLOW_CLI_UI_MODE=classic`)*

**Deprecated** in favor of [`GFLOW_CLI_UI_MODE`](#gflow_cli_ui_mode). `true` now maps to `ui_mode=classic` (emits a `DeprecationWarning`). **Behavior change:** the old silent fallback to agentic when the toggle was unavailable is gone — a classic-required run now **aborts with exit 28** instead of producing an agentic-cohort result. `GFLOW_CLI_UI_MODE` (and `--ui-mode`) take precedence when both are set.

### `GFLOW_CLI_FORCE_AGENT_UI` *(deprecated — use `GFLOW_CLI_UI_MODE=agentic`)*

**Deprecated** in favor of [`GFLOW_CLI_UI_MODE`](#gflow_cli_ui_mode). `true` maps to `ui_mode=agentic` (emits a `DeprecationWarning`). `-i` instructions already force agentic automatically, so this is rarely needed explicitly.

### `GFLOW_CLI_LOCALE`

**What:** BCP-47 locale tag passed to Playwright's `launch_persistent_context(locale=...)` — controls the `Accept-Language` HTTP header only.
**Values:** any BCP-47 tag (e.g. `en-US`, `pt-BR`, `es-ES`, `ja-JP`)
**Default:** `en-US`
**Shipped in:** post-v0.8.1 develop (PR #51).
**When to set it:** capturing locale-invariant DOM via `scripts/dev/capture_locale_invariants.py`, or live-verifying a generation under a non-EN account language.
**Important:** Chrome's *UI* language is independently forced to `en-US` via the `--lang=en-US` launch arg (so Flow keeps serving `/fx/tools/flow/` and the editor's localized text selectors keep working). This env var only affects request headers — not the editor UI you see. See [KNOWN_ISSUES § issue #24](../KNOWN_ISSUES.md) for the path to dropping `--lang=en-US`.

### `GFLOW_CLI_HAR_PATH`

**What:** Captures full Playwright network traffic (requests, responses, headers, cookies) to a HAR file for the session — useful for diagnosing wire-format surprises or WAF rejections.
**Default:** unset (no capture).
**Override examples:**
```bash
export GFLOW_CLI_HAR_PATH=/tmp/gflow-debug/session.har       # POSIX
$env:GFLOW_CLI_HAR_PATH = "C:\gflow-debug\session.har"      # PowerShell
```

**SECURITY:** a HAR file contains live auth cookies and bearer tokens — never share one publicly. The file is chmod'd `0o600` on POSIX after Playwright writes it (best-effort; no-op on Windows). Two concurrent `gflow` processes pointed at the same path will overwrite each other's HAR (last-writer-wins, no error) — use a distinct path per run if running more than one profile/command at once.

### `GFLOW_CLI_DEBUG_TRACEBACK`

**What:** Prints the real exception message + full traceback for unhandled (non-typed) errors — to the console, and under `--json`, into the payload's `error.detail` / `error.traceback` fields — instead of the default generic "Unexpected error" placeholder. This CLI's structured telemetry event is always SHA-256-hashed regardless of this setting; this flag only changes what you see, not what's logged.
**Values:** `true` | `false`
**Default:** `false`
**Override examples:**
```bash
GFLOW_CLI_DEBUG_TRACEBACK=1 gflow image t2i "a cat" --profile dev   # POSIX
$env:GFLOW_CLI_DEBUG_TRACEBACK = "1"                                # PowerShell
```

**SECURITY:** the real error text may contain tokens/cookies present in exception state — for local debugging only. `--json` output under this flag is a materially higher-risk surface than the interactive console: a human watches the console live and can react to the yellow warning, but `--json` output is designed to be piped into CI logs, log aggregators, and webhooks that persist or forward it unreviewed. **Never pipe `--json` output under this flag to a shared or persistent system without redacting it first.**

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

# Image batch: --out overrides the images/<date>/ root
gflow image batch ./manifest.tsv --out ./batch-out/

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

`gflow-cli` (via [`pydantic-settings`](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)) loads **two** `.env` files at startup: `$GFLOW_CLI_HOME/.env` (machine-wide defaults — useful for processes whose working directory is arbitrary, like the MCP server or a worker service) and a `.env` in the **current working directory** (project-local overrides). On conflicting keys the CWD file wins.

The home used for the first file is resolved from the `GFLOW_CLI_HOME` env var, else a `GFLOW_CLI_HOME` entry in the CWD `.env`, else the platform default — the same home `Settings.home` reports. (By construction the home `.env` cannot relocate home itself; set the env var or the CWD `.env` instead.)

Variables already set in the actual environment always beat both `.env` files. Anything explicitly passed on the CLI beats everything else.

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
gflow image batch ./manifest.tsv
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
| `ProfileLockedError` (exit code 11) | Two concurrent calls against the same profile — the cross-process `ProfileLease` fails fast (never waits) on same-profile contention, whether the second holder is another `gflow` process, the `gflow serve` daemon, or an MCP call | Wait for the first call to finish, or use `--profile other` — different profiles run fully in parallel, each with its own lease |
