# Live verification — v0.61.0

Every claim below was **observed**. Claims that could not be observed are stated
as such in §6, not omitted.

## Environment

| | |
|---|---|
| Date | 2026-08-26 / 2026-08-27 |
| Profile | `denon82` (account locale **pt**, `html_lang=pt`) |
| Project | `2ddc3a33-97db-41a0-a0d3-7f9488b0d5a9` |
| UI arm | classic (cohort flaps per page load — see §6) |
| CLI | 0.60.0 tree + the five fixes in this release |
| Credits spent | **one** t2v generation (`omni-flash`). Everything else was credit-free. |

## Summary

| # | change | verified live | how |
|---|---|---|---|
| 1 | Video model refusal (#539) | ✅ | zero-credit A/B + two CLI runs |
| 2 | Image model refusal (#586) | ✅ | CLI, exit 23, 0 files written |
| 3 | Server-side attribution (#586) | ✅ | real agentic mis-attribution detected |
| 4 | Locale navigation (#581) | ✅ | pt account + control arm |
| 5 | Navigation settle audit (#584) | ⚠️ partial | 1 of 4 sites exercised — see §5 |
| 6 | Canary re-exec (#582) | ⚠️ not yet | unit-tested; first real exercise is tonight |

---

## 1. Video model refusal (#539) — the headline change

Flow's video picker, read **while the menu was open**:

```
volume_up Omni Flash
volume_up Veo 3.1 - Lite
volume_up Veo 3.1 - Fast
volume_up Veo 3.1 - Quality
```

`Veo 3.1 - Lite [Lower Priority]` is **not offered to this account at this
moment**. That makes `--model veo-lite-lp` a real MISS to test, not a synthetic one.

### 1a. Zero-credit A/B against the pre-fix code

Model selection happens in the settings panel entirely **before** submit, so both
arms cost nothing. The "old code" column is a real run of the stashed pre-fix
source against live Flow — not a reading of the diff.

| case | old code | new code |
|---|---|---|
| `omni_flash` | SELECTED | **SELECTED** |
| `veo_3_1_lite` | SELECTED | **SELECTED** |
| `veo_3_1_fast` | SELECTED | **SELECTED** |
| `veo_3_1_quality` | SELECTED | **SELECTED** |
| `veo-lite-lp` (not offered) | **SELECTED** — returned success | **REFUSED** |
| ambiguous selector, 3 matches | **SELECTED** — `.first` guess | **REFUSED** |

Harness: `scripts/dev/live_verify_video_model_select.py` (6/6 as expected).

The ambiguous case was produced by injecting `has-text('Veo 3.1')`, which matches
Lite + Fast + Quality. The refusal message reported **3 entries match**, i.e. the
real live count, not a fixture's.

### 1b. CLI, end to end

```
gflow video t2v ... --model veo-lite-lp   → EXIT 18, nothing submitted
```

The RFC 9457 detail names all four models Flow offers and states
`No credits were spent.`

```
gflow video t2v ... --model omni-flash    → EXIT 0
```

- `ui_automation_video.model_selected` with `"model": "omni_flash"`
- `poll_terminal` → `MEDIA_GENERATION_STATUS_SUCCESSFUL`
- `f41810ea-d2e3-40fe-b834-3eaaaa351a1f.mp4`, **2 250 862 bytes**
- catalog row records `model: "omni_flash"`

The guard does not break the working path.

---

## 2. Image model refusal (#586)

`--model imagen4` (an entry Flow has removed) → **EXIT 23**, **0 jpgs written**,
message naming what Flow does offer. An ambiguous selector → **EXIT 23**,
`AMBIGUOUS — 2 entries match`. A disambiguated `NARWHAL` selects and generates.

Batch containment: with prompt 0 refused and prompt 1 valid, the run reported
`1/2 succeeded` and **EXIT 0** — one drifted selector does not abort a batch.

## 3. Server-side attribution (#586)

An agentic run that requested `GEM_PIX_2` was attributed **`NARWHAL`** by
`flow.projectInitialData`, with a real seed where the catalog held the `0`
sentinel. `scripts/dev/verify_model_attribution.py` exits **1** on that real
mismatch. Free, cookie-authenticated, arm-agnostic.

## 4. Locale navigation (#581)

Today's runs resolved `client.account_locale_resolved locale=pt` and navigated
`https://labs.google/fx/pt/tools/flow/project/...`. `url_stable_after_goto`
reported `settle_skipped=false` on the redirecting account and `true` on a
non-redirecting one, so `en` accounts pay no per-navigation wait.

Previously verified across **pt / de / ja / fr / ru** with a control arm showing
the pre-fix path still racing.

## 5. Navigation settle audit (#584) — partial, stated deliberately

Four `page.goto` sites gained a settle. Live runs this cycle exercised
**`_enter_editor`** only (`ui_automation.url_stable_after_goto`, repeatedly).

**Not exercised live this cycle:** `evaluate_fetch.refresh_auth`,
`evaluate_fetch` setup, and the `sapisidhash` fingerprint capture — none of the
runs above needed a bearer refresh. They are covered by unit tests and by the
AST ratchet that pins every `goto` to a following settle, which is a weaker
guarantee than observation and is recorded here as such.

## 6. Canary re-exec (#582) — not yet observed

The runner re-runs itself once when a successful `--pull` changed its own source.
Covered by `tests/scripts/test_canary_reexec.py`. Its first **real** exercise is
the nightly run from the dedicated canary clone; nothing in this cycle triggered
a self-changing pull, so the behaviour is **unverified in production** and is
called out rather than assumed.

---

## Also observed: the cohort race is real

Two byte-identical `--ui-mode classic` invocations produced different arms
seconds apart — one bound classic and refused correctly (exit 23), the other
failed earlier at `mode_control.ensure_media_incomplete` with **exit 31**
(`FlowAppError`) before model selection ran. This bounds what any pre-submit
guard can promise and is recorded so it is not mistaken for a regression.

## Falsified this cycle

The note carried in #539 that *"the video picker uses a different trigger"* is
**wrong**: `MODEL_PICKER_TRIGGER` and `IMAGE_MODEL_PICKER_TRIGGER` are
byte-identical strings. The repeated empty menu captures were caused by the
**capture** — `_switch_to_video_mode` leaves the settings menu open by contract,
and calling `_open_gen_settings_panel` after it re-clicks the same button and
toggles the panel shut. A DOM dump settled it in one run:
`id='radix-:r1q:-trigger-VIDEO'` was present and visible throughout.

## 5-layer ledger (the paid video generation)

| layer | evidence |
|---|---|
| file count | 1 mp4 written |
| magic bytes | `00 00 00 20 66 74 79 70 69 73 6f 6d` — `ftypisom`, real ISO-BMFF |
| size | 2 250 862 bytes (not a truncated error page) |
| structlog invariants | `model_selected model=omni_flash` → `generate_captured status=200` → `poll_terminal SUCCESSFUL` → `video_saved` |
| user-confirmable artifact | `tmp/f41810ea-d2e3-40fe-b834-3eaaaa351a1f.mp4` |

## Cleanup owed

- Ad-hoc media in project `2ddc3a33` from the runs above (1 video, several images).
