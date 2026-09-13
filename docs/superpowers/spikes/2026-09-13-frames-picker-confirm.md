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

## What is still NOT verified

The confirm click **fixing a stuck picker** cannot be observed here — this cohort's picker
closes on its own, so the new branch is never entered against live Flow. Per the Iron Law
that is a **named external blocker**: *a cohort Google has not put us in.* The branch is
covered offline (`test_attach_clicks_the_pickers_confirm_when_the_pick_does_not_commit`,
A/B-controlled: neutering the click turns it red) and the verifier is the #792 reporter.
Anything else would be a "recorded, not omitted" label standing in for a run.
