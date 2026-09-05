# The migrated model menu, and the first capture of `[Lower Priority]`

**Date:** 2026-09-05 · **Profile:** `kittinansr2` (an account Google has already moved to
`flow.google.com`) · **Project:** `66324c59-6e68-4988-a91b-26896c9ebd0b` ·
**Cost:** one 8 s Veo generation (unintended, see below); every measurement below is $0.

**Instrument:** `scripts/dev/capture_migrated_model_menu.py` (`--drive` for the matrix).

## Why

`veo_3_1_lite_lower_priority` was the one tier `VIDEO_MODEL_MENU_LABELS` omitted, so an
account already on the migrated host could not select it at all — `_select_model` refused
with `ConfigurationError` before reading the live menu. The omission was not arbitrary:
the map is keyed by product label, and no capture had ever rendered that entry. The
2026-08-14 two-account capability matrix, #650's duration capture and v0.61.0's refusal
A/B all recorded a **picker MISS**, and v0.62.1's live menu read back four entries.

## What the menu actually holds

```
volume_upOmni 1.1 Flash
volume_upVeo 3.1 - Lite
volume_upVeo 3.1 - Fast
volume_upVeo 3.1 - Quality
volume_upVeo 3.1 - Lite [Lower Priority]      <-- never captured before
```

The picker button read `Veo 3.1 - Lite [Lower Priority] arrow_drop_down` — **the account
was already defaulted to the throttled tier.** That is the most likely reason every
earlier capture missed the entry: they were taken on accounts Flow was not throttling,
which are not served it.

The label is now known. Matching deliberately stays on the `[Lower Priority]` **tag**
anyway: this is one account's rendering, the labs driver keys off the same tag, and a tag
Flow appends to whichever tier it is throttling survives that tier changing.

## The matrix — `_select_model` driven for all five tiers, $0

Selection happens entirely before submit, which is what made v0.61.0's and v0.62.1's
refusal matrices free. `button_after` is read back from the pane after the switch — the
only evidence that separates "clicked the entry the user asked for" from "returned
success".

| requested | outcome | button read back after |
|---|---|---|
| `omni_flash` | selected | `Omni 1.1 Flash` |
| `veo_3_1_lite` | selected | `Veo 3.1 - Lite` |
| `veo_3_1_fast` | selected | `Veo 3.1 - Fast` |
| `veo_3_1_quality` | selected | `Veo 3.1 - Quality` |
| `veo_3_1_lite_lower_priority` | selected | `Veo 3.1 - Lite [Lower Priority]` |

Both directions of the exclusion are exercised: `veo_3_1_lite` lands on the plain entry
with the lower-priority sibling present in the same menu, and the lower-priority tier is
reached through the menu (the preceding row left the picker on Quality).

## The defect this exposed

The port matched entries by case-insensitive **substring** and took `.first`, and ran the
same substring against the button read-back. With this account's real button text:

```python
"Veo 3.1 - Lite [Lower Priority] arrow_drop_down".lower().startswith("veo 3.1 - lite")
# -> True
```

So pre-fix, `--model veo-lite` on this account returned "already selected" **without
opening the menu** and generated on the throttled tier. This is the ambiguity #539 fixed
on labs.google, whose selectors carry `:not(:has-text('[Lower Priority]'))`; the migrated
port had dropped it. Ordinary tiers now exclude the tag on both paths.

## Observability gap, closed

The 2026-09-05 t2v run bound the tier correctly but emitted **no model event at all** —
`_select_model` returned at the button read-back, and the whole settings pass took 151 ms.
"Bound the tier you asked for" and "never touched the picker" were indistinguishable in
the timeline, and answering it needed this separate probe. The short-circuit now logs
`migrated.model_already_selected` with the observed button text.

## Recorded, not asserted

- **One generation was spent to learn this**, and it should not have been. The preceding
  incident bundle showed `generation_requests: []` (a `TimeoutError` in the settings
  phase), so a repro was expected to cost nothing; the timeout did not recur and the run
  went all the way through submit. The $0 probe above is what the question actually
  needed, and it is what should have been reached for first.
- **One account, one locale (`en`).** Whether Flow serves this entry to accounts it is
  not throttling is untested, and every earlier capture suggests it does not.
- `_open_pane` **cannot be called twice on one page load**: re-opening the settings pane
  leaves a detached overlay that still contains radiogroups, and `.last` then resolves to
  it and reports "0 option groups" (reproduced twice while building the probe, which now
  loads a fresh document per tier). Production opens the pane once per run, so no shipped
  path is known to hit this. Not filed as a defect — recorded so the next probe does not
  rediscover it.
