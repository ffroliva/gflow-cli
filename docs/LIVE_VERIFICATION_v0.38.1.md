# Live verification — v0.38.1 (2026-07-17)

Release scope: **agentic-pin recovery** (`mode_control.ensure_media_mode` — opt-in
reload rescue, unforced toggle click, composer-readiness waits, crop-selector
canonicalization). Verified against live Flow on the profile that was
**actively server-pinned agentic for ~2h** (denon82, standing project
`f6caf027`) — the exact incident condition the release fixes. Credit-free
(image t2i only).

## Run 1 — release-branch code WITHOUT the readiness wait: gate CAUGHT a flaw

`uv run gflow image t2i "a single ripe mango …" --model nano-pro --aspect 9:16
--ui-mode classic --profile denon82 --project f6caf027…` (correlation
`884bb065-2…`):

- `ui_driver.ui_mode.attempt_exit_agent` 22:35:04.361 →
  `mode_control.ensure_media_incomplete` 22:35:04.476 — **+115 ms**, no toggle
  click, no grace poll, no reload. The controller probed the freshly-navigated
  page BEFORE the SPA composer rendered: every selector counted 0, the loop
  broke as "nothing actionable", and the entire rescue chain (click → persist →
  grace → reload) was unreachable. `detect_ui_mode` then polled 8 s, saw the
  agentic ligature on the rendered page, and aborted with
  `UiModeUnavailableError` (exit 28, no credits).
- Conclusion: in ALL prior production failures the Agent toggle was likely
  **never clicked at all** — the render race, not the click mechanics, was the
  first-order bug. Fixed by an initial `_wait_until(_composer_present, 8s)` at
  the top of `ensure_media_mode` (unit-locked by
  `test_waits_for_composer_render_before_probing`).

## Run 2 — complete fix: PIN BROKEN, classic recovered

Same command (correlation `750fd69b-2…`), ~90 s later on the same pinned
profile:

- `ui_driver.ui_mode.attempt_exit_agent` 22:36:21.106 →
  `ui_driver.bound mode=classic ui_mode=classic` 22:36:24.048 — classic
  composer reached on an account that had refused it since ~20:41.
- Generation submitted and completed; media downloaded.

## 5-layer evidence ledger (run 2 artifact)

| Layer | Evidence |
|---|---|
| File count | 1 file in `--out` dir: `c24931c6-ca8a-4841-b6c9-b8cf093bd91d_1.jpg` |
| Magic bytes | `ff d8 ff` (JPEG) |
| Dimensions | 768×1376 (9:16 portrait, as requested) — 806,226 bytes |
| Structlog invariants | run 1: `ensure_media_incomplete` at +115 ms, `error_raised UiModeUnavailableError`, `cli_version 0.38.1`; run 2: `ui_driver.bound mode=classic`, no error events, `cli_version 0.38.1` |
| User-confirmable artifact | Image visually confirmed: photorealistic ripe mango on weathered wooden table, soft window light — exact prompt adherence, correct media (not a wrong-media library asset) |

The verification artifact is a throwaway (not committed; `out_lv0381/` deleted
after the ledger was recorded).

## Not verified this cycle (recorded, not omitted)

- The **reload path itself did not fire in run 2** — the readiness wait alone
  let the toggle be reached and the classic panel mounted in place (or the
  server pin had co-incidentally released between runs 1 and 2; run 1 aborted
  agentic 90 s earlier, making in-place recovery via the now-reachable toggle
  the more likely explanation). The reload branch is unit-locked
  (`test_pinned_arm_recovers_via_reload`) but its live trigger condition
  (real click lands + panel still absent) was not observed live this cycle.
- Multi-ref i2i at 5 refs (Phase-0R consumer question) — out of scope for this
  release; tracked in the monorepo's pre-s009 calibration spike.
