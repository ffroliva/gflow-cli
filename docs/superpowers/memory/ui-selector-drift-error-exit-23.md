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

**Carve-out recorded by PR #764 (2026-09-08, #763):** a selector-cascade miss on
`accounts.google.com` (the Google account chooser after the post-migration hop)
raises `FlowAccountChooserError` (exit 38), NOT `UiSelectorDriftError` (exit 23).
The chooser is Google-auth UI, not the Flow editor: reporting it as drift would
tell users to file a frontend bug about a working chooser, and the exit-23
remediation (attach diagnostics, check for a release) cannot fix a missing
account row. The miss is evidence about the *recorded account* (absent row or
a click-through that never reaches the editor). Each raise site interpolates the
observed chooser URL verbatim — there is no URL-kind taxonomy. Explicitly out
of scope: the bot-rejection hop (`.../v3/signin/rejected`) is excluded from the
chooser gate and surfaces as its own error, never as a missing account.
Recovery is `gflow auth login --profile <name>` while signed in as the recorded
account. Precedent:
exits 36 (`FlowHostMigratedError`) and 37 (`InsufficientCreditsError`) each got
the same carve-out recorded when introduced.

## Carve-out 4 — a known landing page is not drift (#756, 2026-09-10)

The broadest one, and the one that names the shared cause under the other three.
`flow_host_kind` classifies the **ORIGIN, not the page**: `/about`, `/project/<id>`
and `/fx/api/auth/signin?error=Callback` all satisfy the same host check. So every
readiness wait that timed out had nothing left to blame but its own anchor — which
is #756 (`/about` -> exit 23), #773 item 2 (a sign-in page reported as an account
chooser), and the 2026-09-10 RED canary (`Could not find 'New project' CTA` on a
NextAuth error page), all one defect at three sites.

`api/transports/_common.py::flow_landing_kind` answers the missing question
(`"signin"` / `"public"` / `None`) and `raise_if_known_landing` converts the
diagnosis: sign-in routes -> `AuthExpiredError` (3), the migrated host's `/about`
-> `FlowAppError` (31, with `retryable=False`). Consulted **only inside an
already-failed branch** — never ahead of a probe, which would delete the DOM
evidence that corrects a wrong absence claim, and never as a bounded wait after
`goto`, which reads the URL before Flow's client-side redirect lands (#639).

**The transferable lesson:** before reporting an anchor as drifted, ask whether the
page is the page you asked for. Three prior special cases (#721 credits, #749 agent
mode, `FlowAppError`'s crash page) were the same question answered one surface at a
time.
