# Security & UX Review: Auth Login via Real Chrome

**Review Date:** 2026-05-15
**Spec Version:** v0.6.0a2
**Verdict:** MINOR-EDITS

## 1. Stealth Integrity (Ref: Section 3.1)
*   **Finding:** The proposed flags (`--enable-automation` removal and `AutomationControlled`) are necessary but **insufficient** for bypassing modern Google (G12) bot detection. Google also monitors for `navigator.webdriver == true` and specific CDP-injected window properties.
*   **Recommendation:** 
    *   Explicitly require the override of `navigator.webdriver` to `undefined` in the `RealChromeStrategy`.
    *   Ensure the `ignore_default_args` parameter is used in Playwright to effectively strip the `--enable-automation` flag.
*   **Citations:** Ref Section 3, Line 1 (Decision 1).

## 2. Privacy & Data Isolation (Ref: Section 3.4 & 7)
*   **Finding:** Using `channel="chrome"` with `launch_persistent_context` poses a risk if the `user_data_dir` defaults to or is accidentally pointed at the user's *actual* personal Chrome profile.
*   **Risk:** This could cause profile locking or unintentional modification of the user's primary browser data.
*   **Recommendation:** 
    *   Add a hard requirement that `RealChromeStrategy` must ONLY use directories inside `GFLOW_CLI_HOME`.
    *   The implementation must include a safeguard that prevents using the system's default `User Data` path.
*   **Citations:** Ref Section 3, Line 4 (Decision 4); Section 7.

## 3. UX Review (Ref: Section 2 & 5)
*   **Finding:** The terminology `bundled` in `--browser [auto|chrome|bundled]` might be confusing for non-technical users.
*   **Recommendation:**
    *   Consider `internal` or `embedded` as more descriptive alternatives to `bundled`.
    *   Help text for `auto` (Section 5, AC-5) should clearly state the preference order and why a fallback occurred.
*   **Citations:** Ref Section 2, CLI Flag; Section 5, AC-5.

## 4. Success Detection Robustness (Ref: Section 4)
*   **Finding:** Depending solely on the `SAPISID` cookie for "Auth Success" (Section 4) is fast but potentially brittle if the redirect hasn't finished or the session isn't fully flushed to disk.
*   **Recommendation:**
    *   Implement a "Dual-Check" success condition: `SAPISID` cookie presence **AND** a UI-based confirmation (e.g., visibility of the Flow editor's main container).
    *   Ensure a short 500ms "settle time" after detection before closing the context to ensure the `storage_state.json` is correctly written.
*   **Citations:** Ref Section 4, "Auth Success" heading.

---
**Verdict:** **MINOR-EDITS**. The spec is technically sound but needs these hardening details to be production-ready for the v0.6.0 release.
