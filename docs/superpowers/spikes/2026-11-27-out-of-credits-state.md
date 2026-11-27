# Spike: what Flow shows an account that cannot afford a video, and where gflow says so

**Date:** 2026-11-27 · **Host served:** `flow.google.com` (UI rendered in Portuguese) · **Profile:** the
account B of [the direct-video spike](2026-11-27-direct-video-and-headless-mint.md), holding **30
credits** · **Script:** [`scripts/dev/spike_out_of_credits.py`](../../../scripts/dev/spike_out_of_credits.py)
· **Cost:** $0 (balance 30 before, 30 after; one image of daily quota; two empty projects created)
· **Raw evidence:** `scripts/dev/_spike_out/spike_out_of_credits_*` and
`scripts/dev/_spike_out/out_of_credits/` (gitignored)

## Question

When an account cannot pay for a video, what does Flow show (a top-up modal, an out-of-credits
state)? What does gflow do on each path today? Where should `InsufficientCreditsError` (exit 37)
be raised, including a browser-free pre-check? A run earlier the same day on this profile failed at
project creation with exit 23 ("the new-project button … did not take the click").

## The premise did not hold, and what was measured instead

The profile was believed to hold 0 credits. `gflow credits user --json` read **30** (exit 0,
2.5 s, the browser-free `nzlxg` read), consistent with the 50 → 30 recorded for account B by the
direct-video spike. An empty balance was therefore not observable. A billed submit was not sent to
make it so.

The per-model shortfall was measured instead. `veo-quality` is quoted at 100 credits, so at a
balance of 30 it is the same state for that model as an empty balance is for every model.

## Method

`spike_out_of_credits.py` drives the production helpers (`create_project`, `_dismiss_dialog`,
`_click_create`, `apply_video_settings`, `send_prompt`) in-process. It ran once headed and once
headless. A context route ABORTS every billed RPC (`YhhmEf`, `eb1hJf`, `MZZa6b`, `nprQif`,
`fZytfe`, `ogiZ0b`) and any aisandbox generation POST. Nothing was aborted, because nothing was
attempted. Each phase records overlays, the `elementFromPoint` hit over the new-project button,
the ligature inventory, `button.prompt-warning-button` and every `arrow_forward` button, plus a
screenshot. The real CLI commands then ran one at a time.

Before running any `gflow video` command against the live account, the spike checked that
`arrow_forward` is absent for `veo-quality` at this balance (2/2). The video path waits only for
that button (`_mc_submit_observe.py:641-658`) and has no Enter fallback. So with the button gone,
the CLI has nothing to click.

## Observations

### Landing: a promo dialog covers the projects page, and it is not an out-of-credits modal

| Phase | Overlays | New-project button receives events | Top element at its centre |
|---|---|---|---|
| landing (8 s after `domcontentloaded`) | 3 | no | `div.cdk-overlay-backdrop.g1-promo-dialog-backdrop` |
| after `_dismiss_dialog` | 0 | yes | the button's own `span` |
| production `create_project` (navigates to `/` again) | 4 | no | `div.g1-promo-dialog-backdrop` |
| wait for `[role=dialog]`, `_dismiss_dialog`, then click | 0 | yes | button `span` → project created |

The same result came out headed and headless, 2/2 each. The dialog is Flow's "redeem 20 bonus
credits" streak reward (screenshot `spike_out_of_credits_headed_landing_20261127_224512.png`).
Its structure:

- `div.cdk-overlay-pane.flow-g1-promo-dialog-panel` > `mat-dialog-container[role=dialog]` >
  `flow-g1-promo-dialog` (custom element, the best anchor)
- `button.close-button` with ligature `close` (so `DIALOG_CLOSE` matches it), `button.no-thanks-button`,
  `button.claim-button`, `a.dialog-terms-link[href=flow.google.com/credits-terms]`
- banner `img` from `www.gstatic.com/aitestkitchen/website/flow/banners/g1_flow_credits_v2.webp`

Closing the dialog does **not** persist: it was back on the next navigation to `/` within seconds.
`create_project` calls `_dismiss_dialog` straight after `domcontentloaded`
(`migrated_composer.py:765`), before the dialog has rendered, so the dismissal does nothing. The
click then lands on the backdrop and the run ends at exit 23 (`migrated_composer.py:740-752`). This
is the cause of the exit 23 seen on this profile. The dialog offers credits; it does not report a
shortfall, and it is unrelated to the balance.

### Composer: Flow swaps the submit control for a warning as soon as the model is selected

| Phase (headed) | `arrow_forward` | `button.prompt-warning-button` |
|---|---|---|
| fresh project, Flow's remembered model | present (disabled, empty prompt) | absent |
| after `apply_video_settings(model=veo_3_1_quality)` | **absent** | **present**, ligature `info` |
| after typing the prompt | absent | present, ligature `info` |

In the headless run the warning was already present in the fresh project, because Flow remembers
the last-selected model per account (`veo-quality`, from the headed run). The warning appears
**before any prompt is typed**, at model selection. No modal or top-up dialog opens: the composer
shows an orange `info` button where submit was (`spike_out_of_credits_headed_typed_20261127_224702.png`).
The existing anchor `CREDITS_WARNING = "button.prompt-warning-button"` (`_mc_common.py:187`) held.
The `info` ligature is a second structural anchor, newly recorded.

### Real commands

