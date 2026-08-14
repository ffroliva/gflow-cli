# PR-A Implementation Plan — #299: video joins the UI-mode policy

> **For agentic workers:** one task at a time, failing test first. `/gflow:check` before every commit.
> Predict: **GO** w/ 7 conditions (all folded in below). Scenario: [SCENARIO.md](SCENARIO.md).
> **Frozen surfaces:** `src/gflow_cli/api/transports/drivers/factory.py` (zero diff) and all
> existing structlog event names.
> Successor (separate PR, re-scoped by predict to CAUTION): `ensure_agent_mode` symmetry
> patch in mode_control.py — NOT this PR.

**Goal:** `gflow video t2v`/`i2v` (CLI + MCP) and `gflow run` video legs go through the
live-verified `get_ui_driver` mode policy instead of a hardcoded classic bind, so an
agentic cohort flip fails fast pre-submit (exit 28, $0) instead of mid-flow (exit 23/25),
with `--ui-mode` exposed on the video commands (agentic honestly rejected at the CLI edge).

## Tasks

### T1 — DTO field
- [ ] Failing test: `GenerateVideoRequest(ui_mode=UiMode.CLASSIC)` accepted, default `None` (`tests/api/` alongside existing video DTO tests; clone the image DTO test pattern).
- [ ] `ui_mode: UiMode | None = None` on `GenerateVideoRequest` (api/video.py:210 block, mirror api/image.py:437).

### T2 — transport bind swap (the core)
- [ ] Failing tests (clone tests/api/transports/drivers/test_ui_mode.py fake-page patterns): (a) classic detected → classic driver bound; (b) agentic-stuck → `UiModeUnavailableError` raised **before** any submit-path call; (c) request.ui_mode=None + env unset → classic-required; (d) env `agentic` → warning event + classic-required.
- [ ] In `_generate_video_locked` (ui_automation_video.py:~3610-3624): delete the hardcoded `ClassicFlowUiDriver(transport=self)` + stale Task-2/Task-3 comment; **after** `await self._enter_editor(...)` (+ its overlay dismissal), resolve `base = request.ui_mode or resolve_ui_mode(None)`, clamp `AUTO→CLASSIC` and env-sourced `AGENTIC→structlog warn + CLASSIC` (no `infer_required_ui_mode` — `-i` is an image/agentic surface), then `ui_driver = await get_ui_driver(page, ui_mode=UiMode.CLASSIC, transport=self)`.
- [ ] Verify every first-use of `ui_driver` sits after the new bind point (read the method top-to-bottom, don't assume).
- [ ] Exit-28 message pin test: names the cohort server-assigned/possibly pinned (S9).

### T3 — CLI options
- [ ] Failing tests (CliRunner): `--ui-mode classic` threads to the request; `--ui-mode agentic` → `UsageError` exit 2 **without** browser/profile work; `--ui-mode auto` accepted.
- [ ] Video-specific `_ui_mode_option` in cli_video.py (own help text: classic/auto only meaningful today; no `-i` mention), applied to `t2v` + `i2v`; body-level agentic rejection (cli_image.py:827-830 pattern).
- [ ] `gflow run`: **no flag** (env-only; image-batch precedent). No cli_run.py change.

### T4 — MCP parity (§61)
- [ ] Failing tests: test_server.py schema includes `ui_mode` on `gflow_generate_video`; parity coverage for the ui_mode param (image AND video mappings — parity file has zero ui_mode coverage today); codec round-trip test for the video payload.
- [ ] `ui_mode` param on `gflow_generate_video` (mcp/tools.py:920 block) cloning the image tool's membership validation (tools.py:784-788) **plus** agentic rejection mirroring T3's semantics (problem-details envelope).
- [ ] worker/codec.py: decode `ui_mode` for video payloads (the :218 image-only decode is the documented silent-drop trap).

### T5 — docs + reconciliation
- [ ] CHANGELOG `[Unreleased]`: behavior change (video mid-flow 23/25 → pre-submit 28), new flag/param, env back-compat warn.
- [ ] USAGE.md (video section), CONFIGURATION.md `GFLOW_CLI_UI_MODE` (video semantics: auto≡classic, agentic=warn/reject).
- [ ] Reconciliation note at the top of docs/superpowers/plans/2026-08-06-ui-mode-policy/PLAN.md: image-side tasks shipped v0.34.0–v0.38.1 (evidence in memory `issue-299-plan-reconciliation-ground-truth`); video leg superseded by this plan.

### T6 — gates
- [ ] `check_repo_hygiene.py` + `check_doc_links.py`
- [ ] `ruff check` + `ruff format --check`
- [ ] `pyright src` (baseline: pre-existing errors in mcp/* + ui/app.py only — any delta blocks)
- [ ] Scoped pytest: tests/api/transports/ tests/mcp/ tests/test_cli_video* (+ CliRunner files touched)

### T7 — live $0 gate
- [ ] Drive the video editor bind on a real profile: N loads binding classic (assert `ui_driver.bound mode=classic` event, zero credits, abort pre-submit via route-abort or plain exit before generate).
- [ ] If the cohort serves agentic during the runs: assert recovery-or-exit-28. If it never does: record deferred-with-reason (cohort is server-assigned; the branch is unit-locked).

### T8 — ship
- [ ] Council branch review + second review layer; apply findings.
- [ ] PR (`Refs #299`, not `Closes` — PR-B still open), SonarCloud green, squash-merge to develop.
