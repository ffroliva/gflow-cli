# Spec: Auth Login via Real Chrome (`v0.6.0a2`)

**Goal:** Restore the ability to sign in to Google Flow by routing `gflow auth login` through the system's installed Google Chrome ("Real Chrome") to bypass the G12 security block.

## 1. Problem Statement
Google currently identifies and blocks Playwright's bundled Chromium during the sign-in flow with the error: *"This browser or app may not be secure"*. While existing authenticated profiles still work for generation, fresh logins or re-authentications are impossible without brittle manual workarounds.

## 2. Surfaces
- **Auth Strategies**: Refactor `src/gflow_cli/auth.py` (module) into a package `src/gflow_cli/auth/` containing:
  - `base.py`: Defines the `AuthStrategy` `typing.Protocol`.
  - `real_chrome.py`: Uses Playwright's `channel="chrome"` with stealth patches.
  - `internal_chromium.py`: Legacy behavior (formerly "bundled"), kept as a fallback.
- **CLI Flag**: `--browser [auto|chrome|internal]` added to `gflow auth login`.
- **Environment Variable**: `GFLOW_CLI_AUTH_BROWSER` for operator-level defaults.
- **Factory**: `AuthStrategyFactory` mirroring the `api/transports` registry pattern.

## 3. Locked Decisions
1. **Stealth is Mandatory**: Must ignore `--enable-automation`, use `--disable-blink-features=AutomationControlled`, and inject a script to set `navigator.webdriver = undefined`.
2. **Strategy Pattern**: Auth logic must mirror the `UiAutomationTransport` factory pattern (lazy registry) for consistency.
3. **Smart Defaults**: `auto` mode probes for Real Chrome; falls back to `internal` if missing.
4. **No Breaking Changes**: Existing `profile_<name>` directories must remain compatible.
5. **TDD Workflow**: Failing tests for all strategies and the factory must be written before implementation.
6. **Privacy Guard**: `RealChromeStrategy` must validate that the provided `user_data_dir` is NOT the user's primary system Chrome profile to prevent data corruption/leaks.

## 4. Hypothesis: Optimistic Orchestration (Performance Optimization)
Current automation often relies on conservative `wait_for_url` or static sleeps. To reduce "dead time" in the CLI:

- **Auth Success**: Shift from 5s polling to a 1s loop checking for the `SAPISID` cookie. Reaching the `New project` or `Your projects` text in the DOM serves as the final confirmation.
- **Generation Tracking**: Monitor for specific DOM mutations (e.g., overlay appearance) to signal "Generation Started" rather than waiting for full page stability.
- **Fast-Path Action**: Use Playwright's `wait_for_selector(..., state="attached")` for non-visual interactions.

## 5. Acceptance Criteria
1. **AC-1**: `gflow auth login --browser chrome` opens a stealth-hardened Real Chrome window.
2. **AC-2**: Google Login accepts credentials without a "browser not secure" block.
3. **AC-3**: The CLI automatically detects login completion via cookie state + UI signals and exits cleanly (no manual window-close required).
4. **AC-4**: `gflow auth login --browser internal` still functions as the legacy fallback.
5. **AC-5**: If `auto` is used and Chrome is missing, the CLI warns the user and falls back to `internal`.
6. **AC-6**: Unit tests verify the factory logic and strategy selection across all three OSes.

## 6. Out of Scope
- Support for Chrome Beta, Canary, or Edge.
- Automatic password injection or MFA handling (remains operator-interactive).
- Refactoring `UiAutomationTransport` itself (only `auth` is targeted).

## 7. Migration
Zero action required. The `storage_state.json` format is standard across Chromium-based browsers; sessions captured in Real Chrome will work seamlessly with the existing `UiAutomationTransport`.

---
_Next Phase: Phase E — Implementation Plan._
