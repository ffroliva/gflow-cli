# Spec: Auth Login via Real Chrome (`v0.6.0a2`)

**Goal:** Restore the ability to sign in to Google Flow by routing `gflow auth login` through the system's installed Google Chrome ("Real Chrome") to bypass the G12 security block.

## 1. Problem Statement
Google currently identifies and blocks Playwright's bundled Chromium during the sign-in flow with the error: *"This browser or app may not be secure"*. While existing authenticated profiles still work for generation, fresh logins or re-authentications are impossible without brittle manual workarounds.

## 2. Surfaces
- **Auth Strategies**: Refactor `src/gflow_cli/auth.py` (module) into a package `src/gflow_cli/auth/` containing:
  - `base.py`: Defines the `AuthStrategy` `typing.Protocol`.
  - `real_chrome.py`: Uses a **Passive Capture** strategy. It launches the system's real Chrome without any automation flags/ports, waits for the user to sign in and close the window, and then verifies the resulting session.
  - `internal_chromium.py`: Legacy behavior (formerly "bundled"), kept as a fallback.

## 3. Locked Decisions
1. **Passive Capture is Mandatory**: Any active automation (CDP, WebDriver flags) during the login flow is currently detected by Google. Login MUST happen in a 100% standard process.
2. **Strategy Pattern**: Auth logic must mirror the `UiAutomationTransport` factory pattern (lazy registry) for consistency.
3. **Smart Defaults**: `auto` mode probes for Real Chrome; falls back to `internal` if missing.
4. **No Breaking Changes**: Existing `profile_<name>` directories must remain compatible.
5. **TDD Workflow**: Failing tests for all strategies and the factory must be written before implementation.
6. **Privacy Guard**: `RealChromeStrategy` must validate that the provided `user_data_dir` is NOT the user's primary system Chrome profile.
7. **Clean Exit**: The CLI will wait for the browser process to terminate before proceeding to verify the capture.

## 4. Performance & UX
- **No More Stalls**: By removing automated click-throughs (which Google detects), we eliminate the hangs and timeouts seen in previous attempts.
- **Clear Instructions**: The CLI will provide a prominent "LOG IN THEN CLOSE BROWSER" instruction.
- **Session Verification**: After the window closes, the CLI will perform a fast, headless check for `SAPISID` to confirm success.

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