| # | Command | Exit | Message (abridged) | Time |
|---|---|---|---|---|
| 1 | `image t2i "a red cube"` | **23** `UiSelectorDriftError` | "the new-project button … was visible but did not take the click … no create request was sent"; `charged: no` | 30 s |
| 2 | `image t2i "a red cube" --project <spike project>` | **0** | image generated (quota, 0 credits), with the video model short in the same account | 32 s |
| 3 | `video t2v "a red cube" --model veo-quality --project <p> --no-max-credits` | **37** `InsufficientCreditsError` | "Flow replaced the submit control with its insufficient-credits warning"; remediation names `gflow credits user`, a cheaper `--model`, top-up; `charged: no`, `next_action: wait` | 14 s |
| 4 | `video t2v "a red cube" --model veo-quality --no-max-credits` | **23** | same create failure as #1: the promo dialog is met before the composer | 26 s |
| 5 | `video t2v … --model veo-quality --project <p> --max-credits 50` | **45** `CreditCapExceededError` | "Flow quoted 100 credits, exceeding the maximum of 50"; route `flow.google.com settings pane` | 12 s |
| 6 | `credits user --json` | 0 | `credits: 30` (unchanged) | 3.5 s |

Two findings follow:

- **The order of the checks decides which error the user sees.** The cap is checked in the
  settings pane (`_mc_settings_pane.py:164`), before the submit-side credits interrogation. A capped
  run that is also short therefore exits 45, not 37. The quote is read only when a cap is set
  (`_mc_settings_pane.py:63-64`).
- **On an account that shows the promo dialog, every run that creates a project fails at exit 23
  before the composer is reached.** On such an account, exit 37 is reachable only with `--project`.

### The direct transport

**Unmeasured.** `direct_http` serves images only in this tree. A direct video submit bypasses the
composer, so the warning cannot guard it, and no observation proves Flow would refuse it unbilled.
It was not sent. No out-of-credits reply has ever been captured on this repository's wire records:
the only refusal reasons recorded are `PUBLIC_ERROR_UNUSUAL_ACTIVITY`, `PUBLIC_ERROR_UNSAFE_*` and
`PUBLIC_ERROR_VIDEO_GENERATION_TIMED_OUT`.

## Recommendations

**(a) Detection anchors.**

1. Keep `button.prompt-warning-button` → exit 37. It held 2/2 on the video path. It could also
   carry `:has(mat-icon:text-is('info'))` as a second structural check, but the class alone is
   sufficient and is already guarded against a hidden node (#745).
2. Raise exit 37 **at model selection**, not only at submit. Right after `apply_video_settings`
   closes the pane, the warning is already visible, so an `_raise_if_out_of_credits(page)` call
   there turns the 14 s wait into an immediate answer. It must stay silent when the warning is
   absent, so drift still surfaces as drift.
3. **The promo dialog is a separate defect, and exit 23 is the wrong exit for it but not a credits
   one.** It should be dismissed after **waiting** for it, bounded (the measured fix: wait for
   `[role=dialog]`, then `_dismiss_dialog`, 2/2). Anchor it on `flow-g1-promo-dialog` and
   `button.close-button`. Do not click `claim-button` or `no-thanks-button`: either answers a
   question on the account's behalf. It must not map to `InsufficientCreditsError`, since it
   appears on funded accounts too. When the click still fails, the exit-23 detail should name the
   overlay found at the hit point (`g1-promo-dialog-backdrop`).

**(b) A browser-free pre-check before billed video.** Worth adding, as a comparison, not a guess.
The `nzlxg` read costs about 2.5 s with no browser and is already the default path for
`gflow credits`.

- A balance of **0** is a certain refusal for every billed model. Raise exit 37 before a browser
  starts, with `charged: no`. That alone removes the case this spike set out to measure.
- A **positive** balance cannot be judged without the model's price. The price comes from Flow's
  quote in the settings pane (100 for `veo-quality` here), which is read only when a cap is set.
  The rigorous check is therefore in the composer: read the quote unconditionally and compare it
  with the `nzlxg` balance. That gives exit 37 with both numbers ("quoted 100, balance 30") before
  the prompt is typed.
- Interaction with `--max-credits`: the cap answers "will I let this cost N". The balance check
  answers "can the account pay N". Run the cap first (exit 45, a user decision), then the balance
  (exit 37), as today.
- For the direct-video route, the composer is never opened, so its quote and its warning are not
  available. The pre-check there must be `nzlxg` against a per-key price table, or the route must
  refuse unknown prices. A per-key table is a new measured artefact, needing the same
  "measured keys only" rule as the direct image models.
- A read failure (429/503, an expired session) must not block generation; it skips the pre-check.

**(c) The direct transport's out-of-credits reply.** Not observed. Until it is captured,
`SubmitOutcome` must not guess.

- Only `PUBLIC_ERROR_UNUSUAL_ACTIVITY` may become `RefusedNotBilled` with a retry: a credits
  refusal retried headless is a second billed attempt against an account that cannot pay. An
  unrecognised reason must stay `Indeterminate` (reconcile, never resend), which the planned
  `classify_reply` already does.
- When a credits refusal is captured, it belongs to its own terminal outcome mapped to
  `InsufficientCreditsError`, `charged: no` after a `nzlxg` read confirms no change. It must never
  go to the WAF retry.
- The cheapest way to capture it is a $0 one: drain an account to 0 and send one direct submit
  for the cheapest key. Flow cannot bill an empty balance, and a `nzlxg` read before and after
  proves it.

## Not measured

- A true 0 balance, on either the UI or the direct path.
- Whether `no-thanks-button` stops the promo from returning. It was not clicked: it answers a
  question for the account.
- Image models at 0 credits (they draw on quota), and the warning on extend, i2v or r2v.
- labs.google: no profile here can reach it.
- The two empty spike projects were not deleted: gflow has no project delete. One holds the image
  from command #2.
