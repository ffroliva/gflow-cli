# PR-A Implementation Plan — #299: video joins the UI-mode policy

> ## ✅ STATUS: PR-A **and** PR-B both SHIPPED in **v0.57.0** (2026-08-14)
>
> - **PR-A** — [#525](https://github.com/ffroliva/gflow-cli/pull/525), squash-merged as `1dacd9b`.
> - **PR-B** — [#527](https://github.com/ffroliva/gflow-cli/pull/527), squash-merged as `606dd7b`:
>   `mode_control.ensure_agent_mode` (real-click-first + `aria-pressed` verify, 15 s bounded
>   reload, unknown editor variants no-op); `_force_agent_mode` **deleted**.
>
> **This file is retained deliberately, not because it is in flight.** It is the only
> tracking home for the [deferred code-review findings](#deferred-code-review-findings-carry-into-pr-b--follow-ups)
> below, and the #299 issue thread links here. Every task checkbox is done; the deferred
> list is what remains.

> **For agentic workers:** one task at a time, failing test first. `/gflow:check` before every commit.
> Predict: **GO** w/ 7 conditions (all folded in below). Scenario: [SCENARIO.md](SCENARIO.md).
> **Frozen surfaces:** `src/gflow_cli/api/transports/drivers/factory.py` (zero diff) and all
> existing structlog event names.
> Successor: the `ensure_agent_mode` symmetry patch in mode_control.py was re-scoped by
> predict to CAUTION and shipped as its own PR (#527) — it was never part of PR-A.

**Goal:** `gflow video t2v`/`i2v` (CLI + MCP) go through the
live-verified `get_ui_driver` mode policy instead of a hardcoded classic bind, so an
agentic cohort flip fails fast pre-submit (exit 28, $0) instead of mid-flow (exit 23/25),
with `--ui-mode` exposed on the video commands (agentic honestly rejected at the CLI edge).

## Tasks

### T1 — DTO field
- [x] Failing test: `GenerateVideoRequest(ui_mode=UiMode.CLASSIC)` accepted, default `None` (`tests/api/` alongside existing video DTO tests; clone the image DTO test pattern).
- [x] `ui_mode: UiMode | None = None` on `GenerateVideoRequest` (api/video.py:210 block, mirror api/image.py:437).

### T2 — transport bind swap (the core)
- [x] Failing tests (clone tests/api/transports/drivers/test_ui_mode.py fake-page patterns): (a) classic detected → classic driver bound; (b) agentic-stuck → `UiModeUnavailableError` raised **before** any submit-path call; (c) request.ui_mode=None + env unset → classic-required; (d) env `agentic` → warning event + classic-required.
- [x] In `_generate_video_locked` (ui_automation_video.py:~3610-3624): delete the hardcoded `ClassicFlowUiDriver(transport=self)` + stale Task-2/Task-3 comment; **after** `await self._enter_editor(...)` (+ its overlay dismissal), resolve `base = request.ui_mode or resolve_ui_mode(None)`, clamp `AUTO→CLASSIC` and env-sourced `AGENTIC→structlog warn + CLASSIC` (no `infer_required_ui_mode` — `-i` is an image/agentic surface), then `ui_driver = await get_ui_driver(page, ui_mode=UiMode.CLASSIC, transport=self)`.
- [x] Verify every first-use of `ui_driver` sits after the new bind point (read the method top-to-bottom, don't assume).
- [x] Exit-28 message pin test: names the cohort server-assigned/possibly pinned (S9).

### T3 — CLI options
- [x] Failing tests (CliRunner): `--ui-mode classic` threads to the request; `--ui-mode agentic` → `UsageError` exit 2 **without** browser/profile work; `--ui-mode auto` accepted.
- [x] Video-specific `_ui_mode_option` in cli_video.py (own help text: classic/auto only meaningful today; no `-i` mention), applied to `t2v` + `i2v`; body-level agentic rejection (cli_image.py:827-830 pattern).
- [x] `gflow run`: **no flag** (env-only; image-batch precedent). No cli_run.py change.

### T4 — MCP parity (§61)
- [x] Failing tests: test_server.py schema includes `ui_mode` on `gflow_generate_video`; parity coverage for the ui_mode param (image AND video mappings — parity file has zero ui_mode coverage today); codec round-trip test for the video payload.
- [x] `ui_mode` param on `gflow_generate_video` (mcp/tools.py:920 block) cloning the image tool's membership validation (tools.py:784-788) **plus** agentic rejection mirroring T3's semantics (problem-details envelope).
- [x] worker/codec.py: decode `ui_mode` for video payloads (the :218 image-only decode is the documented silent-drop trap).

### T5 — docs + reconciliation
- [x] CHANGELOG `[Unreleased]`: behavior change (video mid-flow 23/25 → pre-submit 28), new flag/param, env back-compat warn.
- [x] USAGE.md (video section), CONFIGURATION.md `GFLOW_CLI_UI_MODE` (video semantics: auto≡classic, agentic=warn/reject).
- [x] Reconciliation note at the top of docs/superpowers/plans/2026-08-06-ui-mode-policy/PLAN.md: image-side tasks shipped v0.34.0–v0.38.1 (evidence in memory `issue-299-plan-reconciliation-ground-truth`); video leg superseded by this plan.

### T6 — gates
- [x] `check_repo_hygiene.py` + `check_doc_links.py`
- [x] `ruff check` + `ruff format --check`
- [x] `pyright src` (baseline: pre-existing errors in mcp/* + ui/app.py only — any delta blocks)
- [x] Scoped pytest: tests/api tests/cli tests/mcp tests/worker — green (see commit)

### T7 — live $0 gate
- [x] Drive the video editor bind on a real profile: N loads binding classic (assert `ui_driver.bound mode=classic` event, zero credits, abort pre-submit via route-abort or plain exit before generate).
  **Evidence (2026-08-14, profile `ffroliva`, project `e00291af…`, $0):** 3/3 real
  editor loads via `tmp/live_video_bind_check.py` — `detect_ui_mode` → `classic`,
  `ui_driver.ui_mode.attempt_exit_agent` → `ui_driver.bound mode=classic
  ui_mode=classic` fired on every load (bind 1.2–1.7 s), no prompt typed, no
  submit, zero credits.
- [x] If the cohort serves agentic during the runs: assert recovery-or-exit-28. If it never does: record deferred-with-reason (cohort is server-assigned; the branch is unit-locked).
  **Deferred-with-reason:** the cohort served classic on all 3 loads (server-assigned,
  cannot be forced from the client). The agentic-stuck → exit-28 branch is
  unit-locked by `TestVideoUiModePolicy::test_unreachable_arm_aborts_before_submit`.

### T8 — ship
- [x] Council branch review (YELLOW → fixed in f1848aa) + second review layer (`/code-review` xhigh, 15 verified findings → the in-scope ones fixed pre-merge: post-bind overlay re-dismissal, `UiModeUnavailableError` added to `RETRYABLE_ERRORS`, MCP ui_mode case-normalization + RFC 9457 envelope for both 400 branches, DTO-level agentic rejection as the every-producer backstop, doc-scope clauses).
- [x] PR #525 (`Refs #299`), SonarCloud green, squash-merge to develop. **Merged 2026-08-14 as `1dacd9b`; released in v0.57.0.**

### Deferred code-review findings (carry into PR-B / follow-ups)

> **PR-B shipped without taking any of these.** It was deliberately kept minimal
> (`ensure_agent_mode` only, per the predict CAUTION verdict), so every item below is
> still open after v0.57.0. Each carries its own trigger — none is scheduled work.

- `--ui-mode` on `video r2v`/`chain` — **still undecided**; currently env-only (documented
  as such in CONFIGURATION.md). PR-B did not address it. Note `r2v` *does* now run the
  policy (it shares `_generate_video_locked`); only the **flag** is absent.
- `submit_attempted` checkpoint is persisted before the transport's pre-submit bind → cancellation during the bind classifies indeterminate/possibly-charged despite $0; consider a `bind_completed` checkpoint (pre-existing coarseness, widened by this PR).
- #493 third-variant cohorts now pay the full 8 s detect poll before the same exit-23 cascade — a third-variant indicator collapses it (blocked on reporter diag).
  **Re-verified against the shipped v0.57.0 tree (2026-08-14):** the bind brings that
  cohort **no rescue at all**, only latency. `get_ui_driver(CLASSIC)` calls
  `ensure_media_mode(allow_reload=True)`, but the one sanctioned reload is gated on
  `persisted_off` (`mode_control.py:188`) — which requires a *real* toggle click on
  `AGENT_TOGGLE_SELECTOR` reading `aria-pressed="true"`. The #493 pill matches no known
  toggle selector, so nothing is clicked, `persisted_off` stays `False`, and **the reload
  never fires** — there is no cohort re-roll. Do not describe v0.57.0 as giving #493 a
  free re-roll. Net: identical exit 23, ~8 s slower, plus a new
  `mode_control.ensure_media_incomplete` warning that is itself a useful discriminator.
  Posted to the issue 2026-08-14.
- Cleanups: hoist a shared `ui_mode_option(help=...)` factory + one agentic-rejection message constant (3 hand-copies today); dedupe the four `_bind` monkeypatch stubs in TestVideoUiModePolicy; `pages.yml` pip cache (2 lines on setup-python).
- Verify dependabot's `uv` ecosystem actually opens a `/website` PR on its first scheduled run (silent no-op risk if uv support is lock-file-only).
- `detect_ui_mode`'s classic-on-timeout means "verifies" is best-effort (docs softened; a distinct timeout signal is factory territory → PR-B).
