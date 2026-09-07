# The carrier is one character. The ligature NAME is the other half nobody counted

**Date:** 2026-09-07 · **Issue:** [#730](https://github.com/ffroliva/gflow-cli/issues/730) ·
**Account:** `denon82` (migrated `flow.google.com`) · **Cost:** `$0` — navigation, one free
`createEntity` (deleted again), DOM reads. Nothing submitted, no credit, no image quota.

**Script:** `scripts/dev/spike_ligature_carrier_sweep.py` ·
**Capture:** `scripts/dev/_spike_out/ligature_carrier_sweep_denon82_20260907_*.json` (gitignored)

---

## 1. What was asked

#727 and #731 both came from the labs/migrated **carrier split**: the same Material Symbols
ligature rendered in `<i class="google-symbols">` on labs and `<mat-icon>` on the migrated
host. A static sweep of `src/gflow_cli` found the real unit is the *constant*, not the literal:

| | count |
|---|---|
| constants covering **both** carriers | 6 |
| constants that are **`<i>`-only** | **24** |

(#730 says "55 single-carrier literals". That number counts literals, and double-counts
cascades that alternate carriers across entries. 24 constants is the actionable figure.)

Static analysis cannot say which of the 24 *matter*. A selector whose surface does not exist
on this host is not a bug; one whose miss is rescued by a fallback is a bug that is currently
invisible. So the probe measured, per ligature, on two live surfaces.

## 2. The carrier result — unambiguous

Control: `prompt_boxes=1`, `ligature_hits=8`. The probe was standing on a real editor, so its
zeros are about selectors, not arrival.

```
=== character editor ===
ligature                i.gs   mat   .gs   any  carriers
accessibility_new          0     1     1     1  mat-icon
add                        0     2     2     2  mat-icon
arrow_drop_down            0     1     1     1  mat-icon
arrow_forward              0     1     1     1  mat-icon
close                      0     1     1     1  mat-icon
upload                     0     1     1     1  mat-icon
voice_selection            0     1     1     1  mat-icon
```

**`i.google-symbols` matched zero, for every ligature, on both surfaces.** `mat-icon` matched,
and **`.google-symbols` matched exactly the same count as `mat-icon`, every time**.

The class is on both carriers. `.google-symbols:text-is(L)` therefore covers labs *and*
migrated with no cascade entry at all — the fix for the 24 constants is **one character each**
(`i.google-symbols` → `.google-symbols`), not 24 duplicated entries.

Two selectors are confirmed broken-but-masked:

- `SUBMIT_BUTTON_SELECTORS` — `arrow_forward` is `i.gs=0 / mat=1`. Both `<i>` entries miss;
  only the third, `button:has-text('arrow_forward')`, fires, matching the `<mat-icon>`'s *text*
  rather than its tag. Independently observed live the same day:
  `prompt_submitted via="button:has-text('arrow_forward')"`. **It works by luck.**
- `IMAGE_MODEL_PICKER_TRIGGER` — `arrow_drop_down` is `i.gs=0 / mat=1`, and the constant has no
  twin and no text fallback.

## 3. The finding that a carrier-only fix would have shipped past

**The migrated host renders `add`. It does not render `add_2` anywhere.**

```
composer:  add = 1     add_2 = 0
editor:    add = 2     add_2 = 0
```

That is a **ligature NAME change, not a carrier change.** Consequences:

- `NEW_PROJECT_SELECTORS` (`add_2`) misses — which is why the first run of this very spike died
  with `Could not find 'New project' CTA on Flow gallery` on `flow.google.com`.
- `_CHARACTER_SLOT_ADD_SELECTOR` misses **even though it already covers both carriers**. It was
  swept for the carrier in #703 and is still broken on this host.
- `ADD_MEDIA_BUTTON` (`add_2`) is in the same position.

Changing the carrier on those three would have produced a green sweep, a satisfied checklist,
and three selectors that still match nothing. **The carrier was never the whole story; it was
just the half that had a name.**

## 4. What was NOT measured

- **Ligatures absent from both surfaces are NOT proven absent from the product**:
  `add_2`, `cancel`, `clear`, `edit_square`, `image`, `play_circle`, `tune`, `article_spark`,
  `chrome_extension`, `crop_free` and most `crop_*` did not appear. They plausibly live behind
  menus, dialogs or modes this probe never opened. The probe read two surfaces in their default
  state, and that is all it can speak to. `add_2` is the exception only because a control that
  *should* be on the surface it was probed on (`New project`, `slot add`) was independently
  observed to fail.
- **labs was not measured at all.** No unmoved account exists here. The claim that
  `.google-symbols` also matches `<i class="google-symbols">` is CSS semantics, not an
  observation — it should be verified on labs before anyone calls this closed.
- **Whether broadening `i.google-symbols` → `.google-symbols` changes `.first` picks on labs.**
  A wider match can select a different element where a cascade relies on document order.

## 5. The rule this earns

```
SWEEPING FOR THE FAILURE YOU ALREADY NAMED FINDS ONLY THAT FAILURE.
```

The probe was built to compare two carriers. It found the name drift only because it also
counted whether the ligature was present *at all* — the control column, added so a zero could
not be misread as absence. That column, added for epistemic hygiene, is what turned up the
second defect class.

See also:
[2026-09-07 — the Format button's anchor](2026-09-07-character-format-button-anchor.md),
[2026-09-07 — a click is not an effect](2026-09-07-format-click-is-not-a-format.md),
[`skills/spike/SKILL.md`](../../../skills/spike/SKILL.md).
