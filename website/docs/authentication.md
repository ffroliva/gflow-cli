# Authentication

gflow-cli authenticates by saving a real browser session against **your own** Google Flow account. There are no API keys — the tool drives Flow the way you would, in a headed Chrome window.

## One-time login

```bash
gflow auth login --browser chrome
```

The `--browser chrome` flag is **mandatory**; the CLI fails fast on any other strategy. A Chrome window opens for you to sign in — including 2FA. Once you're in, the session is saved and reused on every subsequent run.

!!! warning "Your account, your risk"
    The saved session drives Flow as you. gflow-cli is unofficial and reverse-engineered — treat the whole thing as your own account risk. Not affiliated with Google.

## reCAPTCHA & the WAF

Google's auth path and Flow's WAF are the two things most likely to interrupt a run:

- **reCAPTCHA** can appear during login or token refresh. Complete it in the headed window.
- **The WAF (403)** reacts to cumulative request cadence. Bursty, unpaced generation trips it. Pace batch runs with `--jitter MIN-MAX` or `GFLOW_CLI_JITTER_RANGE`, and give a blocked account a cooldown before retrying. See [Known issues](known-issues.md) for the field notes.

## Sessions & profiles

- The saved session persists between runs — you don't log in every time.
- If a run starts failing with auth or 403 errors that a fresh login fixes, re-run `gflow auth login --browser chrome`.
- Live/E2E test flows opt in via their own environment variables (`GFLOW_LIVE=1`, `GFLOW_CLI_E2E_PROFILE`).

Next: [**Let your assistant drive it →**](agents.md).
