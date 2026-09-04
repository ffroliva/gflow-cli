---
name: video-generation-spec
description: "Phase A (T2V) shipped in PR #23 (merged 2026-05-19); Phase B I2V/R2V open. Spec at docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md."
---

**Status: PR #23 merged 2026-05-19** (commit `b50e147`). Phase A (T2V) live on `develop` and shipped in v0.7.0. I2V / R2V are open (`NotImplementedError` raised) — see [[phase-b-followups]] items 4 and 5.

Design spec written and committed on 2026-05-18 to:
`docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md`

Branch: `feat/ui-automation-onboarding-bypass`
Commit: `f71f962`

**Why:** All `aisandbox-pa.googleapis.com` generation endpoints return 401 for HTTP transports — confirmed e2e for both `batchGenerateImages` and `batchAsyncGenerateVideoText`. `UiAutomationTransport` is the only working path.

**What the spec covers:**
- Three video modes confirmed from HAR (labs.google8–10.har):
  - T2V: `veo_3_1_t2v_{tier}_{aspect}`, no image inputs
  - I2V Frames: `veo_3_1_interpolation_lite`, `START_IMAGE`/`END_IMAGE` inputs
  - R2V Elementos: `veo_3_1_r2v_lite`, `ASSET_IMAGE` array inputs
- Domain changes: extend `GenerateVideoRequest` with `start_frame_id`, `end_frame_id`, `reference_image_ids`; add `Mode.R2V`; update `model_key()` and `build_generate_body()`
- Transport: `generate_video()` + `_upload_asset()` (via `page.request.post`) + video mode UI navigation + response capture + polling
- Two attachment strategies (both to be implemented and verified e2e, weaker one removed after verification)

**Council review outcome (2026-05-18):** 5-dimension review — verdict **Needs-rework, do not plan yet**. Architecture and Security/Scope: Approve-with-changes. Wire-format, Testability, Robustness: Needs-rework.

**Spec finalized via 4 council rounds — consensus reached (2026-05-18).** Sanitized wire captures are committed to `samples/captured/` (02 redacted; 08-11 new); the design spec was revised 4 times against the captures + the real transport code, and re-reviewed each round by a 4-dimension LLM council until all reviewers approved with no blockers. Branch `chore/video-wire-captures` off `feat/ui-automation-onboarding-bypass`; spec at `docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md`, status "Revised (rev 4)".

Key facts the spec settled: each video mode has its own endpoint; `UiAutomationTransport` drives the Flow UI — Flow's JS builds/sends the request and mints reCAPTCHA, so the transport never POSTs a body; the 401-dead HTTP video path (`build_generate_body`/`model_key`/`client.generate_video`/`VideoOperation`) is retired by this work.

**How to apply:** PR #23 (`chore/video-wire-captures` → `feat/ui-automation-onboarding-bypass`) carries the captures + consensus spec + the Phase 0 plan. Next — run the Phase 0 spike per `docs/superpowers/plans/2026-05-18-video-phase0-submit-spike.md` (operator-run: needs a live Flow login and spends Veo credits; builds `scripts/smoke_video_editor.py`). It answers spec §10.2 Q1/Q3/Q5/Q6/Q7; then Phase A (T2V) can be planned. See [[branch-workflow]] for PR process.

**Council consensus on the 4 open questions:** drop Strategy B (ship UI-driven Strategy A only — B "fails silently"); use active polling not passive; deprecate `start_asset_uuid` with a warning; add a credit-cost guard (warn before submit). Other must-fixes: `ui_automation.py` already exceeds the 800-line cap so video logic needs its own module; `VideoStatus` type is referenced but undefined; `FAILED → ContentPolicyError` mapping is wrong (conflates quota/server errors).
