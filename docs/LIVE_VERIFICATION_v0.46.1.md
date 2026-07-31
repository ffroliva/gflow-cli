# Live Verification — v0.46.1

**Date:** 2026-07-31
**Feature under test:** classic count-setter survives Flow's `1x`→`x1` label rename ([#404](https://github.com/ffroliva/gflow-cli/issues/404), PR [#405](https://github.com/ffroliva/gflow-cli/pull/405))
**Verifier:** repo owner's Windows 11 workstation, profile `ffroliva` — the exact environment of the original incident bundles (`20260729T151458Z-f9c00656…`, `20260729T151827Z-4f4a6093…`)
**Credit cost:** zero — image generation costs no Flow credits; the DOM recon was navigation-only.

## Why this release needs live verification

The defect *was* Flow-side UI drift: only a real composer can prove the renamed `x1`
tab exists, that a digit-keyed click converges, and that the selection persists.
The pre-fix unit suite was fully green while every real `-n 1` run died — the fakes
ignored locator filters, so no offline test could have caught this (they now honor
them, and the pre-fix failure is reproduced mechanically in
`tests/api/transports/test_ui_automation.py::TestSetCountRetry`).

## 1. Root-cause recon (credit-free, navigation only)

`scripts/dev/spike_issue404_count_tabs_recon.py` against project `gflow-cli t2i`:

| Probe | Result |
|---|---|
| Image count row labels | `['x1','x2','x3','x4']` — `1x` gone (video row identical) |
| Old filtered set (`^(1x\|x[2-4])$`) | size **3** — count-1 tab dropped |
| Replicated failing click (`nth(0)`) | landed on **`x2`**, moved the count 1→2 (away from target) |
| Selected `x1` under old read-back | `None` (regex miss) |
| Digit-keyed click on `x1` | `aria-selected="true"` immediately |
| Persistence | selection survives Escape-close + reopen — **no Save commit** |

Artifacts on disk: `scripts/dev/_spike_out/spike_issue404_count_tabs_*.json` +
step screenshots (`404_1`–`404_4_*.png`) + `_diagnostics/count_panel_dom_prompt_404.json`.

## 2. Classic surface — the reported invocation (2 passed, 89 s)

`tests/e2e/test_classic_count_setter_e2e.py` (`GFLOW_CLI_UI_MODE=classic`, 9:16,
NARWHAL — mirroring the reporter's run):

- **count=1 (the regression):** fresh project displays 2 → the renamed `x1` tab was
  located, clicked, converged; exactly **1** generation result returned.
- **count=2 (early-exit):** already-matching display short-circuits with no click;
  exactly **2** results.
- structlog invariant: final `ui_automation.count_setter_completed` has
  `success=True` in both cases; no `error_unhandled`.

## 3. Agentic surface (4/4 passed, ~4 min)

`tests/e2e/test_agentic_count_enforcement_e2e.py`, counts 1–4, each against a
**deliberately mismatched** sticky panel default:

- All four counts returned exactly the requested number of images; every image
  downloaded with a recognized magic-byte format (PNG/JPEG/WebP) and non-zero size.
- `ui_driver.bound mode=agentic`; no `enforce_count_failed` events.
- First attempt failed in the *test's own setup* (Flow now loads the editor in
  classic mode; the raw-Playwright setup assumed the Agent composer) — repaired in
  `beeb833` by toggling via the composer pill before waiting for the `tune` icon.
  No product code was implicated in that failure.

## 4. Not verified live this cycle (recorded, not omitted)

- **Video output-count cascade** (`_set_output_count` widened to probe `x1` then
  legacy `1x`): a full end-to-end drive would spend Veo credits. DOM-level evidence
  from §1 covers the selector shape (the video row renders `x1..x4`; a
  `[role='tab']:text-is('x2')` click — the cascade's exact selector form — executed
  live during the spike's cleanup). Miss remains non-fatal by design
  (`Flow default (x2) applies`).
- Image **dimensions** were not re-measured this cycle (format sniff + non-empty
  size only); the original incident's passing run already pinned 768×1376 for this
  aspect/model pair.

## 5. Ledger summary

| Layer | Evidence |
|---|---|
| File count | exact requested count, 6 live generations across counts 1–4 (§2, §3) |
| Magic bytes | PNG/JPEG/WebP sniff on every downloaded image (§3) |
| Dimensions | not re-measured (recorded above); format+size verified |
| structlog invariants | `count_setter_completed success=True`, `ui_driver.bound`, zero `error_unhandled` / `enforce_count_failed` |
| User-confirmable artifact | spike JSON + 4 step screenshots under `scripts/dev/_spike_out/` |
