# Live Verification — v0.49.0

- **Scope**: Re-enable `omni-flash` start-frame I2V video generation (#125), Playwright dependency upper-bound pin (`playwright>=1.59.0,<1.60.0`), submission stage watchdog timeout (`TransportTimeoutError`), and count-tab fail-closed selector logic (#404) via PR #424 (merged 2026-08-03).
- **Date**: 2026-08-03
- **Credits spent**: 1 video credit (live Omni Flash I2V generation on profile `ffroliva`).

## Pre-tag gates

- `/gflow:check`: hygiene + doc-links + PII + mirror-drift ✅, ruff check/format ✅, pyright 0 errors ✅, full suite 2902 passed / 5 skipped ✅.
- `/gflow:doc-review`: mechanical pass ✅.
- SonarCloud / GitHub Actions: 13/13 CI workflow checks passed on PR #424.

## Evidence ledger (what is actually proven, and by what)

| Layer | Evidence | Status |
|---|---|---|
| **0-Credit Wire Intercept** | `capture_i2v_intercept_submit.py --model omni-flash --start-only`: intercepted submit XHR pre-network, captured `https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoStartImage` with `startImage: "5ea393ee"`, `endImage: null`, `refs: 0` | ✅ 0-credit wire |
| **Live E2E Costed Run** | `gflow video i2v --initial-frame tmp/test.jpg "cinematic slow motion pan" --model omni-flash --profile ffroliva --out-dir tmp/out_live`: submitted successfully, status `MEDIA_GENERATION_STATUS_SUCCESSFUL`, saved `tmp/out_live/bc742f56-536b-4cc7-a456-1b6b7f9fd7f2.mp4` (1,219,129 bytes) | ✅ live costed |
| **Playwright Pin Safeguard** | `pyproject.toml` constrained to `playwright>=1.59.0,<1.60.0`; tested in `tests/test_playwright_pin.py::test_playwright_pinned_range_upper_bounded` | ✅ offline |
| **Submission Stage Watchdog** | `_run_stage` watchdog aborts pre-submit on Playwright CDP stall, emits `stage_stalled`, captures screenshot, names installed Playwright version; tested in `tests/api/transports/test_ui_automation_video.py` | ✅ offline |
| **Count Tab Fail-Closed** | `_set_output_count` raises `UiSelectorDriftError` on probe miss, matches digit affixes (`xN`/`Nx`); tested in `tests/api/transports/test_ui_automation_video.py` | ✅ offline |
| **Release tree healthy** | Full suite 2902 passed / 5 skipped; green full-suite CI on merged PR #424 | ✅ |
