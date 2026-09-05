---
name: structlog-cache-logger-off-for-tests
description: "`structlog.configure(cache_logger_on_first_use=True)` freezes the processor chain at first get_logger() call, breaking per-test LogCapture fixtures — must be False in this project"
---

Rule: `src/gflow_cli/observability.py:configure_logging` MUST call `structlog.configure(..., cache_logger_on_first_use=False)`. The "true" default (structlog's recommendation for production) breaks every test that relies on per-test `structlog.configure(processors=[LogCapture(), ...])` fixtures.

**Why:** With `cache_logger_on_first_use=True`, the first `structlog.get_logger()` call (which happens at module import for any module that has `log = structlog.get_logger(__name__)` at module level — most of `src/gflow_cli/`) snapshots the current processor chain into the logger and never re-resolves it. A subsequent test fixture that does `structlog.configure(processors=[LogCapture()])` writes to the global config but the cached logger keeps using the old chain. The events go to the original renderer (stdout JSON in prod, /dev/null in tests) instead of the LogCapture instance. Tests then assert `len(log_capture.entries) == N` and see `0`.

This bit four CI tests on PR #40 (`test_emits_submission_attempt_per_row`, `test_emits_row_completed_per_row`, `test_t2i_single_image`, `test_multiimage_fanout`). The `image_batch.submission_attempt` events fired but were never observed by the test fixture; the tests asserted absence and failed. The fix was a single-line flip from `True` to `False` in commit `a509dd3`.

**How to apply:**
- Do not change this back. Production overhead from `cache_logger_on_first_use=False` is negligible because structlog re-resolves the processor chain from a dict lookup, not from a heavy I/O step.
- If you ever see CI tests fail with "expected N events, got 0" on `image_batch.*` or `ui_automation.*` events, suspect this knob first.
- The pattern affects ANY structlog user that captures events per-test — not specific to gflow-cli's events. Future contributors using LogCapture should not have to learn this twice.

**See also:** `src/gflow_cli/observability.py:92` for the configure call. PR #40 commits `a509dd3` (the fix) and the CI failure trace in run id 26334465125 (showed `assert 0 == 2` with `len([]) == 0` for events that demonstrably fired in production).
