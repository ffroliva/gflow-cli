# Spec — Harmonize auth-context viewports to 1920×1080 (#315 follow-up)

**Date:** 2026-07-16 · **Branch:** `chore/auth-viewport-1080p` · **Follows:** PR #327
(generation viewport → 1920×1080) · **Status:** in progress

## Problem

After #327 moved the `UiAutomationTransport` generation viewport to 1920×1080, the two
**auth-login** contexts still open at the old `1280×800`:

- `internal_chromium.py:151` — Playwright `viewport={"width": 1280, "height": 800}` in the
  internal-Chromium login `launch_persistent_context`.
- `real_chrome.py:68` — `"--window-size=1280,800"` Chrome CLI arg for the real-Chrome login.

This is a cosmetic inconsistency: the browser a profile logs in with is a different size
than the browser it later generates with.

## Scope

**In:** change both auth viewports to `1920×1080` (Playwright dict + `--window-size` string).

**Out:** the `FlowApiClient` REST context (`client.py`, 1280×720) — independent,
selector-irrelevant, unchanged. No shared-constant abstraction (the three viewports are
independent knobs of different purpose/format — coupling them would be over-engineering).

## Why this is low-risk (verification bar)

Both are **login-window size only**, NOT selector-sensitive:
- `internal_chromium.login()` navigates to the Google sign-in page; the **user** signs in
  manually while gflow polls the **session token** (`_poll_session_until_authenticated`) —
  gflow never drives login-page selectors.
- `real_chrome` launches the real Chrome the user logs into.

So there is no generation-selector surface to break. A headed login e2e is both
**impractical** (interactive Google sign-in can't be automated overnight) and
**unnecessary** (no selector path). Verification = unit tests asserting the new size in
each launch path + `/gflow:check`.

## Acceptance criteria

1. `internal_chromium.py` launch viewport == `{"width": 1920, "height": 1080}`.
2. `real_chrome.py` args contain `--window-size=1920,1080`.
3. Unit tests assert both (added/updated); all gates green (`/gflow:check`).
4. No other 1280×800 auth references remain.

## Value / caveat

Small consistency tidy-up. The fingerprint gain is marginal (login and generation are
different session moments), so this is primarily code/behavior consistency, not a stealth
measure — stealth humanization stays parked under ADR-13.
