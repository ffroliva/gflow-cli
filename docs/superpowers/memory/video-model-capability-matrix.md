---
name: video-model-capability-matrix
description: "Flow's video duration control is COHORT-dependent: omni_flash renders 4/6/8/10s everywhere observed, while the Veo 3.1 models render 4/6/8s on some accounts and NONE on others. Root cause of #451/#288 'duration drift' (never Playwright, never locale). A static per-model duration table is unsafe in either direction. Ingredients are model-gated too."
---

**Verified live 2026-08-14, $0** (owner screenshots + spike
`scripts/dev/capture_video_model_capability_matrix.py`, profile `ffroliva`,
project `5ee3e625-…`; evidence doc
`docs/superpowers/spikes/2026-08-14-video-model-capability-matrix.md`, PR #534):

| Model | Duration tabs | Count | Credits | Ingredients |
|---|---|---|---|---|
| `omni_flash` | **4s/6s/8s/10s** | x1–x4 | 15 @10s, 7 @4s | accepted |
| `veo_3_1_lite` | **NONE** | x1–x4 | 10 | accepted |
| `veo_3_1_fast` | **NONE** | x1–x4 | 20 | accepted |
| `veo_3_1_quality` | **NONE** | x1–x4 | 100 | **REJECTED** |
| `veo_3_1_lite_lower_priority` | picker MISS — **absence, not a broken selector; settled 2026-09-05, see the end of this file** | | | |

**🎯 ROOT CAUSE of [[#451]]/#288:** `api/video.py:44` claims "the four `VEO_3_1_*`
models cap at 8s" — presuming they render a duration control. **They render none.**
`_select_video_duration` hunts a control the selected model never draws, so the
failure *looks* like `UiSelectorDriftError` exit 23. This is why the bug
reproduced identically on playwright **1.59 AND 1.61** (version bound correctly
exonerated) and why the **locale hypothesis was refuted** — it was never either.
`--duration` can only ever work on `omni_flash`. Do NOT re-derive this as drift.

**Ingredients are model-gated:** `Veo 3.1 - Quality` greys an attached ingredient
with "You cannot use image ingredients with this model."; Fast/Omni accept the
same asset. There is **no ingredient axis** on `VideoModel`, so
`r2v --model veo-quality --ref x.jpg` is an impossible combination that burns
selector timeouts instead of failing fast. (The ingredient cap is expressed
separately, via `reference_cap_for(model)` returning 0 for `VEO_3_1_QUALITY`.)

> **Corrected 2026-09-02.** This paragraph used to say "`VideoModel.supports_frames()`
> exists for i2v". No such method has ever existed on the enum, and after #626 the only
> capability predicate left is **`supports_duration()`** — `supports_i2v_end_frame()` was
> deleted when Flow shipped first+last for Omni 1.1 Flash and gflow replaced the static
> table with a post-submit route check (`_assert_i2v_route`). **Every model now does i2v
> with a start frame AND an end frame**; duration remains omni-flash-only, which is what
> the rest of this file is about. See [[rtk-grep-quote-false-negative]] for why a grep
> "confirming" a symbol's absence was not trustworthy on this machine before that date.

**Two new DOM affordances, unread by the transport today:**
- **Live credit cost** — `Generating will use N credits`, scaling with model AND
  duration. This is the missing input for the deferred tier-aware credit
  confirmations.
- **Dynamic composer chip** — raw textContent `Video · 10scrop_9_16x1` =
  duration + aspect **icon ligature** + count. Good read-back affordance, but any
  matcher MUST strip Material Symbols ligatures ([[flow-locale-leak-icon-ligatures]]).

**✅ CONFIRMED on a 2nd account + 2nd locale (`denon82`, pt-BR, PR #535):** matrix
IDENTICAL. Credits match exactly for lite/fast/quality (10/20/100); omni differs
only by selected duration (7 @4s, 12 @8s, 15 @10s) → **pricing is
duration-scaled**. `veo_3_1_lite_lower_priority` missed its picker on **BOTH**
accounts → ~~that `:not()` selector is genuinely broken, not cohort-specific~~.
**FALSIFIED 2026-09-05: the selector was always right and the entry was simply not
served to these accounts.** Two accounts agreeing on an absence is not evidence the
selector is broken — see the end of this file.
The one-cohort caveat looked retired here. **It was not — see the 2026-09-04
re-opening at the end of this file.** Two accounts that agree are evidence of a
shared cohort, not of universality.

**🌍 pt-BR re-confirms [[flow-locale-leak-icon-ligatures]] (#56) live:** video tabs
render `crop_freeFrames`, `chrome_extensionElementos` (Ingredients→"Elementos"),
`crop_9_169:16`. Text localizes, the ligature does NOT; ligature-keyed selection
worked unchanged in both locales. Chip = `Vídeo · 8scrop_16_9x1` → a chip matcher
must strip ligatures AND not assume the English word.

**Spike gotchas worth reusing:**
- **The composer can sit in IMAGE mode** — then the popover shows FIVE aspect tabs
  (16:9/4:3/1:1/3:4/9:16) and **no video model picker**, so every model lookup
  misses. A pt-BR run reported 5/5 "picker misses" for exactly this reason. Select
  the Video tab first (ligature `videocam`); production does it via
  `switch_to_video_mode`. **A "model picker missing" report may be a MODE problem,
  not drift.** Video has only 2 aspects; image has 5 — a fast way to tell which
  popover you are looking at.
- An English-only `/Generating will use N credits/` regex returns null on pt-BR
  and reads as "no cost shown" — a silent wrong answer. Match a number next to a
  `cr[ée]dito?s?|credits?` stem.
- The `crop_*` settings trigger is a **TOGGLE** — clicking it while the popover is
  open closes it. Probe `[role='menu']` first; clicking unconditionally produced
  empty reads alternating with model-picker misses (the picker lives *inside* the
  popover).
- The account's `isAgentModeToggled` persists **server-side**, so the editor can
  load agentic on a profile that was classic yesterday. Call
  `mode_control.ensure_media_mode(page, allow_reload=True)` first — see
  [[flow-agent-settings-panel-sticky-defaults]] (the Agent settings panel now
  carries BOTH image and video defaults plus a Confirm-before-generating radio).
- `scripts/dev/` is **not** linted by CI (`ruff check src tests` only) — 54
  pre-existing errors live there; keep your own file clean regardless.

---

**🔁 RE-CONFIRMED 2026-09-03, $0 — matrix UNCHANGED (PR #650 challenge, REJECTED).**
External PR #650 asserted "Flow now renders 4s/6s/8s for all Veo 3.1 models" and
flipped `supports_duration()` to a constant `True` on that claim alone. Re-ran the
spike (`denon82`, pt-BR, project `2ddc3a33-…`, capture
`video_model_capability_matrix_20260903_225459.json`):

| Model | Duration tabs | Count | Credits |
|---|---|---|---|
| `omni_flash` | **4s/6s/8s/10s** | x1–x4 | 12 @8s |
| `veo_3_1_lite` | **NONE** | x1–x4 | 10 |
| `veo_3_1_fast` | **NONE** | x1–x4 | 20 |
| `veo_3_1_quality` | **NONE** | x1–x4 | 100 |
| `veo_3_1_lite_lower_priority` | **PICKER MISS** — absence on this cohort; the tier IS rendered to a throttled account (2026-09-05, end of file) | — | — |

Identical to 2026-08-14 **and** to the PR #535 pt-BR run, credits included. Two
independent DOM signals agree, which is why `NONE` is a real absence and not a
missed selector: (a) the `x1`–`x4` count tabs came back on every model, proving
the popover was open and parsed; (b) the composer chip carries the duration
segment ONLY for omni (`Vídeo · 720p · 8scrop_9_16x1` vs `Vídeo · 720pcrop_9_16x1`).
**Use both as the read-validity check in any future capability recon** — a bare
`duration=NONE` with no count tabs means the read failed, not that the row is gone.

---

**RE-OPENED 2026-09-04 — the "REJECTED" verdict above is WITHDRAWN. Duration is
COHORT-DEPENDENT.** The #650 reporter ran the same credit-free collector on a third
profile (the reporter's own, not a maintainer account) on **`labs.google`**
(`page_lang=ru`) and got `4s/6s/8s` on `veo_3_1_lite`, `veo_3_1_fast` **and**
`veo_3_1_quality`; `x1-x4` on all four; `veo_3_1_lite_lower_priority` picker MISS.

It **passes this file's own read-validity check** — count tabs came back on every
model, and the composer chip read `Video - 720p - 4s...` with a Veo model selected.
So `NONE` on the maintainer cohort and `4s/6s/8s` on the reporter's are
**both real**. Credits differ too (5/10/100 vs 10/20/100; that cohort's omni adds
`360p/720p` tabs and bills 7), a second independent signal of a distinct cohort.

**The migrated-frontend hypothesis is REFUTED** — the positive capture is on
`labs.google`, the same frontend gflow drives. Naming the host was necessary but not
sufficient; name the **profile** too.

**Instrument check, done:** PR #650 also widens the collector's scrape from
`[role='tab']` to `button, [role='tab'], [role='button'], [role='option'],
[role='menuitem']`, so the two matrices came from different script versions. That is
**not** the explanation: the reporter's own JSON pins the elements as
`radix-:r1m:-trigger-4/6/8`, Radix tab triggers carrying `role="tab"`, which the base
selector demonstrably reads (it found omni's row on `denon82`). The widening is a
fidelity fix — the transport's cascade already probed all five roles.

**What is settled:** the 2026-08-14 and 2026-09-03 captures remain VALID for the
`ffroliva`/`denon82` cohort — `--duration` genuinely cannot work there. The ligature
rule held again under `ru`.

**What is NOT settled:** the cohort key (region? account age? experiment bucket?),
and whether `10s` ever appears on Veo. ~~Whether `lower_priority`'s miss is selector or
absence~~ — **settled 2026-09-05: absence.** See the end of this file. See [[flow-capabilities-are-cohort-dependent]] and
[[flow-recon-must-run-on-denon82-ffroliva-migrated]].

**Migrated host (flow.google.com), ffroliva cohort, measured 2026-09-05 (`scripts/dev/spike_migrated_duration_by_model.py`, $0):** Omni 1.1 Flash renders duration `4s/6s/8s/10s` AND resolution `360p/720p`, 12 credits; Veo 3.1 Lite / Fast / Quality render NO duration row and NO resolution row, 10 / 20 / 100 credits. So on this cohort #650's `--duration` on Veo has no control on the new host either → the migrated composer aborts pre-submit with exit 11 (ConfigurationError naming the axis), $0. The positive Veo 4/6/8 path remains cohort-external (contributor accounts).


---

**`veo_3_1_lite_lower_priority` — SETTLED 2026-09-05: the miss was ABSENCE, not a
broken selector** (`scripts/dev/capture_migrated_model_menu.py`, $0, migrated host).

Every prior capture recorded a picker MISS and this file twice concluded the `:not()`
selector was broken. It never was. A migrated account renders the entry verbatim:

```
volume_upOmni 1.1 Flash
volume_upVeo 3.1 - Lite
volume_upVeo 3.1 - Fast
volume_upVeo 3.1 - Quality
volume_upVeo 3.1 - Lite [Lower Priority]
```

and its picker button was **already defaulted to that tier**. That is the likely cohort
key for this row specifically: Flow serves the entry to accounts it is **throttling**,
and every earlier capture was taken on accounts it was not. The label is now known —
`Veo 3.1 - Lite [Lower Priority]` — but both drivers still match on the `[Lower Priority]`
tag, which survives Flow moving the throttle to a different tier.

All five tiers were driven and read back on that account (`_select_model`, then the
picker button re-read): every one selected, and plain `Veo 3.1 - Lite` did **not** bind
the throttled sibling.

**The read-validity check in this file did not cover this.** Count tabs and the composer
chip prove the *popover* was read; neither says anything about a *menu entry that the
account is not served*. For a picker row, "MISS on two accounts" means absence on that
cohort and nothing more — do not promote it to "the selector is broken" again.

Duration/credits for this tier remain **unmeasured**: it was selected at $0 and never
generated on.

Capture: `docs/superpowers/spikes/2026-09-05-migrated-model-menu-lower-priority.md`.
See [[flow-capabilities-are-cohort-dependent]].
