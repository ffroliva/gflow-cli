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

## Carve-out 5 — an account with no Flow access is not drift (2026-09-15, exit 39)

The same question as carve-out 4, asked one layer deeper. A Google account with no
Google AI Plus/Pro/Ultra (or qualifying Workspace) plan is served Flow's own
`<flow-pinhole-unavailable-screen>` instead of the app — so the URL half returns
`None` (the screen sits on a Flow origin, on an ordinary path) and the caller's drift
report stood. Measured as an **A/B through the same command** on a real unentitled
account: probe neutered → exit **23**, *"Google may have updated their frontend …
file a bug"*, incident bundle written including `sensitive/screenshot.png` of the
user's own signed-in page; probe restored → exit **39**, naming the subscription.
gflow blamed its own selectors for a missing subscription and asked the user to
report it.

`raise_if_known_landing` now probes the DOM **before** it reads the URL and raises
`FlowAccessUnavailableError` (39, `retryable=False`). Three things make this carve-out
different from 1–4:

- **The anchor is a component, not a URL.** The spike
  (`docs/superpowers/spikes/2026-09-15-unentitled-account-signal.md`) pre-registered
  four signals and refuted all four: no entitlement field on the wire, HTTP **200**
  with the hop done client-side (no 3xx), and an unstable path — `/unavailable` and
  `/u/8/unavailable` both observed. A path matcher would have passed the spike's own
  arm and missed the other.
- **The guard was not where the failure was.** `ui_automation._enter_editor` returns
  early when `--project` is supplied, with no readiness gate, so the guard in its
  gallery arm never ran and the labs drift surfaced from `_switch_to_{image,video}_mode`
  instead. Found by the council, not by the tests — and then A/B'd like the first arm:
  with that second guard removed, the offline labs scenario fails with
  `UiSelectorDriftError('probe=mode_switch_trigger: ... the editor may be a new Flow UI
  layout this gflow-cli version does not recognize yet (issue #493)')`. **One chokepoint
  is a claim about control flow — grep every raise site of the class you are converting,
  not just the ones that already call the helper.** The labs arm cannot be run live here
  (every profile we hold is served flow.google.com), so the offline scenario plus its
  neutered control is the whole proof, and that is why it exists.
- **Deliberately not incident-captured.** A direct `GFlowError` outside
  `_capture_triggers()`, so `should_capture()` returns False: the remediation is a
  subscription and a DOM dump tells nobody anything. If Flow renames the component the
  class stops firing and the failure reverts to exit 23, which *is* captured — the
  evidence path for the case that needs evidence stays open.

Unfixed siblings, recorded rather than implied: `auth login` (8), `auth status` (1)
and the labs REST 401 at `project.createProject` (3) are wrong on such an account too.
They read an oracle that cannot discriminate — the session endpoint answers 200 with
an empty `user` for an abandoned sign-in *and* for no entitlement — and on the login
path Chrome has already closed by the time the verdict runs.
