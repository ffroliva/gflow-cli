# The Format button was fixed, and `--format-prompt` still did nothing

> **RESOLVED in the same PR ([#729](https://github.com/ffroliva/gflow-cli/pull/729)).** This
> records the measurement that found the second fault, not a standing limitation.
> `format_character_prompt` now polls the composer until the text actually changes, and the
> live e2e passes (`1 passed in 406.20s`, `denon82`, 2026-09-07) asserting the **observed
> rewrite** rather than the click. Kept because the measurement is what makes the fix
> defensible, and because the failure class it names outlived it.

**Date:** 2026-09-07 · **Follows:** [#727](https://github.com/ffroliva/gflow-cli/issues/727),
[#729](https://github.com/ffroliva/gflow-cli/pull/729) ·
**Account:** `denon82` (migrated `flow.google.com`) · **Cost:** `$0` — never submits, no
image, no video, no credit.

**Script:** `scripts/dev/spike_format_prompt_effect.py` ·
**Capture:** `scripts/dev/_spike_out/format_prompt_effect_denon82_20260907_163552.json`
(gitignored)

---

## 1. Why this was measured at all

#727 fixed the *anchor*: `format_character_prompt` now finds and clicks Flow's Format
button. It then waits `_jitter_ms(500)`, logs `ui_automation.prompt_formatted`, and
returns — and `_send_prompt` calls `_click_submit` on the next line.

That event proves **a click**. It has never proved **a rewrite**
([[playwright-click-no-downstream-event-signature]]). The PR council's D6 reviewer
flagged exactly this gap: the #727 spike captured no network, so nothing established
that the click reached a backend, let alone that its result arrived before the submit.

## 2. What was measured

Seed a deliberately terse prompt, confirm it landed, click Format through the **shipped**
cascade, then poll the box every 250 ms for 20 s while recording every request:

```
baseline        'girl, red hair, sad'                       19 chars
format_clicked  flow-format-prompt-button button
box_text        t=0.011   chars=19    'girl, red hair, sad'      <- unchanged
box_text        t=5.355   chars=651   'Medium studio shot of a young girl with…'
verdict         changed=True  changed_at_s=5.355  within_current_wait=False
```

Two states, one discrete swap — no streaming, no intermediate text. The request that
does it:

```
t=1.191  POST https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute
         ?rpcids=eAenfb&source-path=/project/<id>
```

**The rewrite is a server round trip on `batchexecute` rpcid `eAenfb`, and the DOM
settles ~5.4 s after the click.**

## 3. The finding

```
gflow waits ~0.5 s. Flow answers in ~5.4 s. The submit fires in between.
```

`--format-prompt` therefore submits the prompt **the user typed**, discarding the
reshaped one, on every run — including runs where the button is found, enabled, clicked,
and `ui_automation.prompt_formatted` is emitted. The flag has never worked on this host:
before #727 it missed the button, and after #727 it clicks a button whose answer it
throws away.

**The e2e does not catch this.** `test_character_create_format_prompt_clicks_format_button`
asserts `prompt_formatted` is present and the two failure events are absent. All three
hold. The test is green and the feature is broken — it asserts the click because, at the
time it was written, the click was the only observable.

## 4. What makes this fixable

The rewrite is **observable in the DOM**: the prompt box text changes, in one step, from
what we typed to something else. So the settle signal is a condition, not a duration —
poll the box until its text differs from the text we inserted, with a timeout.

That also upgrades the telemetry: `prompt_formatted` can be emitted on the **observed
rewrite** rather than on the click, which makes it mean what its name has always claimed,
and makes the existing e2e assertion meaningful rather than incidental.

Anchoring on the text changing is locale-invariant by construction — it compares Flow's
output against **our own** inserted string and never reads a display label.

## 5. What this did NOT measure

- **How variable ~5.4 s is.** One observation, one prompt, one account, one moment. A
  timeout has to be chosen against a distribution nobody has sampled yet; 5.4 s is a
  single point, not a budget.
- **Failure modes of the rewrite.** What the box does if `eAenfb` errors, rate-limits, or
  returns empty was never provoked. A poll-until-changed loop needs to know whether
  "unchanged" can also mean "Flow declined".
- **The labs frontend.** No unmoved account exists here. Whether labs rewrites in-place,
  at what latency, and on what rpcid is unknown.
- **Whether the rewrite draws on any quota.** Nothing observable here says. No image is
  generated.
- **Idempotency.** Clicking Format twice, or clicking it on already-formatted text, was
  not tried.

## 6. The rule this re-earns

```
A CLICK IS NOT AN EFFECT.
```

#727 replaced "the selector missed" with "the selector matched" and stopped there, because
matching was what the ticket asked for. The council caught that the evidence chain ended
at the click; measuring one step further showed the feature still did not work. A green
test that asserts the last thing you happened to be able to observe is not a green feature.

**What the fix cost, measured after the fact.** Making the flag work made the run slower in
two places: the rewrite itself (~4-5 s), and the generation, because Flow is now given a
608-character elaboration instead of the 102 characters the user typed. Live: **406 s with
`--format-prompt` against a 210 s control** on the same account and face prompt. That is not
a regression to fix — it is the actual cost of the feature, which was free only while it was
doing nothing. It did overrun the e2e's 300 s budget, which is why that budget moved to 480 s.

See also: [2026-09-07 — the Format button's anchor](2026-09-07-character-format-button-anchor.md),
[`skills/spike/SKILL.md`](../../../skills/spike/SKILL.md).
