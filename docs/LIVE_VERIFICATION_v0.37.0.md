# Live Verification — v0.37.0

**Date:** 2026-07-17 · **Profile:** `ffroliva` (real Chrome, live Flow) · **Tree:** `develop` @ v0.37.0 prep

This release's user-facing changes were exercised end-to-end against live Flow before the
tag. Evidence uses the 5-layer ledger (file count + magic bytes + shape + structlog
invariants + user-confirmable artifact).

## Features covered

| Feature (release) | How verified |
|---|---|
| **UI-automation viewport 1920×1080** (#327) | Both e2e runs below launched at 1920×1080; every selector resolved at that size (no drift). Also live-verified in PR #327 across the agentic (image) and classic (video) cohorts. |
| **Auth-login viewports 1920×1080** (#328) | Login-window only, not selector-bound; unit-tested on both launch paths (`internal_chromium` viewport + `real_chrome --window-size`). Interactive Google sign-in is not automatable; no selector surface to regress. |
| **FIPS-safe SAPISIDHASH** (#329) | Digest verified byte-identical (`usedforsecurity=False` only affects FIPS gating); the live generations below authenticated and succeeded unchanged. |
| **Agentic image count enforcement** (#313) | Shipped with its own live regression test (commit 82667fb); the image path below generated the requested count cleanly. |

## E2e evidence — `gflow image t2i` (classic cohort)

- **Command:** `image t2i "a lighthouse on a rocky coast at sunset, photorealistic" --aspect 16:9 -n 1` · **exit 0** · `status: ok`.
- **File count:** 1 JPEG produced.
- **Magic bytes:** `ff d8` (valid JPEG).
- **Shape:** 1376×768 (16:9), 933 KB.
- **Structlog invariants:** `image_mode_entered → image_model_selected → aspect_ratio_set → count_setter_completed → prompt_submitted → batch_response_captured`.
- **Artifact:** `292e4153-…_1.jpg`.

## E2e evidence — `gflow video t2v` (veo-fast, classic cohort)

- **Command:** `video t2v "gentle waves lapping a tropical shore, aerial view" --aspect 16:9 --model veo-fast` · **exit 0** · `generation_status: MEDIA_GENERATION_STATUS_SUCCESSFUL`.
- **File count:** 1 MP4 produced.
- **Magic bytes:** `ftyp` container (valid MP4).
- **Shape:** 11.5 MB (landscape).
- **Structlog invariants:** `editor_ready → model_selected(veo_3_1_fast) → aspect_set(landscape) → output_count_set → generate_captured(HTTP 200) → poll_terminal(SUCCESSFUL) → video_saved`.
- **Artifact:** `97cea9f5-…​.mp4`.

## Result

Both live generation paths succeeded end-to-end on the v0.37.0 tree at the new 1920×1080
viewport, authentication (SAPISIDHASH) unchanged. Release gate **PASS**.
