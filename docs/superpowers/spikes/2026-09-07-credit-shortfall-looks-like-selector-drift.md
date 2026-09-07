# A credit shortfall removes Flow's submit button, and gflow called it selector drift

**Date:** 2026-09-07 · **Cost:** $0 (types a prompt, never submits) · **Issues:** #721, #725, #719
**Probe:** [`scripts/dev/spike_migrated_submit_anchor.py`](../../../scripts/dev/spike_migrated_submit_anchor.py)
**Raw evidence:** `scripts/dev/_spike_out/spike_migrated_submit_anchor_*.json` (gitignored)

## What was observed

`gflow video t2v` failed repeatedly on the migrated host with:

```
UiSelectorDriftError (exit 23): migrated host: the submit button (arrow_forward)
is missing after the prompt was typed
```

and a remediation hint telling the user *"Google may have updated their frontend … file a bug."*

The frontend was fine. Flow does not **disable** the submit control when a generation cannot
be paid for — it **replaces** it:

```
lig=['info']  aria-label='Insufficient credits warning'  class='prompt-warning-button'
```

### The A/B

Same probe, same code, same migrated host, ~60 s apart. One variable.

| profile | balance | model asked | that model's cost | `arrow_forward` after typing | `prompt-warning-button` |
|---|---|---|---|---|---|
| `ci-probe` | **50** (`G1_FREEMIUM`) | `veo-quality` | **100** | **ABSENT** | **PRESENT** |
| `ffroliva` | funded | `omni-flash` | — | **PRESENT** | ABSENT |

Full ligature inventory on the shortfall account after typing — no submit-shaped icon anywhere:

```
accessibility_new, add, apps_spark_2, arrow_back, close, crop_16_9, dashboard,
delete, favorite, filter_list, help, info, left_panel_close, more_vert, movie,
search, settings_2, swap_horiz
```

The only ligature that appears *after* typing is `close` ("Clear prompt"), which confirms the
text landed. The composer held a prompt, had settings applied (`Video · 720p · 8s · x1`), and
offered no way to submit it.

## The correction, which is the more useful half

**I first reported this as an empty wallet. It was not.** The account held **50 credits**;
the run asked for a 100-credit model. Flow's own label says *Insufficient*, not *none*, and
`gflow credits user --profile ci-probe` says `Credits: 50` in one command. I read the label,
then wrote something the label does not say — into an issue title, an issue body, a memory
file, and from there into another session's error text and commit message.

It was caught by a second session asking the question I never asked: **how much credit is on
that account?**

The distinction is not cosmetic, because it changes what the user can do:

- *"Your account has no Flow credits left"* is a **dead end** for someone holding 50. It reads
  as a gflow bug and offers no action.
- *"Short for this model"* has a **remedy**: pick a cheaper tier. `veo-lite` costs 10 against
  `veo-quality`'s 100, so an account in this exact state can generate immediately.

## Why it was worth fixing beyond the wording

The old error actively instructed users to file frontend-drift bugs. So a credit state
**manufactures unreproducible reports** against the one thing this project's credibility rests
on — its ability to track a genuinely moving frontend. Every such report is unreproducible by
anyone with credits, and no code change can ever close one.

It is also the third variant this week of one failure shape:

| | Feature | "Evidence" | Reality |
|---|---|---|---|
| 09-06 | `character create` on the migrated host | one 20 s selector timeout | the editor was fully present |
| 09-07 | `image` mode on the migrated composer | a verdict printed by a probe whose overlay-open had silently failed | the mode radio is present and hit-testable |
| 09-07 | the submit button | the anchor genuinely absent | **the anchor was absent because the STATE changed, not the frontend** |

The first two are "a measurement that could not be taken, recorded as a measurement of
absence". This one is different and worth naming separately: **the measurement was correct —
the anchor really was gone — and the inference from it was wrong.** An absent control can mean
a changed account state. Check the account before blaming the frontend.

## What was NOT measured

- **The positive match on `prompt-warning-button` was seen ONCE**, at 11:17. One sighting is
  not a characterisation. The constant is proven to *parse* through Playwright's engine against
  a live migrated page (count 0 on a funded account, control `arrow_forward` = 1), but a
  repeated positive match is still owed. Named as a blocker in #725 rather than claimed.
- **Whether the same account can reliably reach the composer at all.** Two runs of mine
  (09:03 and 11:17) landed on `/project/<id>` and found `.settings-trigger-button`; another
  session at 13:49 reported the same URL redirecting to `/about` with no trigger. Unresolved.
  The leading hypothesis is the navigation path — `goto` + a fixed 6–8 s wait here, versus the
  production readiness gate there — not the account. Handed to a separate investigation with a
  control, so that "both land on /about" cannot be confused with a broken probe.
- **Whether the upload failures on the same account share this cause.** Local-file uploads
  there fail with `no maseQ reply within 60s` (#719). Two profiles cannot separate balance,
  SKU, model shortfall, and "something else on that account" — four candidates, one
  comparison. The correlation is recorded; the mechanism is not claimed.
- **Whether labs.google behaves the same way.** Every account on this machine is migrated, so
  it could not be checked. It decides whether the guard belongs in the shared submit path or
  only in `migrated_composer`.
