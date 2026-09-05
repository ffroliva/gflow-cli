# Installation

## Requirements

- A Google account with Flow access — every generation bills your own account (a paid AI Pro/Ultra plan only affects credit allowances).
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
gflow update            # runs the installer that put gflow-cli here: uv tool / pipx / pip
gflow update --check    # only report installed vs latest
```

Every command shows a banner when a newer release is on PyPI (`GFLOW_CLI_UPDATE_CHECK=0`
silences it). Installed from a checkout? `gflow update` refuses (exit 11) — `git pull` and
reinstall instead. Full behaviour: [USAGE § `gflow update`](USAGE.md#gflow-update).

Next: [**Authentication →**](AUTHENTICATION.md).
