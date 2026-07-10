# Live Verification — v0.31.0

**Date:** 2026-07-10 · **Profile:** `denon82` · **Project:** `f6caf027-ad68-49e3-aac9-ee32f1582bf3`
(the standing production project — deliberately asset-heavy, i.e. the exact environment of the
2026-07-10 wrong-media incident this release fixes) · **Build:** develop @ `ecfb5ed`
(merge of PR #285) run via `uv run gflow` · **Model:** nano-pro · **Aspect:** 9:16 ·
**Credit cost:** 0 Veo credits (image generation only).

Features under verification (CHANGELOG `[Unreleased]` → 0.31.0):
1. **#281 layer 1** — agentic ambiguity fail-fast + two-pass baseline settle (`MediaAttributionError`, exit 26)
2. **#281 layers 2/3** — pre-download attribution guard + route-scoped collision escalation
3. **#282** — UUID `--ref` picker: virtualised-grid scroll + search-state hygiene

## Run 1 — t2i portrait (musician): correct attribution end-to-end ✅

`gflow image t2i "<musician portrait prompt>" --model nano-pro --aspect 9:16 --project f6caf027-… --out <dir>`

5-layer ledger:
1. **File count:** exactly 1 file produced: `98a404e7-766d-443c-9385-a16fdb37c799_1.jpg` (886,886 bytes; sha256 `e25e96be454f0215…`).
2. **Magic bytes:** `ff d8 ff` (JPEG).
3. **Dimensions:** 768×1376 (native 9:16, no crop).
4. **Structlog invariants:** real wire metadata surfaced — `seed=648895`, `dimensions=768x1376`
   (not the agentic scrape sentinels seed=0/0x0); **no** `data.persistence_failed_after_success`
   warning (the 0.30.0 incident's tell); recorder wrote history cleanly.
5. **User-confirmable artifact:** image visually inspected — weathered street musician, late 50s,
   grey beard, black beanie, worn dark jacket, guitar, plain neutral background, front view.
   Exactly the prompt; NOT a pre-existing project asset. (Under 0.30.0 in this same project, this
   exact call once returned an old brand logo.)

## Run 2 — t2i portrait (mentor), attempt 1: ambiguity fail-fast fires ✅ (negative-path proof)

Same command shape, mentor prompt. The generation hit the incident-class condition live: the
asset-heavy project lazy-rendered multiple prior assets after the baseline settle, and the run
**failed loudly instead of guessing**:

- `MediaAttributionError` raised naming **every** candidate UUID (the list included
  `6a0b44d5-…` and `64fc2c31-…` — assets generated earlier that day, i.e. genuinely pre-existing
  media that 0.30.0 would have been free to silently download) plus the remediation text
  ("Re-run the generation; a dedicated project with fewer pre-existing assets avoids lazy-render
  ambiguity (issue #281)").
- **No file was written** for this attempt (output dir unchanged).

This is the strongest possible live evidence for #281: the exact failure environment reproduced,
and the new behavior is a actionable hard stop, not a wrong artifact with exit 0.

## Run 2 — t2i portrait (mentor), attempt 2: clean ✅

Retry of the same command: 1 file `dacdc6ee-2725-4e5c-b08f-c7143c36d9b6_1.jpg` (591,180 bytes;
sha256 `e82cc96b3bba60cf…`; JPEG magic `ff d8 ff`; 768×1376; real `seed=744814`; no persistence
warning). Visually inspected: elegant silver-haired man, 60s, tailored black suit, pocket square,
neutral background — exactly the prompt.

## Run 3 — i2i with two UUID `--ref`s (#282 picker) ✅

`gflow image i2i "<street scene prompt>" --ref dacdc6ee-… --ref 98a404e7-… --model nano-pro
--aspect 9:16 --project f6caf027-… --out <dir>` — both refs are the freshly generated portrait
UUIDs from Runs 1–2, attached by picker selection (no local file passed). Under v0.30.0 this exact
call failed structurally: only the first UUID was picker-selectable (#282).

Two attempts noted for completeness: attempt 1 failed pre-submission with
`UiSelectorDriftError probe=mode_switch_trigger` whose debug screenshot was an entirely black
viewport — a page-that-never-rendered transient, not selector drift; zero generations spent.
Attempt 2 succeeded.

5-layer ledger:
1. **File count:** exactly 1 file: `d6f1927a-3eae-4626-bc90-9a6ea7637bab_1.jpg` (670,708 bytes;
   sha256 `fd828df1a33e37c0…`).
2. **Magic bytes:** `ff d8 ff` (JPEG).
3. **Dimensions:** 768×1376 (native 9:16).
4. **Structlog invariants:** real `seed=244712`; both refs attached (no
   "could not be selected in the picker" error, no upload fallback); no persistence warning.
5. **User-confirmable artifact:** visually inspected — BOTH canonical identities transferred:
   the mentor is the same silver-haired man from Run 2's portrait (same face, suit, and paisley
   pocket square), the musician the same man from Run 1's (same beanie, beard, work jacket).
   Monochrome street scene with the iridescent coin accent confined to the guitar case. No
   pre-existing project asset leaked into the frame.

## Verdict

All three release features live-verified: #281's defense layers in both directions (correct
attribution succeeds with ground-truth metadata; incident-class ambiguity hard-stops with
actionable output and no file written), and #282's multi-UUID-ref picker selection end-to-end —
which simultaneously validates the canonical-reference identity workflow the fixes exist to serve.
