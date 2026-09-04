---
name: real-browser-auth-mandatory
description: Real-browser (Chrome-strategy) auth is mandatory for gflow-cli UI automation — harden scripts/transports to fail fast on non-chrome profiles
---

Directive (2026-05-18): **real-browser authentication is mandatory** for UI-automation paths — "we harden on that until further notice."

**Why:** Google's `accounts.google.com` sign-in/OAuth flow rejects automated and Playwright-bundled-Chromium browsers (the "G12 block" — `/v3/signin/rejected`, "this browser may not be secure"). Only the user's real installed Google Chrome is accepted. A profile authenticated via `gflow auth login --profile <name> --browser chrome` (`RealChromeStrategy`, passive capture — launches real Chrome, no automation) gets a `.gflow_browser_strategy=chrome` marker; `channel_for_profile()` in `src/gflow_cli/browser_manager.py` returns `"chrome"` only when that marker is present, so Playwright drives real Chrome instead of bundled Chromium. A marker-less profile silently launches bundled Chromium → blocked.

**How to apply:** Any UI-automation script/transport that drives the Flow UI must require a Chrome-strategy profile and **fail fast** with a clear error pointing to `gflow auth login --browser chrome` when `channel_for_profile()` returns `None`. Never write a flow that expects interactive Google sign-in inside a Playwright-driven browser — it cannot work. The Phase 0 spike `scripts/smoke_video_editor.py` was hardened with exactly this guard in `main()` (commit a04b9b7). See [[video-generation-spec]].
