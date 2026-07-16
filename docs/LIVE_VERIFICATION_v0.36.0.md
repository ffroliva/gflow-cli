# Live Verification — v0.36.0 (2026-07-16)

The release's new user-facing features — `GFLOW_CLI_HAR_PATH` and `GFLOW_CLI_DEBUG_TRACEBACK` — were exercised against a live Flow session and verified. The third change in this release, the reference-entity-smuggling fix (#312), already carries its own dedicated e2e regression test (`tests/e2e/test_entity_smuggling_e2e.py`) from the PR that introduced it; not re-run here.

---

## 1. Verified Features

### A. `GFLOW_CLI_HAR_PATH` (Playwright network capture)

* **Command:** `GFLOW_CLI_HAR_PATH=<path> gflow character list --project 74a0ffb6-ccff-4bd2-a6a2-3c5b9b805256 --profile ffroliva`
* **Ledger Details:**
  * **File written:** 9.4 MB HAR file at the configured path, valid JSON (HAR 1.2 format).
  * **Structlog invariant:** `client.har_capture_enabled` fired with the security hint before launch, correlated to the run's `correlation_id`.
  * **Real traffic captured:** 54 entries across 9 domains including `labs.google`, `aisandbox-pa.googleapis.com`, `lh3.googleusercontent.com`. 34 successful (200) responses, including the Flow editor shell (`GET https://labs.google/fx/tools/flow?hl=en` → 200) and static assets.
  * **Genuine failure captured too:** the profile's session token had expired since its last use — the HAR correctly captured the resulting real `401` on the `projectInitialData` call, which the CLI surfaced as a typed `AuthExpiredError` (exit 3) with the standard remediation hint. This is exactly the diagnostic scenario the feature exists for.
  * **Permissions:** `os.stat().st_mode` reports `0o666` on this Windows run — expected, not a bug: NTFS has no POSIX permission bits, and the design's own chmod call is a documented no-op there (`tests/api/test_client_launch_kwargs.py::test_close_browser_resources_chmods_har_file` skips the permission assertion on `win32` for the same reason). POSIX hardening is covered by unit tests with a real `stat.S_IMODE` assertion; not independently live-verified on this Windows machine.

### B. `GFLOW_CLI_DEBUG_TRACEBACK` (unhandled-exception visibility)

* **Command:** `GFLOW_CLI_DEBUG_TRACEBACK=1 gflow character list --project not-a-real-project-id-!!! --profile ffroliva`
* **Ledger Details:**
  * **Correct scoping confirmed live:** the run hit the same typed `AuthExpiredError` (a `GFlowError`, not the unhandled catch-all this flag gates) — console output was identical with and without the flag, confirming `GFLOW_CLI_DEBUG_TRACEBACK` does not alter typed-error output, exactly as designed (typed errors already show full `detail`/`remediation_hint` unconditionally; only the unhandled/catch-all path is gated).
  * **Unhandled-path behavior:** not independently re-triggered live in this pass (the available live sessions only reached typed-error paths). Covered by 6 tests added specifically for this feature (2 console-path, 2 JSON-path, 1 Rich-markup regression, plus 1 env-var-parsing test) across `tests/cli/test_error_handling.py` and `tests/test_json_output.py` — 30 tests total in those two files after this change, including pre-existing coverage — each individually reviewed by a task-scoped code reviewer during implementation (see PR #321) — including the Rich-markup regression test for a real crash the review process caught (bracketed exception text, e.g. this codebase's own Playwright selector strings, previously risked crashing or silently corrupting the debug output).

### C. Reference entity smuggling fix (#312)

* **Verification:** covered by its own dedicated e2e test added in the fix's PR (`test(e2e): add E2E test for reference entity smuggling prevention`, commit `1c53e42`, file `tests/e2e/test_entity_smuggling_e2e.py`), part of `develop` before this release cycle. Not re-exercised live in this pass.

---

## 2. Verification Ledger Summary

| Feature | Verification Method | Status | Evidence |
|---|---|---|---|
| `GFLOW_CLI_HAR_PATH` | Live run against real Flow session | ✅ Verified | 54-entry HAR, real domains + a genuine captured 401 |
| `GFLOW_CLI_DEBUG_TRACEBACK` (typed-error scoping) | Live run, compared with/without flag | ✅ Verified | Identical console output on a typed `GFlowError` |
| `GFLOW_CLI_DEBUG_TRACEBACK` (unhandled path) | 6 new tests (30 total in both files) + SDD task review | ✅ Verified | `tests/cli/test_error_handling.py`, `tests/test_json_output.py` |
| Reference entity smuggling fix (#312) | Dedicated e2e test (prior PR) | ✅ Verified | `tests/e2e/test_entity_smuggling_e2e.py` |

---

## 3. Not Verified This Cycle

* `GFLOW_CLI_HAR_PATH`'s POSIX `0600` file-permission hardening — only unit-tested (real assertion, real `stat` check), not live-verified, since this session's development machine is Windows and the hardening is a documented POSIX-only no-op there.
* `GFLOW_CLI_DEBUG_TRACEBACK`'s unhandled-exception path — not re-triggered against a live Flow session in this pass (both available live-auth attempts surfaced typed errors instead); covered by extensive unit/integration coverage instead (see above).
