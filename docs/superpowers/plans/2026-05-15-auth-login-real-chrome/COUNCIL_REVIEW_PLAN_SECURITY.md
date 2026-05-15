# Council Review: Implementation Plan - Security & Quality

**Review of Plan:** `docs/superpowers/plans/2026-05-15-auth-login-real-chrome/PLAN.md`  
**Orchestration:** `docs/superpowers/plans/2026-05-15-auth-login-real-chrome/orchestration.md`  
**Reviewer:** Senior Code Reviewer (Agent)  
**Date:** 2024-05-15 (Simulated)

## 1. Test Coverage Analysis
**Status:** ⚠️ **Insufficient**

*   **Focal Point:** Ensure 80%+ coverage for `src/gflow_cli/auth/`.
*   **Findings:** 
    *   The plan allocates tasks for Factory tests (T1.1) and BDD scenarios (T1.2).
    *   **GAP:** There are no explicit unit tests for `RealChromeStrategy` or `InternalChromiumStrategy` logic (e.g., error handling, path resolution, stealth patch application).
    *   BDD tests are excellent for integration but typically do not hit all edge cases in the strategy implementations required to reach 80% coverage.
*   **Recommendation:** Add a task `T1.4: Strategy Unit Tests` to Phase 1. This should specifically target mocking Playwright browser contexts to verify strategy-specific behavior without opening real windows.

## 2. Security Verification (Privacy Guard)
**Status:** ❌ **Not Explicitly Tested**

*   **Focal Point:** Verification of the 'Privacy Guard' implementation (T2.2).
*   **Findings:**
    *   The implementation of the Privacy Guard is correctly identified in T2.2 (preventing leakages outside `GFLOW_CLI_HOME`).
    *   **GAP:** The plan fails to define a test case in the RED phase (Phase 1) that verifies this guard. 
*   **Recommendation:** Update `T1.1` or add `T1.4` to include: "Verify `RealChromeStrategy` raises exception if a `user_data_dir` outside `GFLOW_CLI_HOME` is provided."

## 3. Manual Verification (Smoke Testing)
**Status:** ❌ **Missing**

*   **Focal Point:** Manual smoke-test recipe in release ceremony or documentation.
*   **Findings:**
    *   **GAP:** The plan lacks a specific task for a manual smoke test. Given that "Real Chrome" interactions can be brittle due to local environment variations (Chrome version, OS-level security prompts), a manual verification step is essential.
*   **Recommendation:** Add a task to **Phase 3** or **Phase 4**: `T4.3: Manual Smoke-Test Execution`. This should follow a "Recipe" (e.g., Run `gflow auth login --browser chrome`, perform real login, verify `SAPISID` is captured and persisted).

## 4. UX (Optimistic Orchestration)
**Status:** 🟡 **Minor Edits Required**

*   **Focal Point:** Correct ordering of T2.4 to prevent brittle behavior.
*   **Findings:**
    *   T2.4 correctly identifies the polling targets (`SAPISID` cookie + `New project` UI element).
    *   The Risk Management section in `orchestration.md` correctly notes the need for a "settle-time".
    *   **GAP:** The plan description "1s polling for SAPISID cookie + New project UI element" does not explicitly define the terminal state condition. 
*   **Recommendation:** Clarify T2.4 implementation details: "The login is successful only when (Cookie is valid AND UI element is interactive). Wait for 500ms after detection to ensure session persistence before closing the browser."

## 5. Summary of Plan Deviations
*   The plan is structurally sound but leans too heavily on BDD for coverage.
*   Security features (Privacy Guard) are treated as implementation details rather than verifiable requirements.

## Recommendations
1.  **Add T1.4 (Phase 1):** Unit tests for Strategy logic and Security Guards.
2.  **Add T4.3 (Phase 4):** Manual smoke-test recipe and ceremony step.
3.  **Refine T2.4 (Phase 2):** Explicit "AND" condition and settle-time for polling.

## Verdict
**Verdict:** **MAJOR-REVISION**

*Reasoning:* The current plan will likely fail the 80% coverage requirement and lacks the necessary testing rigor to guarantee the 'Privacy Guard' security feature works as intended before implementation begins. The absence of a manual smoke test recipe for a high-risk UI feature is a quality blocker for release.
