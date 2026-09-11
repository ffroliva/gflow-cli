# Does Google's glue cookie bar render on flow.google.com, and does it block? (#780 / PR #781)

- **Date:** 2026-09-11
- **Script:** [`scripts/dev/spike_migrated_cookie_bar.py`](../../../scripts/dev/spike_migrated_cookie_bar.py)
- **Profile / project:** `ci-probe` · `1e4efe0d-…` (migrated host, `flow.google.com`)
- **Cost:** $0 — navigation, DOM reads, and one consent click in the control arm. No
  credits, no quota.
- **Raw:** `scripts/dev/_spike_out/spike_migrated_cookie_bar_20260911_0951*.json` and
  `…_0953*.json` (gitignored)

## The question

[#780](https://github.com/ffroliva/gflow-cli/issues/780) opens with a claim, and PR #781
builds three gates on it:

> Google's glue cookie banner (`glue-cookie-notification-bar`) sits fixed at the viewport
> bottom and intercepts every pointer event over the composer's settings trigger.

Nothing in this repo had ever observed that bar on `flow.google.com`. Both prior sightings
are **labs.google**: `test_assets/debug_editor/buttons.json` (its sibling buttons carry
styled-components classes, which is labs' React build, not the migrated host's Angular
one) and `scripts/smoke_video_editor.py:385`. And yesterday's spike,
[`2026-09-10-migrated-click-blocked.md`](2026-09-10-migrated-click-blocked.md), read 159
DOM samples on this exact host and project, found **zero** overlays and **zero** dialogs,
and watched the click land 3/3 in 65–130 ms.

That spike never looked for a cookie bar. So it neither found nor excluded one.

```
A SELECTOR NOBODY LOOKED FOR IS NOT AN ABSENCE.
A BAR THAT EXISTS IS NOT YET A BLOCKER.
```

Two questions, because #780 conflates them: does it render here, and if it does, does it
actually win `elementFromPoint` over the controls the driver clicks.

## Pre-registered readings

Written into the script's docstring before the run.

| Outcome | Reading |
|---|---|
| bar visible, wins `elementFromPoint` over a control | #780's premise holds |
| bar visible, controls still hit-test to themselves | the bar EXISTS and does NOT block — premise falsified |
| bar absent on migrated, present on labs | a labs surface; #781 guards the Angular composer against React-app furniture |
| bar absent on both, glue bundle absent too | strongest available negative |
| bar absent on both, glue bundle PRESENT | **does not reproduce here; settles nothing** — consent already stored |

## What was observed

6 samples across the composer-mount window, then a 4-sample control arm.

| Reading | Before | After the driver's `_dismiss_cookie_bar` |
|---|---|---|
| bar elements visible | 6 / 6 | 0 / 4 |
| samples where a control was covered by the bar | 5 | 0 |
| settings trigger hit-testable | 0 / 5 rendered | 4 / 4 |
| image submit hit-testable | 0 / 5 rendered | 4 / 4 |

The bar is `div#glue-cookie-notification-bar-1.glue-cookie-notification-bar`,
`position: fixed`, `z-index: 1000`, `pointer-events: auto`, occupying y 547→655 of a
655 px viewport. Its consent bundle is served from
`https://www.gstatic.com/glue/cookienotificationbar/cookienotificationbar.min.{js,css}`
and it installs a `glue` global.

Both controls sit **inside that band**: the settings trigger at y 582 (h 32) and the image
submit at y 582 (32×32). `elementFromPoint` over each returned
`span#glue-cookie-notification-bar-1-label` in every rendered sample. Flow's composer is
itself bottom-fixed (`flow-prompt-box.prompt-box-container` appears in the same
bottom-fixed inventory), so this is a collision between two bottom-anchored layers, not an
artifact of a short window.

## Verdict: #780's premise is CONFIRMED, and it is broader than #780 says

The bar renders on the migrated host, and it covers **both** the settings trigger *and*
the image submit button. #780 names only the trigger. PR #781's `_dismiss_cookie_bar`
cleared it in one attempt and restored hit-testability on both controls, 4/4.

**This does not contradict yesterday's 0/3.** Same profile, same project, seventeen hours
apart: the consent bar was absent then and present now. Yesterday's spike said the state
it could not measure was one "you cannot summon on demand — a first visit after a Flow
deployment". This is that state, arriving on its own. It is the reason that spike declined
to call 0/3 evidence of transience, and it was right to.

## How widely does it fire? Still n=1

A second profile was attempted specifically to answer this, and it did not answer it.

`denon82` — an independent account, never touched by this spike — was driven at both
hosts. **Both arms landed on `https://flow.google.com/about`**, not the project, so no
composer ever mounted: `trigger_rendered_in` and `submit_rendered_in` were 0/5 in both.
That run measured a marketing page. It is not a second sample and must not be counted as
one.

The one thing it does say: on that origin the bar was **in the DOM but hidden** (5/5), so
consent is already stored for `denon82`. Which is consistent with the mechanism — the bar
is consent-state dependent, so it fires on a rolling subset of profiles rather than on
everyone at once — but it is corroboration, not a measurement of prevalence.

**So: one account confirmed blocked, one account confirmed already-consented, prevalence
unmeasured.** What would settle it: the same probe on several profiles that reach the
editor. The `/about` redirect that stopped this one is separately recorded in
[`2026-09-10-about-redirect-stability.md`](2026-09-10-about-redirect-stability.md) and was
not investigated here.

## Two more things this did NOT measure

**The labs contrast arm failed.** `ci-probe` is a moved account, so
`labs.google/fx/tools/flow/project/<id>` redirected straight to `flow.google.com` and the
second arm re-measured the first. Whether an **unmoved** account sees the same bar on the
labs editor is still unknown. It matters for PR #781's other half, which routes unmoved
accounts onto the migrated host for images.

**The state is now consumed.** The control arm clicked the bar's first button to dismiss
it, so a re-run on `ci-probe` will read 0/N until Google re-prompts. Capture before you
dismiss.

## A finding the control arm produced by accident

The bar's buttons are, in DOM order, `glue-cookie-notification-bar__accept` ("Agree") then
`glue-cookie-notification-bar__reject` ("No thanks"). PR #781 dismissed with
`buttons.first`, so it **accepted** cookies on the user's behalf — and because the control
arm ran that version, this spike accepted on `ci-probe` before anyone noticed.
`scripts/smoke_video_editor.py` still does the same. Rejecting unblocks the composer
identically, so index 1 is the same fix without making a consent decision for the operator.

The shipped fix (PR #782) therefore anchors on `.glue-cookie-notification-bar__reject`,
and re-running this spike's `--dismiss` arm today rejects rather than accepts: it imports
the driver's own `_dismiss_cookie_bar` instead of clicking the bar itself, so the control
arm always measures whatever actually ships.

## What this means for the fix

The dismissal earns its place and should land. The gates built *around* it are a separate
question: the hit-test gate refuses on a covered submit, which is the state measured here,
but PR #781 also reports the covering element's raw `id` and `className` into a
user-facing error, which is the data
[#776's](https://github.com/ffroliva/gflow-cli/issues/776) closed allowlist exists to keep
out of one.

## Related

- [`2026-09-10-migrated-click-blocked.md`](2026-09-10-migrated-click-blocked.md) — the 0/3 this completes
- [`2026-09-05-migrated-frames-attach.md`](2026-09-05-migrated-frames-attach.md) — a non-blocking "high demand" banner on the same host
