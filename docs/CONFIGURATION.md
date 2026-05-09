# Configuration

`gflow-cli` is configured via three layers, with a strict precedence order:

```text
CLI flag (highest)  >  environment variable  >  .env file  >  built-in default (lowest)
```

Every setting validates at startup via [`pydantic-settings`](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) (planned for Phase 1). Bad values fail loudly with the offending key + the rule it violated, never silently.

## Reference

Each variable in `.env.template` documented here:

### `FLOW_CLI_HOME`

**What:** Root directory for Playwright persistent contexts (signed-in Google sessions).
**Default:** Per-OS user-data-dir via [`platformdirs`](https://github.com/platformdirs/platformdirs):
- Windows: `%LOCALAPPDATA%\gflow-cli` (e.g. `C:\Users\<you>\AppData\Local\gflow-cli`)
- macOS: `~/Library/Application Support/gflow-cli`
- Linux (XDG): `$XDG_DATA_HOME/gflow-cli` (typically `~/.local/share/gflow-cli`)

**Override examples:**
```bash
export FLOW_CLI_HOME=/secure-volume/gflow-cli                       # POSIX
$env:FLOW_CLI_HOME = "D:\gflow-cli"                                # PowerShell
```

See [AUTHENTICATION § Session storage](AUTHENTICATION.md#session-storage) for the full layout.

### `FLOW_CLI_PROFILE`

**What:** Default profile name used when `--profile` isn't passed on the CLI.
**Default:** Resolved from `$FLOW_CLI_HOME/config.toml` → auto-pick if exactly one profile → otherwise prompts the user to choose.
**CLI override:** `--profile <name>`

A profile maps to a directory `$FLOW_CLI_HOME/profile_<name>/`. Profiles are isolated — different Google accounts, different cookies, different Flow project histories. See [AUTHENTICATION § Multiple accounts](AUTHENTICATION.md#multiple-accounts).

#### Default-profile resolution chain

1. CLI flag `--profile <name>` (highest)
2. Env var `FLOW_CLI_PROFILE`
3. `$FLOW_CLI_HOME/config.toml` → `default_profile = "..."` (set by `gflow auth use <name>`)
4. Auto: if exactly one `profile_*` dir exists, it's the de-facto default
5. Fail with the list of available profiles (lowest)

### `FLOW_CLI_OUTPUT_DIR`

**What:** Root directory where downloaded assets land. Subfolders are created per kind/date.
**Default:** Per-OS Downloads dir + `/gflow-cli`:
- Windows: `%USERPROFILE%\Downloads\gflow-cli`
- macOS: `~/Downloads/gflow-cli`
- Linux (XDG): `$XDG_DOWNLOAD_DIR/gflow-cli` (falls back to `~/Downloads/gflow-cli`)

**CLI override:** `--output <path>` per-call.

### `FLOW_CLI_PROVIDER`

**What:** Backend to use for generations.
**Values:** `flow` (default — reverse-engineered, requires Ultra/Pro) | `official` (planned v0.3+, requires Gemini API key).
**Default:** `flow`
**CLI override:** `--provider <name>` per-call.

### `FLOW_CLI_GEMINI_API_KEY`

**What:** API key for the official Veo 3.1 SDK.
**Required when:** `FLOW_CLI_PROVIDER=official`.
**Default:** unset
**Get one:** <https://aistudio.google.com/apikey>

### `FLOW_CLI_TIMEOUT_SECONDS`

**What:** Per-request HTTP timeout. Veo videos can take 60–180 s each.
**Default:** `600`
**Note:** This is a single-request ceiling; the *batch* timeout you experience is sum of all per-clip waits.

### `FLOW_CLI_LOG_LEVEL`

**What:** Logging verbosity.
**Values:** `DEBUG` | `INFO` | `WARNING` | `ERROR`
**Default:** `INFO`
**CLI override:** `-v` / `--verbose` flag flips to `DEBUG`.

### `FLOW_CLI_LOG_FORMAT`

**What:** Output format for log lines.
**Values:**
- `auto` (default) — text on TTY, JSON when stdout is piped/redirected
- `text` — always pretty (Rich-styled, colours)
- `json` — always machine-readable (one JSON object per line)

### `FLOW_CLI_CONCURRENCY`

**What:** Max concurrent operations during batch runs.
**Default:** `1`
**Note:** Planned full effect in v0.4 once the per-account pool ships. Current scaffold ignores this and runs sequentially.

## Output paths

The default output scheme keeps generated assets sortable, dated, and grouped by job:

```text
$FLOW_CLI_OUTPUT_DIR/
├── images/<YYYY-MM-DD>/<job_id>_<index>.png
├── videos/<YYYY-MM-DD>/<job_id>.mp4
└── manifests/<YYYY-MM-DD>/<batch_id>.tsv      # snapshot of the batch input
```

`<job_id>` is the Flow workflow ID (UUID4); `<index>` is 1-based for multi-image jobs.

### Per-call override

```bash
# File path (single image)
gflow image generate -p "..." --output ./out/specific-name.png

# Directory path (batch — files placed inside)
gflow image batch ./manifest.tsv --out-dir ./batch-out/
```

A file path always wins over a directory; passing `./out/foo.png` to a multi-image generation gets templated as `./out/foo_1.png`, `./out/foo_2.png`, etc.

## .env loading

`gflow-cli` looks for `.env` files in this order:

1. **Current working directory** (`$CWD/.env`) — highest precedence among `.env` sources.
2. **Profile root** (`$FLOW_CLI_HOME/.env`) — handy for global per-machine defaults.

Variables already set in the actual environment always beat any `.env` file. Anything explicitly passed on the CLI beats everything else.

Use [`.env.template`](../.env.template) as your starting point:

```bash
cp .env.template .env
$EDITOR .env
```

## Worked examples

### "I want all output on a different drive"

```bash
# .env in CWD or $FLOW_CLI_HOME
FLOW_CLI_OUTPUT_DIR=/mnt/big-disk/flow-output
```

### "I'm running in CI — I want JSON logs and a strict timeout"

```bash
FLOW_CLI_LOG_FORMAT=json \
FLOW_CLI_TIMEOUT_SECONDS=300 \
gflow image batch ./manifest.tsv
```

### "I want to test against the official Veo SDK"

```bash
FLOW_CLI_PROVIDER=official \
FLOW_CLI_GEMINI_API_KEY=AIza... \
gflow image generate -p "test"
```

(planned v0.3 — current scaffold accepts but ignores `--provider official`.)

### "I want a sandbox profile that doesn't pollute my main one"

```bash
gflow auth login --profile experiments
gflow image generate -p "test idea" --profile experiments
# sandbox dir lives at $FLOW_CLI_HOME/profile_experiments/
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ValidationError: FLOW_CLI_TIMEOUT_SECONDS must be a positive integer` | Bad `.env` value | Set to a number ≥ 1 |
| `FileNotFoundError: $FLOW_CLI_HOME/profile_default not found` | First run, no auth yet | `gflow auth login` |
| `AuthExpiredError` | Cookies expired or revoked | `gflow auth login --profile <name>` |
| Output files don't appear where I expect | Flag > env > .env > default — check actual resolved path | `gflow image generate ... --verbose` shows the resolved output path |
| Two concurrent calls fail with "Chromium profile locked" | Same profile used twice | Use `--profile other` for the second call |
