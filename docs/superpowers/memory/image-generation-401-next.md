---
name: image-generation-401-next
description: "RESOLVED for `gflow image t2i` in v0.7.0 (2026-05-20) via ui_automation transport. Scope clarification (2026-05-22): the verification only covered t2i; batch was not exercised and has a separate same-project-transport defect captured in [[batch-submission-cadence]]."
---

**Status: RESOLVED in v0.7.0 (2026-05-20) for `gflow image t2i` only.** Kept as a historical breadcrumb.

**Scope caveat added 2026-05-22:** v0.7.0's live verification (`docs/LIVE_VERIFICATION_v0.7.0.md`) covered four aspect ratios of *single-image* generation — `gflow image t2i`. It did NOT exercise `gflow image batch` or the `--same-project=1` code path. A separate transport-level defect (the `ui_automation.generate_images` method discards the caller's `project_id`) was discovered 2026-05-22 during the multi-image-prompt branch's jitter matrix. That defect is documented in [[batch-submission-cadence]]. This entry remains historical and accurate for what it claimed; the gap is in scope, not correctness.

**Second latent defect discovered 2026-05-23 (now also closed):** the image transport had no equivalent of the video side's `_switch_to_video_mode`. v0.7.0's four-aspect matrix on `ffroliva` happened back-to-back so the editor stayed in image mode across all four runs and the asymmetry was invisible. Hit immediately today when a `gflow image t2i` on `ffroliva` after an unrelated video session silently generated a video. Closed by PR #40. See [[image-video-mode-switch-symmetry]] for the invariant.

Original problem: image **generation** via `aisandbox-pa.googleapis.com` returned HTTP 401, while `verify_flow_session`, `createProject`, and `health_check` succeeded. Tracked in `KNOWN_ISSUES.md` (then under `## Open`).

**Why:** The aisandbox-pa HTTP transport (`evaluate_fetch` / `bearer` / `sapisidhash`) was blocked by Google's session-auth mismatch. Moving image generation to `UiAutomationTransport` — which drives the Flow web UI so Flow's own JS mints the token and POSTs the request — bypasses the 401 by piggy-backing on the live editor session.

**How to apply (going forward):** If a 401 appears again on `aisandbox-pa`, treat it as a regression on the *HTTP* transports (the experimental ones under `src/gflow_cli/api/transports/experimental/`). `ui_automation` is the production path and is now end-to-end verified — see `docs/LIVE_VERIFICATION_v0.7.0.md` for the four aspect ratios (`9:16`, `16:9`, `1:1`, `4:3`) that landed images on disk in v0.7.0. Do NOT reopen this entry without first confirming the `ui_automation` listener log keys (`ui_automation.batch_response_seen`, `…_dropped_project_id_mismatch`) — those eliminate the silent-listener-miss black-hole that hid the bug before.

**Closure pointers:**
- KNOWN_ISSUES.md entry should be moved to `## Mitigated` with a pointer to v0.7.0.
- `feat/ui-automation-onboarding-bypass` → merged via PR #27 on 2026-05-19.
- Phase A T2V (PR #23) and v0.7.0 release (PR #32) confirmed the bypass holds in production.

See also: [[phase-b-followups]], [[branch-workflow]], [[video-generation-spec]].
