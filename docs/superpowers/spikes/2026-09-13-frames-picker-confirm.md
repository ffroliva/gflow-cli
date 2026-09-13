# Does the Frames picker need an explicit confirm? — a cohort split, measured

**Date:** 2026-09-13 · **Account:** the maintainer profile (`flow.google.com`, migrated)
· **Cost:** $0 — an upload and a pick; nothing was submitted. **Instrument:**
`scripts/dev/spike_frames_picker_confirm.py`.

## Why

[#792](https://github.com/ffroliva/gflow-cli/issues/792) reports that i2v dispatch fails
with `UiSelectorDriftError` on the migrated host, and the reporter root-caused it: Flow's
Frames picker **no longer closes when an asset is clicked** — it waits for its own "Add to
prompt" confirm. gflow waits `FRAME_COMMIT_HIDDEN_S` for an auto-close that never comes and
raises at `migrated_composer.py:1761`.

Their working delta clicks `button:has-text('Add to prompt')`. That is an **en-locale text
anchor**, which this module forbids outright (AGENTS.md § Locale-Invariance Discipline) —
and they said as much themselves. So the question was not *whether* to add a confirm click
but **what to anchor it on**, and a selector may not be guessed here.

## The complication: our cohort does not reproduce it

`tests/e2e/test_migrated_i2v_e2e.py::test_e2e_start_frame_uploads_and_binds_on_the_migrated_host`
passes unchanged on this account — **1 passed in 61.38s**, upload → pick → bind, no confirm
involved. So the reporter's failure is **not observable here**, and the naive conclusion
("can't see it, can't fix it") would have left the fix anchored on a guess.

## What was measured instead

Not *"does the picker require a confirm"* (it does not, here) but *"does the confirm
**exist** on this entry into the picker"*. Those are different questions, and the second one
is answerable on a cohort that never needs it.

The picker was opened from the **Frames** entry (empty Start chip), searched, and every
`<button>` inside `flow-add-menu-popover-content` dumped — **tag and class tokens only**,
never text, `aria-label`, `alt` or `src`, per the `_CLICK_POSTMORTEM_JS` rule (a signed-in
Flow page carries the account email in `aria-label`).

Three buttons. Two visible:

| classes (abridged) | visible | what it is |
|---|---|---|
| `header-close-btn` `flow-icon-button-transparent` … | ✅ | the picker's **close** |
| `detail-add-to-prompt-btn` `mat-tonal-button` `flow-button-secondary` … | ✅ | the **confirm** |
| `asset-item` `asset-item-active` | — | the listed asset |

**`button.detail-add-to-prompt-btn` is present, visible and enabled on this cohort too.**
It is simply never *needed*, because here the option click also commits. That is the same
class the r2v spike named on the `@`-mention entry into this component
([`2026-09-05-migrated-r2v-attach-surface.md:72`](2026-09-05-migrated-r2v-attach-surface.md)) —
so it is now measured on **both** entries, ten days apart.

## Two things this changes

1. **The confirm click is anchored on a measured class, not on translated copy.** The port
   of the reporter's delta uses `button.detail-add-to-prompt-btn`, which satisfies the
   locale rule and needed no new recon round-trip with them.

2. **A "click the picker button that is not an asset option" fallback would have clicked
   CLOSE.** That shortcut is the obvious structural guess when you cannot name the confirm,
   and this dump is the reason it is explicitly warned against at the `PICKER_CONFIRM`
   constant. Measuring cost one $0 run; guessing would have silently cancelled the pick and
   re-raised as the same selector drift it was meant to fix.

## Driving the branch anyway — what a $0 run could still settle

The first draft of this note claimed the whole confirm branch was unreachable here. That
was **over-scoped**, and the council caught it: *"listing something as unverified that you
could have verified is the same failure as omitting it, plus a false paper trail."*

Two different questions were hiding behind one blocker:

| Question | Reachable here? |
|---|---|
| Does a picker that **refuses to close** get rescued by the confirm? | **No** — needs the reporter's cohort |
| Does the branch gflow will run **resolve and behave safely** on the live DOM? | **Yes**, with one constant |

The second was forced by driving `FRAME_COMMIT_GRACE_S` to 1 ms, so the grace wait expires
on every run and the branch is always entered
(`test_e2e_the_pickers_confirm_is_clickable_on_the_real_frames_picker`, `e2e_auth`, $0).

**Result, 2026-09-13, against live Flow: PASSED, with `confirm_clicked=False`.** Even at a
1 ms grace the picker had already gone, `is_visible()` returned False, the click was
correctly **skipped**, and the chip bound normally.

That is the measurement the code's own safety claim needed. The comment says clicking the
confirm "is a no-op where the click already committed, because a closing picker no longer
carries it" — previously an argument, now an observation, and it retires the review's
concern that a slow auto-close could make this branch *worse* than the 15 s wait it
replaced. `is_visible()` rather than `count()` is what makes that true: a detached pane
still counts.

## What is still NOT verified

**The confirm click itself has never executed against live Flow.** Every arm available
here skips it, because no picker on this cohort stays open — which is exactly the state
the reporter has and we do not. Per the Iron Law that remainder is a **named external
blocker**: *a cohort Google has not put us in.*

It is covered offline across all three outcomes — clicked and committed, clicked and
ignored, and no confirm offered — and A/B-controlled: neutering the click turns
`test_attach_clicks_the_pickers_confirm_when_the_pick_does_not_commit` red. The verifier
is the #792 reporter. The **r2v** half of the naming fix is likewise offline-only: it
shares the upload leg the i2v e2e exercised live, but its own attach was not re-run.
