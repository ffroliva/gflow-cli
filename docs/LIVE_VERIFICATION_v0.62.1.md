# Live verification — v0.62.1

Every claim below was **observed**. Claims that could not be observed are stated
as such in §4, not omitted.

## Environment

| | |
|---|---|
| Date | 2026-08-30 |
| Profile | `ffroliva` (locale `en`), project `bb66227b-e8c5-42ab-b5fe-03ed4050faf5` |
| Code under test | `develop` @ `1638a60` (the #604 squash), transport byte-identical on the release branch |
| Harness | `scripts/dev/live_verify_video_model_select.py` — drives the production `_select_video_model` against the real Flow DOM |
| Veo credits spent | **0** — model selection happens in the settings panel entirely *before* submit, and this harness never submits |

## 1. #604 — `--model omni-flash` selects again after the picker rename

The release exists for one reason: Flow renamed the tier to `Omni 1.1 Flash`,
`has-text` is a contiguous substring match, and `v0.62.0` therefore refused every
explicit `--model omni-flash` run with exit 18. The decisive line:

```json
{"model": "omni_flash",
 "via": "[role='menuitem']:has-text('Omni'):has-text('Flash'):not(:has-text('[Lower Priority]'))",
 "event": "ui_automation_video.model_selected"}
```

That is the shipped selector string, verbatim — including the `:not` exclusion
added after the code review — resolving against the live picker.

## 2. All four offered tiers still select; both refusal paths still refuse

The widened selector must not break the working paths, and the fail-loud gate
must stay loud. Both arms were exercised in the same run:

| Case | Expected | Actual |
|---|---|---|
| `omni_flash` | SELECTED | **SELECTED** |
| `veo_3_1_lite` | SELECTED | **SELECTED** |
| `veo_3_1_fast` | SELECTED | **SELECTED** |
| `veo_3_1_quality` | SELECTED | **SELECTED** |
| `veo_3_1_lite_lower_priority` (not offered to this account) | REFUSED | **REFUSED** — `not selectable — no picker entry matched after 2 attempts` |
| `veo_3_1_fast` with a deliberately AMBIGUOUS selector | REFUSED | **REFUSED** — `AMBIGUOUS — 3 entries match "[role='menuitem']:has-text('Veo 3.1')"` |

**6/6 as expected.** The AMBIGUOUS case is the one that matters for this change:
it proves the transport still refuses rather than resolving `.first`, which is the
behaviour the widened Omni selector relies on if Flow ever ships two concurrent
Omni tiers.

## 3. Ledger

The 5-layer ledger is adapted: this verification deliberately produces **no media
file**, because proving model *binding* costs nothing while generating costs Veo
credits. Layers 1–3 (file count, magic bytes, dimensions) are therefore N/A by
design, not by omission.

| Layer | Observed |
|---|---|
| File count | N/A — no submit, by design |
| Magic bytes | N/A — no submit, by design |
| Dimensions / shape | N/A — no submit, by design |
| Structlog invariants | Per case: `mode_switch_trigger` matched (`crop_9_16` arm) → `video_mode_tab` matched → `video_mode_entered` → `model_picker_trigger` matched → `model_selected model=<m> via=<selector>`; for the refusals, `model_option_retry` ×2 → `model_option_not_found` |
| User-confirmable artifact | Live picker inventory read back from the refusal path: `['volume_up Omni 1.1 Flash', 'volume_up Veo 3.1 - Lite', 'volume_up Veo 3.1 - Fast', 'volume_up Veo 3.1 - Quality']` — byte-confirming both the new Omni label and that the Veo labels did **not** move |

That inventory line is independent evidence for the fixture in
`tests/fixtures/flow_model_inventory.json`: it comes from the transport's own
diagnostic, not from the probe that wrote the fixture.

## 4. Recorded as NOT verified

- **The rename *direction*.** Only the 2026-08-30 labels are byte-verified. The
  `Omni Flash` baseline is a different account, locale and date (`denon82`, pt,
  2026-08-26), so a single read cannot separate a global in-place rename from a
  cohort- or locale-gated label. What corroborates the rename is that
  `has-text('Omni Flash')` stopped matching for the #604 reporter on an account
  where it had worked. The selector matches both labels either way.
- **An actual `Omni ... [Lower Priority]` entry.** The `:not` exclusion is graded
  offline against a synthetic menu carrying that label; Flow has never been
  observed offering it. It is a guard against a shape Flow already ships for Veo
  Lite, not against an observed entry.
- **A full generation on `omni-flash`.** Not run — it costs Veo credits and the
  selection binding is what this release changed. The last end-to-end
  `--model omni-flash` generation (exit 0, real 2.2 MB `ftypisom` mp4, catalog
  recording `omni_flash`) is in
  [LIVE_VERIFICATION_v0.61.0](LIVE_VERIFICATION_v0.61.0.md).
- **`#539`'s open question.** `Veo 3.1 - Lite [Lower Priority]` was again not
  offered — now three observations across two accounts and two locales. Still an
  observation, still not proof of absence; #539 stays open.
