# Installation

## Requirements

- A Google AI **Ultra or Pro** subscription with Flow access — every generation bills your own account.
- [`uv`](https://docs.astral.sh/uv/) to install the tool.
- A Chromium the browser transport can drive (installed via Playwright, below).
- A display for the one-time browser login (headless CI without a prerecorded profile won't work — see [Known issues](KNOWN_ISSUES.md)).

## Install the CLI

```bash
uv tool install gflow-cli
```

This puts `gflow` on your `PATH`. Verify:

```bash
gflow --version
```

## Install the browser engine

gflow-cli drives Flow through a real Chrome session managed by Playwright. Install Chromium once:

```bash
uv tool run --from gflow-cli playwright install chromium
```

!!! note "Why a browser at all?"
    Google's auth + reCAPTCHA stack rejects Playwright's bundled Chromium in most headless setups, so gflow-cli uses a headed browser transport (`ui_automation`). This is the project's defining trade-off — it works end-to-end against live Ultra/Pro accounts, but needs a saved profile and a display for the first login. See [Known issues](KNOWN_ISSUES.md).

## Configuration

- **Output directory** — CLI outputs default to `./out/`; override with `GFLOW_CLI_OUTPUT_DIR`. Scripts/tests write to `./tmp/`.
- **Batch pacing** — `GFLOW_CLI_JITTER_RANGE` (or `--jitter MIN-MAX`) tunes the delay between batch submissions.
- **Environment** — copy the project's `.env.template` to `.env.local` for the full list of variables; never commit it.

## Upgrade

```bash
uv tool upgrade gflow-cli
```

Next: [**Authentication →**](AUTHENTICATION.md).
