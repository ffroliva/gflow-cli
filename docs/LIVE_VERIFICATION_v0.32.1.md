# Live verification — v0.32.1 (2026-07-11)

Patch release: the #293 teardown fix (PR #294) plus the #283 follow-up wave
(PR #292). All verification credit-free.

## 1. Browser teardown — no leaked Chrome after error exits (#293, PR #294)

Baseline (pre-fix, same day): three separate error-exit runs on the `denon82`
profile left a full Chrome tree holding the profile dir; the next run died at
context launch with `TargetClosedError` → "Unexpected error." exit 1.

Post-fix live repro, run twice (pre- and post-review-refactor):

1. Trigger an error-path abort: `gflow video i2v --initial-frame
   <foreign-UUID> … --project dd9e498c-… --profile denon82` → **exit 9**
   (typed picker-miss abort, pre-credit).
2. Immediately after process exit:
   `Get-CimInstance Win32_Process -Filter "Name='chrome.exe'"` filtered on
   `profile_denon82` in the command line → **0 processes**, both runs.
3. Follow-up launches in the same session succeeded cleanly (no
   `TargetClosedError` at launch).

Honest caveat: in both post-fix runs the *graceful* close succeeded within the
bound, so the force-close fallback did not fire live — its trigger (a wedged
close) is nondeterministic. The fallback branch is pinned by unit tests
(`tests/api/test_concurrency.py`: force-close ordering before driver stop,
double-failure last resort, hang-timeout path, both `_is_target_closed`
branches), and `context.browser` being non-None for persistent contexts was
verified against the pinned Playwright 1.59.0 package source.

The `ProfileLockedError` (exit 11) launch translation is unit-verified; a live
sample requires deliberately wedging a browser and was not manufactured.

## 2. #283 wave (PR #292) — logic-level fixes, unit-pinned

The picker scroll off-by-one, `await_images` stable-break, and phantom
screenshot paths are pure logic around mocked DOM scrapes — the unit tests are
the affected surface (mock-verified to the exact call counts: 12 pre-scroll
checks + 1 post-loop re-check). The phantom-screenshot fix's real-world
trigger was itself observed live on 2026-07-11 (an error message named a
`debug_no_mode_trigger.png` that was never written — the empty directory is
the evidence); post-fix, capture failures return `None` and every message
appends its `Screenshot:` clause conditionally.

Deferred-with-reason: no credited generation was needed for this patch — no
generation-path behavior changed.
