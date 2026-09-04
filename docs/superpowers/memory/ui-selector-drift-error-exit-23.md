---
name: ui-selector-drift-error-exit-23
description: "Selector-probe failures = typed UiSelectorDriftError exit 23, never bare RuntimeError — RuntimeError messages get hashed by observability redaction so users see only \"Unexpected error\" even with --verbose"
---

**Selector-probe failure contract (PR #184, issue #183):** any UI-automation selector-cascade miss must raise `UiSelectorDriftError` (exit 23) with detail built by `selector_drift_detail(probe, what, shot)` (in `ui_automation_video.py`) — never a bare `RuntimeError`.

**Why:** non-`GFlowError` exceptions hit the `error_unhandled` path where `observability.py` SHA-256-hashes the message (`message_hash`) for privacy — the user sees only "Unexpected error", even with `--verbose`. This was the root cause of issue #183's useless report. Typed `GFlowError`s route through `_handle_gflow_error` (`_cli_helpers.py`) → real title/detail/remediation printed + mapped exit code.

**How to apply:**
- Converted sites (PR #184): mode-switch trigger, image/video mode tabs, video sub-mode tabs. PR #405 (issue #404) converted `_set_count` (the last hard-failing settings-panel setter) — typed error carries `desired=`/`displayed=` + screenshot. ~17 sibling `raise RuntimeError` selector sites remain in transports (model picker `ui_automation.py:~2455`, prompt box `~1042`, add_2 `~2737`, video frame slots, etc.) — migrate them to the typed error when touched; follow-up tracked on the repo.
- Debug screenshots require `FlowApiClient(out_dir=...)` — `_plumb_out_dir` forwards to `transport._out_dir`. The image surface (`cli_image.py` ×4) was wired in PR #184; `cli_character.py` (×4) is still unwired → its drift errors will (correctly) omit the Screenshot clause.
- `selector_drift_detail` omits the `Screenshot:` clause when `shot is None` — don't reintroduce f-strings that render `Screenshot: None`.
- Exit code 23 documented in `docs/USAGE.md`; EXIT_CODE_MAP entry is a direct `GFlowError` subclass (ordering invariant unconstrained).

**Remediation contract updated by PR #504 (2026-08-13, #493):** `UiSelectorDriftError._default_remediation` now asks for "the diagnostics JSON and/or debug screenshot referenced in this message, plus the incident bundle's report.md" — the old "debug screenshot from this message" was a false promise on the mode-switch probe, which writes `diag_mode_switch_miss.json` ONLY (no screenshot; the full-page screenshot lives in the incident bundle's `sensitive/`). The exit-23 mode-switch fall-through detail additionally names the unrecognized-new-variant hypothesis. See [[issue-493-third-editor-variant-predict-stop]].

See [[pr-184-e2e-drift-sim-results]], [[flow-library-ui-drift-174]], [[exit-code-map-ordering-invariant-test-pitfall]].
