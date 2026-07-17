# Live Verification — v0.38.0

**Date:** 2026-07-17 · **Profile:** `ffroliva` (real Chrome, live Flow) · **Tree:** `develop` @ v0.38.0 prep

This release's user-facing changes were exercised end-to-end against live Flow before the
tag. Evidence uses the 5-layer ledger (file count + magic bytes + shape + structlog
invariants + user-confirmable artifact).

## Features covered

| Feature (release) | How verified |
|---|---|
| **Robust agentic↔classic mode control** (#332) | The t2i run below requested `--ui-mode classic` on a profile that loaded in the agentic cohort; the log shows `ui_driver.ui_mode.attempt_exit_agent` followed by the full classic-editor chain — the exact path that previously false-aborted as "forced agentic, not recoverable". Also live-validated in #332 itself via `scripts/dev/spike_mode_roundtrip.py` (full classic→agent→classic round-trip asserting `aria-pressed`). |
| **i2i ref dedup via picker filename search** (#314, #334, #335) | Two identical `image i2i` runs against the same project with a uniquely-named local ref. Run 1: picker search missed (scroll probes logged) → `image_uploaded` (HTTP 200) → `reference_attached`. Run 2: `reference_deduped_by_filename` (`dedup-v038-check.jpg`), **zero** upload events, `reference_attached` via the existing tile, generation OK. This closes the "Live e2e re-verify pending" note on #335. |
| **Cognitive-complexity refactor** (#331) | Behavior-preserving by construction (pyright 0/0/0, 1202 scoped tests, adversarial behavior-diff review in the PR); all three live runs below traverse the refactored `_enter_setup` client path. |
| **PR-Triage Autopilot** (#238, #333) | **Not live-verifiable this cycle:** the autopilot executes on the hermes-ops VPS, whose deployment is staged separately (implementation + fixture evals only in this repo). Recorded here per the release gate rather than silently omitted. |

## E2e evidence — `gflow image t2i --ui-mode classic` (classic cohort, #332 path)

- **Command:** `image t2i "a red vintage bicycle leaning against a stone wall, morning light, photorealistic" --aspect 16:9 -n 1 --ui-mode classic` · **exit 0** · `status: ok`.
- **File count:** 1 JPEG produced.
- **Magic bytes:** `ff d8 ff e0` (valid JPEG).
- **Shape:** 1376×768 (16:9), 1.16 MB, model `NARWHAL`.
- **Structlog invariants:** `ui_mode.attempt_exit_agent → image_mode_entered → image_model_selected → aspect_ratio_set → count_setter_completed → prompt_submitted → batch_response_captured`.
- **Artifact:** `2ac7ae3f-…_1.jpg`.

## E2e evidence — `gflow image i2i` ×2, same ref (dedup, #314)

- **Commands:** `image i2i "<prompt>" --ref dedup-v038-check.jpg --project 9d5b9a86-…` run twice · both **exit 0** · `status: ok`.
- **Run 1 (miss→upload):** `picker_scroll_probe`×4 → `picker_scroll_done` → `image_uploaded` (HTTP 200) → `reference_attached`; 1 JPEG generated.
- **Run 2 (hit→dedup):** `reference_deduped_by_filename {filename: dedup-v038-check.jpg}` → `reference_attached`; **0** `image_uploaded` events; 1 JPEG generated.
- **Artifacts:** `582da216-…_1.jpg` (run 1), `00e99878-…_1.jpg` (run 2).

## E2e evidence — `gflow video t2v` (veo-fast, classic cohort)

- **Command:** `video t2v "slow dolly shot along a misty forest road at dawn, cinematic" --aspect 16:9 --model veo-fast` · **exit 0** · `generation_status: MEDIA_GENERATION_STATUS_SUCCESSFUL`.
- **File count:** 1 MP4 produced.
- **Magic bytes:** `ftyp isom` container (valid MP4).
- **Shape:** 15.2 MB (landscape), model `veo_3_1_fast`.
- **Structlog invariants:** `video_mode_entered → editor_ready → model_selected → aspect_set → output_count_set → generate_captured → poll_terminal(SUCCESSFUL) → video_saved`.
- **Artifact:** `3d001ce4-….mp4`.

## Pre-tag gates

- `/gflow:check`: hygiene + doc links + ruff check/format + pyright 0/0/0 green; full-suite CI green on the exact release tree (develop @ `b97a8ea`).
- `/gflow:doc-review`: mechanical pass green after fixes (PROJECT_STATUS updated for v0.37.0+v0.38.0; shipped plan dirs consolidated). _Council verdict: YELLOW/YELLOW/GREEN across the 3 auditors. 6 findings: 0 Tier 1; 4 Tier 2 fixed in the release-prep commit (USAGE i2i dedup semantics, KNOWN_ISSUES resolved entry for the false forced-agentic abort + exit-25 remediation refresh, AGENTS.md `--browser` claim, AGENTS.md video `batch` stub label); 2 cosmetic noted, no action. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._

## Result

All live generation paths succeeded end-to-end on the v0.38.0 tree: the #332 mode
controller recovered agentic→classic in a real run, and the #314 dedup path selected the
existing library tile instead of re-uploading. Release gate **PASS**.
