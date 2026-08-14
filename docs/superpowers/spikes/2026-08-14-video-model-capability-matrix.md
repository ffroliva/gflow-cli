# Spike evidence — per-model capability matrix in the classic video composer

**Date:** 2026-08-14 · **Verdict: ROOT CAUSE FOUND for #451 / #288 — the duration
control is MODEL-CONDITIONAL, not "dropped by Flow".** `api/video.py:44`'s claim
that "the four `VEO_3_1_*` models cap at 8s" is wrong: they expose **no duration
control at all**. Only `Omni Flash` renders a duration row.

## Method

Zero credits — navigation + settings-popover reads only. Never typed a prompt,
never clicked Generate. Selecting a model in the picker does not bill.

`scripts/dev/capture_video_model_capability_matrix.py --profile ffroliva
--project 5ee3e625-…`: opened the real editor, restored the classic composer
(`mode_control.ensure_media_mode`, since the account's persisted
`isAgentModeToggled` can serve the agentic arm), then for **each** registered
`VideoModel` selected it in the picker and read the open popover's
`[role='tab']` inventory, the live credit line, and the composer chip.

Raw capture: `scripts/dev/_spike_out/video_model_capability_matrix_*.json`
(gitignored). Owner screenshots of the same states are in issue #451.

## Result

| Model | Duration tabs | Count tabs | Credits | Ingredients |
|---|---|---|---|---|
| `omni_flash` | **`4s` `6s` `8s` `10s`** | `x1`–`x4` | 15 (@10s), 7 (@4s) | accepted |
| `veo_3_1_lite` | **none** | `x1`–`x4` | 10 | accepted |
| `veo_3_1_fast` | **none** | `x1`–`x4` | 20 | accepted |
| `veo_3_1_quality` | **none** | `x1`–`x4` | 100 | **REJECTED** |
| `veo_3_1_lite_lower_priority` | picker miss | — | — | — |

Ingredient column is from owner recon (an attached ingredient is required to
observe it; the automated run had none attached). `Veo 3.1 - Quality` greys the
ingredient thumbnail with **"You cannot use image ingredients with this model."**
while `Veo 3.1 - Fast` and `Omni Flash` accept the same asset.

## Interpretation

1. **#451 / #288 are not selector drift and never were a Playwright regression.**
   The prior A/B (`playwright` 1.59 vs 1.61) found the duration tabs missing on
   BOTH, and the locale hypothesis was refuted — because the tabs were never
   there for the model under test. `_select_video_duration` hunts a control the
   Veo 3.1 models do not render, so `--duration` can only ever work on
   `omni_flash`. The resulting exit 23 reports "UI drift" for what is really a
   **model capability mismatch**.
2. **`VideoModel` is missing an ingredient-capability axis.** `supports_frames()`
   exists for i2v, but nothing models "accepts image ingredients", so
   `gflow video r2v --model veo-quality --ref x.jpg` drives an impossible
   combination and burns selector timeouts instead of failing fast at the DTO.
3. **Credit cost is readable in the DOM** (`Generating will use N credits`) and
   varies by model **and duration** (omni: 7 @4s → 15 @10s). Nothing in the
   transport reads it today. This is the missing input for the deferred
   tier-aware credit-confirmation work.
4. **A dynamic composer chip now summarises the selection.** Captured raw
   textContent: `Video · 10scrop_9_16x1` — i.e. duration + aspect **icon
   ligature** + count. It is a genuine read-back affordance (verify what is
   actually selected), but any matcher must strip Material Symbols ligatures —
   the usual locale-leak trap.
5. `veo_3_1_lite_lower_priority` did not match its picker selector in this run;
   its `:not()` exclusion form needs re-deriving. Recorded, not fixed here.

## Recommended follow-ups (none applied in this spike)

- Correct `api/video.py:44`'s docstring and add a `supports_duration()` capability
  (true only for `OMNI_FLASH` on current evidence), then fail `--duration` fast at
  the DTO for models without the control, with a message naming the model — not
  a drift error.
- Add an ingredient-capability predicate and reject `r2v` + `veo_3_1_quality`
  pre-submit.
- Re-derive the `veo_3_1_lite_lower_priority` picker selector — it missed on
  **both** accounts, so its `:not()` exclusion form is genuinely broken rather
  than cohort-specific.

## Second-account confirmation (`denon82`, pt-BR) — 2026-08-14

Re-ran on a **different account in a different locale**. The matrix is
**identical**, which removes the one-cohort caveat for the duration finding:

| Model | Duration tabs | Credits (denon82) | Credits (ffroliva) |
|---|---|---|---|
| `omni_flash` | **`4s` `6s` `8s` `10s`** | 12 @8s | 15 @10s |
| `veo_3_1_lite` | **none** | 10 | 10 |
| `veo_3_1_fast` | **none** | 20 | 20 |
| `veo_3_1_quality` | **none** | 100 | 100 |
| `veo_3_1_lite_lower_priority` | picker MISS | — | picker MISS |

Credits match exactly for the three Veo models; `omni_flash` differs only
because the selected duration differs (12 @8s vs 15 @10s), which independently
confirms **duration-scaled pricing**.

### Locale findings (pt-BR) — the ligature rule holds

- Video-mode tabs read `crop_freeFrames`, `chrome_extensionElementos`
  ("Ingredients" → "Elementos"), `crop_9_169:16`, `crop_16_916:9`.
  **Text is localized; the Material Symbols ligature is not.** Selecting by
  ligature worked unchanged across both locales — a live re-confirmation of
  [[flow-locale-leak-icon-ligatures]] (#56).
- The composer chip reads `Vídeo · 8scrop_16_9x1` — localized word, embedded
  ligature. Any chip matcher must strip ligatures AND not assume "Video".
- The credit line is localized too; an English-only
  `/Generating will use N credits/` regex returns `null` on pt-BR and reads as
  "no cost shown". Match a number adjacent to a `cr[ée]dito?s?|credits?` stem.

### Spike bug this exposed (worth keeping)

The composer can be in **Image** mode, where the popover shows five aspect tabs
and **no video model picker** — every model lookup then misses. The first
denon82 run reported 5/5 picker misses for exactly this reason. Selecting the
Video tab first (by the `videocam` ligature) fixed it. Production does this via
`switch_to_video_mode`; any recon script must too.
