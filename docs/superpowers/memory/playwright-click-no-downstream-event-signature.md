---
name: playwright-click-no-downstream-event-signature
description: "A ~30-40s gap between a successful Playwright click and a TimeoutError typically means the click registered but landed on the wrong element — visible target matched, but the click didn't trigger the expected downstream UI."
---

Debugging heuristic for Playwright failures in `ui_automation*.py` paths: when you see ~30-40 seconds elapsed between the last successful structlog event and a `TimeoutError` (with NO structlog events in between), the failure pattern is almost always:

1. `locator.wait_for(state="visible", timeout=8000)` — succeeds (~8s budget)
2. `locator.click()` — succeeds silently (no exception raised)
3. Downstream wait (`expect_file_chooser`, `wait_for_event`, `wait_for_function`, etc.) — Playwright's default 30s timeout

Total elapsed: 8 + 30 ≈ 34-38 seconds. **That's the signature.**

The visible meaning: the selector matched something visible and clickable, but the click landed on the WRONG element — usually a sibling that looks similar but isn't interactive in the expected way, or a localized text variant that doesn't trigger the action handler.

**Why:** incident #56 — `_attach_references` had `add_media.wait_for(timeout=8000)` SUCCEED + `add_media.click()` SUCCEED + `_upload_via_open_dialog` waited 30s for the `filechooser` event that never fired → 34s gap between `count_setter_completed` and `error_unhandled TimeoutError`. The click hit the right button but the post-click popover's "Upload media" item was matched by a `has-text('Upload media')` selector that missed on pt-BR ("Enviar mídia"). See [[flow-locale-leak-icon-ligatures]].

**How to apply:**

1. **Triage**: when debugging a Flow CLI hang, compute the elapsed gap between the last structlog event and the error. ~30-40s with no intermediate events is the click-mislandedlanded signature.
2. **DOM dump first**: reach for `scripts/dev/capture_image_add_media_dom.py` (or the `capture_locale_invariants.py` family pattern — both reuse production helpers via `FlowApiClient` to avoid selector drift). Dump the DOM around the click target on the *affected* profile. Compare to a working profile if you have one.
3. **Add per-click screenshots**: any new `_attach_*` / `_upload_*` helper should emit a screenshot BEFORE and AFTER the suspicious click, not just on the `wait_for` miss. PR #60's fail-loud `RuntimeError` fires only if both upload tiers miss — a per-click screenshot would have surfaced the locale issue faster.
4. **Other gap signatures to know**:
   - ~3-minute gap with no `batchGenerateImages` response → image listener miss (separate flake, see [[phase-b-followups]])
   - <1s `TimeoutError` immediately after `__aenter__` start → profile auth issue (re-login required)
   - Fast 401 with no UI events → REST session expired (different from UI auth — see [[real-browser-auth-mandatory]])
