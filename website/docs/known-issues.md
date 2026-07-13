# Known issues

gflow-cli is alpha and reverse-engineered. These are the current sharp edges. For the authoritative, up-to-date list, see [`KNOWN_ISSUES.md`](https://github.com/ffroliva/gflow-cli/blob/develop/KNOWN_ISSUES.md) and the [issue tracker](https://github.com/ffroliva/gflow-cli/issues) in the repo.

## Headed-browser dependency

gflow-cli drives Flow via a real Chrome session managed by Playwright (`ui_automation` transport). Google's auth + reCAPTCHA stack rejects bundled Chromium and most headless approaches. This means:

- It needs a saved Chrome profile and a display for the one-time login.
- It can't run on serverless / headless CI workers without transplanting a prerecorded profile.
- Per-account concurrency is capped by what one warm browser can drive.

If you can help unblock a pure HTTP transport (especially for video generation, where HTTP 401 + reCAPTCHA mints currently block it), please [open an issue](https://github.com/ffroliva/gflow-cli/issues).

## WAF / 403 pacing

Flow's WAF reacts to cumulative submission cadence. Bursty, unpaced runs trip a `403` ("blocked by WAF or fingerprint check").

- Pace batch runs with `--jitter MIN-MAX` or `GFLOW_CLI_JITTER_RANGE`. Defaults are deliberately small; widen only when you actually hit 403s, then dial back.
- After a 403, give the account a cooldown (30–60 min) and probe with a single small generation before batching again.

## Credit safety

- Use `--ui-mode` where available — it fails fast *before* spending credits when Flow's UI cohort isn't what the command needs.
- Generation always bills your own Google account. There is no dry-run that produces media for free.

## Reporting

Found something? An [issue](https://github.com/ffroliva/gflow-cli/issues) with the command, the traceback, and your OS helps a lot. If you drive gflow-cli through an agent, include what you asked it to do.
