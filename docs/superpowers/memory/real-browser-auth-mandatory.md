---
name: real-browser-auth-mandatory
description: Real-browser (Chrome-strategy) auth is mandatory for gflow-cli UI automation — harden scripts/transports to fail fast on non-chrome profiles
---

Directive (2026-05-18): **real-browser authentication is mandatory** for UI-automation paths — "we harden on that until further notice."

> **Correction (2026-09-08): one sentence of this file was wrong. The directive is not.**
> This file used to say *"Never write a flow that expects interactive Google sign-in inside a
> Playwright-driven browser — it cannot work."* It can.
> [`2026-09-08-g12-blocks-webdriver-not-playwright`](../spikes/2026-09-08-g12-blocks-webdriver-not-playwright.md)
> drove three arms, each a human signing in by hand on a throwaway *unauthenticated* profile.
> Real Chrome with **no** stealth flags reported `navigator.webdriver === true` and was
> rejected at `/v3/signin/rejected` after 17.5 s. The same real Chrome **with**
> `--disable-blink-features=AutomationControlled` and
> `ignore_default_args=["--enable-automation"]` reported `false`, never saw the rejection, and
> reached a Flow session cookie at 59.4 s. Playwright's bundled Chromium, with the flags,
> passed too. **The discriminator is `navigator.webdriver` — not the Playwright connection,
> and not the binary.** `gflow auth login` now drives real Chrome through Playwright by
> default and closes the browser itself when the sign-in completes.
>
> **What the correction does NOT reach.** It retires the *sign-in-gate* claim only, and on
> **N=1** evidence: one account, one Windows host, one residential IP, one Chrome build, one
> day, every arm headed. It says nothing about headless in either direction, and nothing
> about generation — that is reCAPTCHA-Enterprise-gated, a different machine entirely
> ([[flow-google-com-batchexecute-headless-proven]]). Everything below still binds.

**Why:** a profile authenticated via `gflow auth login --profile <name> --browser chrome` gets a `.gflow_browser_strategy=chrome` marker; `channel_for_profile()` in `src/gflow_cli/browser_manager.py` returns `"chrome"` only when that marker is present, so Playwright drives the user's real installed Google Chrome instead of bundled Chromium. **A marker-less profile silently downgrades to bundled Chromium** — no error, just a different browser than the one the profile was built for. That downgrade, not the sign-in gate, is why `channel="chrome"` is load-bearing on every generation path. (Historically this file also named the G12 block — `/v3/signin/rejected`, "this browser may not be secure" — as the reason; see the correction above for what that block actually keys on.)

**How to apply:** Any UI-automation script/transport that drives the Flow UI must require a Chrome-strategy profile and **fail fast** with a clear error pointing to `gflow auth login --browser chrome` when `channel_for_profile()` returns `None`. The Phase 0 spike `scripts/smoke_video_editor.py` was hardened with exactly this guard in `main()` (commit a04b9b7). See [[video-generation-spec]].

**Interactive sign-in inside a Playwright-driven browser is now allowed — with flags.** If you write one, it must carry `--disable-blink-features=AutomationControlled`, `ignore_default_args=["--enable-automation"]`, `chromium_sandbox=True` (Playwright otherwise injects `--no-sandbox`, which is both an automation signal and Chrome's "unsupported command-line flag" banner) and `no_viewport=True` (an explicit `viewport=` emulates a size independent of the OS window and pushes Google's sign-in form off-screen on scaled displays). Both stealth flags were only ever measured together; keep both.
