# Is the migrated composer's settings trigger ever visible-but-unclickable? (#776)

- **Date:** 2026-09-10
- **Script:** [`scripts/dev/spike_migrated_click_blocked.py`](../../../scripts/dev/spike_migrated_click_blocked.py)
- **Profile / project:** `ci-probe` · `1e4efe0d-…` (migrated host, `flow.google.com`)
- **Cost:** $0 — navigation, DOM reads, one settings-pane click, Escape. No credits, no quota.
- **Raw:** `scripts/dev/_spike_out/spike_migrated_click_blocked_20260910_160220.json` (gitignored)

## The question

#776 dies 5.039 s after `migrated.editor_ready` with a bare Playwright `TimeoutError`.
By elimination that is `migrated_composer.py:884` — `await trigger.click(timeout=5000)`,
the only unguarded 5000 ms call in that window. Its sibling one line up
(`wait_for(state="visible")`, `:870`) *is* guarded, so the trigger was **visible** and the
**click** expired.

#593 measured a mechanism with exactly that shape: an announcement overlay sets
`body { pointer-events: none }`, leaving controls visible **and enabled** but unclickable
(`ui_automation.py:1210`). The guard it produced, `_require_unblocked`
(`ui_automation.py:1313`), is called 4× on the labs path and **0×** on the migrated one.

So: does that state occur on `flow.google.com`, and is `_dismiss_dialog` timed to miss it?

## Pre-registered readings

Written into the script's docstring before the run, so the result could not be respun:

| Outcome | Reading |
|---|---|
| body blocked ≥1/N | #593's mechanism reaches the migrated host; port the guard |
| trigger `hit_testable:false` ≥1/N | occlusion without a body block; the guard needs the hit-test too |
| replayed click expires | #776 reproduced locally |
| 0/N, click always lands | **does not reproduce here; settles nothing** |

## What was observed

3 independent navigations, 159 readable DOM samples at 250 ms.

| Run | trigger mounts | blocked | occluded | `wait_for(visible)` | click |
|---|---|---|---|---|---|
| 1 | 2194 ms | 0 | 0 | passed (64 ms) | **landed (130 ms)** |
| 2 | 3143 ms | 0 | 0 | passed (33 ms) | **landed (65 ms)** |
| 3 | 1479 ms | 0 | 0 | passed (39 ms) | **landed (70 ms)** |

`body_pointer_events` was `auto` in **159 of 159** samples. `occluded_by` was `null`
whenever the trigger existed. Zero `.cdk-overlay-pane`, zero `[role='dialog']` at any
point before the click; exactly **1** overlay after it — Flow's own settings pane, which
is the pane `_open_pane` is trying to open.

## Verdict on the overlay hypothesis: UNMEASURED

**0/3. This settles nothing about #776**, and per the pre-registration it is not evidence
of transience — it is equally consistent with `ci-probe` never having been served the
announcement (which [`2026-09-05-migrated-frames-attach.md`](2026-09-05-migrated-frames-attach.md)
already noted: that account had dismissed it in a prior session, so the migrated-host
changelog modal has *still* never been captured live).

The related rung-1 finding matters more than the 0/3: **#593's `pointer-events:none` was
measured on labs.google, not on the migrated host.** `migrated_composer.py:744`'s
description of the migrated dialog as "#593's twin" is **asserted, not measured** — it is
one of the few selector claims in that file with no dated spike behind it.

**What would settle it:** the block probe running on a profile that has *not* yet
dismissed a Flow announcement, i.e. a first visit after a Flow deployment. That is a state
you cannot summon on demand — which is precisely why the fix must not depend on knowing
which overlay it is.

## Three things this DID measure

### 1. `_dismiss_dialog` provably runs before the app exists — 3/3

At the instant `_dismiss_dialog` fires (`migrated_composer.py:595`, immediately after
`goto(wait_until="domcontentloaded")`), every run read:

```
trigger=0  dialog=0  overlay=0  ready_state=interactive
```

The composer mounted **1479–3143 ms later**. So the driver's one and only overlay check
looks at a page Angular has not rendered yet, in every run. It cannot see a dialog that
mounts with the app, and no amount of retrying that call site changes it — the miss is
structural, not flaky. This confirms with numbers what `migrated_composer.py:607` asserts
in prose about the SPA race, and extends it: the race applies to `_dismiss_dialog`, not
just to the agent-chip probe that comment is about.

### 2. A healthy click on this control costs 65–130 ms

Two orders of magnitude under the 5000 ms budget. So #776's expiry is not a slow click or
a loaded machine — the element never became actionable at all. The `wait_for(visible)`
that precedes it returned in 33–64 ms, which is why it is `:884` and not `:870`.

### 3. The locale-settle error is NOT sufficient to cause #776 — falsified

The reporter asked whether `account_locale_lang_unchanged … reason=Error` is relevant.
All three runs reproduced it:

```
client.account_locale_lang_unchanged  lang=en  reason=TimeoutError  waited_ms=4000.0
```

…and the click landed every time. A failed locale settle therefore does not, on its own,
produce this failure. It is real and separately tracked as **#643**; it is not #776's
cause. (Consistent with the locale-invariance rule: `READY_ANCHOR = ".settings-trigger-button"`
is structural, so no locale can hide it.)

## What this means for the fix

> **This result turned a `predict` GO into a STOP.** The proposal it was gating was
> "port #593's overlay guard to the migrated driver". Four of five personas returned
> GO/CAUTION on the mechanics; the Devil's Advocate returned **STOP on the premise**,
> having found [#752](https://github.com/ffroliva/gflow-cli/issues/752) finding #7 —
> which predicted #776's symptom at #776's function before it was filed, and whose cause
> (a mid-run agent-mode flip) touches neither `body{pointer-events}` nor the hit test. A
> guard built on the overlay would have reported the wrong cause with confidence. The
> spike above agreed from the other direction, and the fix was redesigned to **read**
> rather than diagnose.


The confirmed defect in #776 is **unattributability**, and that is independent of what
covers the trigger. A guard built only on `body{pointer-events:none}` would catch one of
Playwright's four actionability conditions (receives-events) and stay silent on the other
three — visible, stable, enabled. Building the fix *around the overlay* would be building
it around the one thing this spike could not measure.

The durable move is to make the failure name itself: report the locator, and whatever the
page can tell us about why the click did not land, at the moment it did not land.

## Related

- [`2026-09-05-migrated-frames-attach.md`](2026-09-05-migrated-frames-attach.md) — the account had already dismissed the changelog; a non-blocking "high demand" banner was present
- [`2026-09-05-migrated-host-wire-protocol.md`](2026-09-05-migrated-host-wire-protocol.md) — `goto` 8.1–11.4 s, settled 11.1–14.7 s; `cdk-overlay-container` absent until the first overlay opens
- [`2026-09-10-about-redirect-stability.md`](2026-09-10-about-redirect-stability.md) — the other 0/N "unmeasured" result this week, same discipline
