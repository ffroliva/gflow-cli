# The migrated composer has an Image mode — the claim that it does not is falsified

**Date:** 2026-09-07 · **Cost:** $0 (navigation, one click, DOM reads) · **Issue:** #692
**Probe:** [`scripts/dev/spike_migrated_composer_mode_axis.py`](../../../scripts/dev/spike_migrated_composer_mode_axis.py)
**Raw evidence:** `scripts/dev/_spike_out/spike_migrated_composer_mode_axis_20260907_090356.json` (gitignored)

## What was observed

Live, profile `ci-probe`, project `1e4efe0d-afcf-4e0d-ae4d-b4431f2d73de`, landed on
`https://flow.google.com/project/<id>`. Clicked `.settings-trigger-button`, waited for a
`.cdk-overlay-pane` **containing** a `[role='radiogroup']` to become visible, then
enumerated it:

```
6 groups, 16 radios
group0  [imageImage, videocamVideo*]                      <- mode
group1  [crop_freeFrames, chrome_extensionIngredients*]   <- submode
group2  [crop_16_916:9*, crop_9_169:16]                   <- aspect
group3  [360pinfo*, 720p]                                 <- resolution
group4  [4s, 6s, 8s*, 10s]                                <- duration
group5  [x1, x2*, x3, x4]                                 <- count
                                        (* = aria-checked)

image_mode_present   = True
image_mode_hit_testable = True
```

The VIDEO option is the one carrying `aria-checked`. **Why** is not established by this
run: the probe opened a fresh page and read persisted overlay state, and never invoked
the driver. Flow remembering the account's last-used mode explains it as well as anything
gflow does — and the same reading shows `submode` checked on Ingredients, which
`apply_video_settings` only ever sets for I2V/R2V, so persisted state is demonstrably in
play here.

Separately and independently of this measurement:
`MigratedComposer.apply_video_settings` does pin `axis="mode"` to `videocam`
unconditionally — the only `axis="mode"` call in the file, with a hardcoded ligature. **The
`mode` axis is write-only to video** as far as gflow is concerned. That is a fact about the
driver, read from the source, not a conclusion from this probe.

## What it falsifies

`src/gflow_cli/api/client.py` (as of `3ba9e88`, 2026-09-06 22:03) asserted:

> "the migrated project composer **has no image-generation mode**. Its add menu is a
> media library (Scenes / Images / Videos / Upload media) and its settings radios are
> **grid/batch and size** — **measured, not assumed**."

Three of those statements are wrong. The settings radios are mode / submode / aspect /
resolution / duration / count — not "grid/batch and size", which name no axis in the
overlay, in `migrated_composer.py`, or in the 2026-09-04 capture. There **is** an image
mode. And it was not measured.

## Why it survived

This reproduces the 2026-09-04 enumeration in
[`2026-09-04-migrated-host-handoff-mechanism.md:123-136`](2026-09-04-migrated-host-handoff-mechanism.md)
**exactly** — same six groups, same sixteen radios — three days later on a different
account. So the contradicting evidence was already committed to this repo when the
retracted claim was written.

The claim cited `scripts/dev/spike_migrated_image_capability.py`, which:

- was added in the **same commit** as the conclusion citing it;
- has **no recorded run** anywhere in the tree — no finding doc, no committed output;
- opened the settings overlay **best-effort**, swallowing the exception, so it completed
  and printed `"no image mode surfaced; the migrated composer looks VIDEO-ONLY"` having
  potentially never opened the panel;
- would have contradicted itself had it read that panel: its own image-ligature filter is
  `/(image|photo|picture|palette|brush|draw|frame|camera|art)/i`, and the string
  `imageImage` matches it. The verdict would have been *"an image capability may be
  drivable — investigate"*.

So the most probable history is: the click failed, the exception was swallowed, the
script read the default view, and its unconditional verdict became "measured, not
assumed" in shipped code.

## The pattern, twice now

| | Feature | "Evidence" | Reality |
|---|---|---|---|
| 2026-09-06 | `character create` on the migrated host | one 20 s selector timeout | the editor was fully present; Angular + ProseMirror, not React + Slate |
| 2026-09-07 | `image` mode on the migrated composer | a verdict printed by a probe whose overlay-open had silently failed | the mode radio is present and hit-testable |

Both are the same defect in a probe, not in Flow: **a measurement that could not be taken
was recorded as a measurement of absence.** `skills/spike/SKILL.md` already forbids the
first form ("a selector that does not match is evidence about the selector"). This spike
adds the second: **a probe that cannot reach the surface it exists to read must fail, not
conclude.**

Both probes have been changed accordingly — `spike_migrated_image_capability.py` now
raises `SpikeUnreachedError` and exits 3 with *"This is a failed measurement, NOT evidence
of absence"* rather than printing a verdict, and the new mode-axis probe is hard-error at
every step.

## What was NOT measured

- Whether clicking `imageImage` actually switches the composer into an image mode that
  submits — only that the radio is present, enabled and hit-testable. **Nothing was
  clicked on the `mode` axis and nothing was submitted.**
- What the image path's submit `rpcid` is, or whether it matches the labs
  `batchGenerateImages` shape.
- Whether the `labs.google` cohort renders the same overlay (every account on this
  machine is migrated).
- Whether routing the mint to `/project/<id>` is sufficient to get `gflow image` past the
  guard — that is the next probe, and it is the one that decides the size of the port.
