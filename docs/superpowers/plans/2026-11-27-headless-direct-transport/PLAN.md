# Headless Direct Transport Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature headless-direct-transport` to find the
> next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** In release v0.108.0, images and videos of the measured shapes are generated without a
visible browser: text-to-image, the measured image-to-image shape, text-to-video (including a
bare `gflow video t2v` when the account's selected model resolves to a measured key) and
image-to-video (start frame) at measured keys, and 1080p video upscale go over direct HTTP on
flow.google.com, and the browser is used only to mint the reCAPTCHA token, headless. A submit Flow
refuses as `UNUSUAL_ACTIVITY` (not billed) is retried once with a token from a fresh headless
browser. Every other surface stays on the UI transport, launched headed, with its route decided
before launch.

**Principle:** the browser is used only where strictly necessary (minting the token) and runs
headless. A visible window opens only for a surface that still needs the Flow UI, or when the user
sets `--browser-visibility headed`.

**Architecture:** Two pure modules carry the domain: `api/submit_policy.py` (visibility, submit
outcome, reply classification, the single retry step, the re-arm rule, the `RecaptchaTokenSource`
port) and `api/generation_route.py` (the measured-shape allowlists and the direct-or-UI route,
decided before the browser launches). `api/transports/direct_http.py` becomes a package at the
same import path (session, mint adapter, batchexecute gateway, images, video, upscale, parse,
router), and `FlowApiClient` gains `relaunch(visibility)`, which drains in-flight work and keeps
the profile lease. The retry loop lives once, in `api/_flow_retry.py`, called from
`GenerationSurface` and `MediaEditSurface`, so CLI, MCP and the worker need no call-site retry
code. Warmth is per process: the armed page is reused for the life of one `FlowApiClient` and
released with it; decision D3 (no lease held while idle between commands) is unchanged.
`ui_automation` remains the kill switch (`GFLOW_CLI_TRANSPORT=ui_automation`).

**Predict verdict:** CAUTION — confidence 6/10 overall, conditional on the warm page being
limited to one process (the cross-command warm page was STOP and is deferred). The
pre-implementation analysis's required mitigations are carried below, except where
[SCENARIO.md](SCENARIO.md) § "Decisions this analysis assumes" and the Decision log record a
different decision. Two reviews of this plan (review A, correctness; review B, simplicity) are
folded in; each change is logged with its reason.

**Preconditions (not tasks):**

- PR #1552 (#1549, `HARBOR_SEAL` and `GEM_PIX_2` on the direct image path) and PR #1553 (#1550,
  catalog cross-check) are merged to `develop` and this branch is rebased onto them before Task 2.
  Both edit code this plan moves; this plan does not redo either.
- The spike document's section "The default video shape over direct HTTP" feeds Tasks 4, 14, 28
  and 29: the default t2v sends no model, so its key is the account's last picker selection; the
  default i2v binds Veo 3.1 Lite (`veo_3_1_i2v_lite`, 9:16). Both captured default frames were
  accepted direct and headless. The app's t2v frame carries a trailing `[4]` duration slot and a
  batch tail of `1` where the spike's hand-built frame had none and `2`. The section "Headless UI
  automation" is evidence for #1554 (headless UI transport, next release), not for this plan.

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| Critical | A billed submit is classified as refused, or as not sent, and resent | Outcome is decided by phase: once the billed POST's bytes are sent, every later failure (reply, poll, read, download, cookie read) is `Indeterminate`; `RefusedNotBilled` only for HTTP 200 + an error frame for the submit rpcid carrying `UNUSUAL_ACTIVITY` + no record; mutation-tested (Tasks 3, 9, 26) |
| Critical | Stacked retry ladders submit three times | The post-send 3xx/401/403 UI fallback in `_send_once` is removed; one retry, at most once per request (Tasks 9, 13) |
| Critical | `--max-credits` fails closed (exit 45) or is bypassed on direct video, which has no wire quote | Every credit-capped video routes to the UI path, where the quote is read; the worker routes after it resolves the effective cap (Tasks 4, 12) |
| Critical | The relaunch closes the context under an accepted job's poll or download | `relaunch()` blocks new starts and drains every in-flight POST, poll and download, including `client.download` through `page.request` (Tasks 10, 11, 13) |
| Critical | A UI fallback after a not-sent direct attempt bills without passing the cancel gate | The loop emits `submit_attempted` through the #1198 gate before the UI submit (Task 13) |
| Critical | A cancellation raised by the submit gate is swallowed and turned into a fallback | Exceptions from `on_before_submit` propagate unchanged (Task 13) |
| High | The retry lands in the same refusal window | One retry only, from a new browser process (the only fresh-page arm the spike measured accepted after a refusal); a second refusal is final, not charged, and its remediation names `--browser-visibility headed` and the cooldown in `docs/ACCOUNT_SAFETY.md` (Tasks 3, 8, 13) |
| High | The UI path runs on a refused or headless page | Route decided before launch; a mixed run launches headed; every fallback or retry uses a new page; the transport never calls the UI path in place (Tasks 4, 12, 13) |
| High | The relaunch releases the lease and another process takes the profile mid-retry | `relaunch()` keeps the same `ProfileLease`; a failed relaunch ends in a final not-charged refusal and the lease is released only in `__aexit__` (Task 11) |
| High | `WafRejectionError` tells an agent to retry into a refusal window | Terminal refusal raised with `charged="no"`, `retryable=False`, `next_action="none"` (Task 8) |
| High | Concurrency opens an account-level refusal window | Mint-only lock and at most 4 submits in flight per process and profile; `GFLOW_CLI_CONCURRENCY` cannot raise it; a log-only warning when more than 8 submits start within 120 s on one profile. 8 in flight measured clean twice; the one burst refusal did not reproduce, was not billed and is retried once (Task 10) |
| High | A page left idle, resumed from suspend, or refused once mints refused tokens | Re-arm on a wall-clock gap since the last mint (monotonic time is not used); any refusal discards that page (Task 10) |
| High | `submit_attempted` written before the mint makes cancel and recovery report a refused job as possibly charged | Checkpoint written immediately before the POST; a refusal writes `submit_refused` (Tasks 8, 13) |
| High | Unmeasured video keys, Omni i2v at 9:16, end frames or ingredients sent direct | Direct only for an exact measured tuple; the allowlist grows only with a live run (Task 4) |
| High | Bare t2v guesses a key that is per-account picker state | The key is built from the account's selected model as read (Tasks 28, 29); an unread or unmeasured selection goes to the UI path |
| Medium | A headed launch (explicit `headed`, or a UI-bound shape) on Linux without a display misreports as `ProfileLockedError` | Display check before any headed launch (Task 11) |
| Medium | macOS headless is unmeasured | Documented as unmeasured; the retry is the mitigation; e2e names the missing hardware as its blocker (Tasks 23, 24) |
| Medium | Docs keep claiming headless is rejected, or the README and its translation drift from the doc | One docs task rewrites every claim; the per-command table lives once and an offline test compares the three copies (Tasks 22, 23) |
| Low | Token or `f.sid` text leaks through an httpx exception | Redaction widened (Task 7) and applied to exception text (Task 9) |

---

## File structure

### New files

```
src/gflow_cli/api/submit_policy.py
  Pure domain: BrowserVisibility, VisibilitySetting, SubmitOutcome (NotSent | Accepted |
  RefusedNotBilled | Indeterminate), classify_reply, RetryStep + next_step, needs_rearm
  (wall-clock gap), MAX_IN_FLIGHT, starts_exceed, the RecaptchaTokenSource Protocol.
  No Playwright, httpx or I/O.
src/gflow_cli/api/generation_route.py
  Pure domain: Route (DIRECT | UI), the measured allowlists (image wire models, video key tuples,
  upscale source keys), choose_image_route / choose_video_route / choose_upscale_route,
  plan_visibility(setting, routes), and (Task 29) the selected-model-to-key mapping.
src/gflow_cli/api/_flow_retry.py
  Application: the one retry loop (run, classify, next_step, relaunch, retry once), the checkpoint
  and gate calls around a refusal and a UI fallback.
src/gflow_cli/api/transports/direct_http/__init__.py
  Re-exports every name other code or tests read; the import path stays stable.
src/gflow_cli/api/transports/direct_http/_session.py
  Live-browser cookie headers, user agent, shell tokens (at / f.sid / bl); (Task 29) the selected
  video model read.
src/gflow_cli/api/transports/direct_http/_mint.py
  PageTokenSource(page, visibility) implementing RecaptchaTokenSource: the mint-only lock, the
  retry budget, re-arm and discard on a new page.
src/gflow_cli/api/transports/direct_http/_gateway.py
  batchexecute request builder (source-path parameter), write and read rpcid allowlists, the
  phase-tracked attempt (sent flag) returning a SubmitOutcome, the in-flight semaphore, quiesce(),
  the start-rate warning.
src/gflow_cli/api/transports/direct_http/_images.py
  run_direct_images, run_direct_upload.
src/gflow_cli/api/transports/direct_http/_video.py
  run_direct_t2v (YhhmEf), run_direct_i2v (maseQ + eb1hJf), jwpduf poll, as29s read, download.
src/gflow_cli/api/transports/direct_http/_upscale.py
  run_direct_upscale_1080p (p0UkFb, poll <workflow>_upsampled, as29s, mp4 dimension check).
src/gflow_cli/api/transports/direct_http/_parse.py
  Exact-id record finder (handles <uuid>_upsampled), "exists = carries a media id", as29s
  original-mp4 URL extraction.
src/gflow_cli/api/transports/direct_http/_transport.py
  DirectHttpTransport: routes each request with generation_route and raises
  DirectAttemptEnded(outcome) to the retry loop instead of falling back in place.
scripts/dev/spike_selected_video_model.py
  Task 28 spike: where the account's selected video model can be read without a click.
docs/superpowers/spikes/2026-11-27-selected-video-model.md
  Task 28 findings.
docs/BROWSER_MODES.md
  The per-command table (the single source), route and retry rules, a Mermaid diagram, platform
  status, how to force headed, the kill switch.
tests/api/test_submit_policy.py
tests/api/test_generation_route.py
tests/api/test_flow_retry.py
tests/api/test_client_relaunch.py
tests/api/transports/test_direct_http_contract.py
tests/api/transports/test_direct_http_gateway.py
tests/api/transports/test_direct_http_mint.py
tests/api/transports/test_direct_http_video.py
tests/api/transports/test_direct_http_upscale.py
tests/test_domain_import_graph.py
tests/test_cli_routing.py
tests/docs/test_browser_modes_table.py
tests/docs/test_no_stale_headless_claims.py
tests/features/direct_routing.feature
tests/features/test_direct_routing_steps.py
tests/e2e/test_direct_video_e2e.py
tests/e2e/test_direct_upscale_e2e.py
tests/e2e/test_refusal_retry_e2e.py
tests/e2e/test_direct_video_mcp_e2e.py
tests/e2e/headless_direct.feature
tests/e2e/test_headless_direct_bdd.py
```

### Modified files

```
src/gflow_cli/api/transports/direct_http.py   -> becomes the package above (Task 2)
src/gflow_cli/api/transports/__init__.py      default transport direct_http; ui_automation stays a production choice
src/gflow_cli/api/client.py                   visibility replaces headless; relaunch(visibility); display check
src/gflow_cli/api/_flow_session.py            headless field -> visibility
src/gflow_cli/api/_flow_generation.py         images (Task 13) and video (Task 14) call the retry loop
src/gflow_cli/api/_flow_media_edit.py         upsample_video routes 1080p direct when measured (Task 15)
src/gflow_cli/api/dto.py                      GenerationPhase gains "submit_refused"
src/gflow_cli/api/recaptcha.py                remediation hints name GFLOW_CLI_BROWSER_VISIBILITY
src/gflow_cli/paths.py                        image_dimensions(head) for png / jpeg / gif / webp, stdlib only
src/gflow_cli/config.py                       browser_visibility setting; GFLOW_CLI_HEADLESS deprecated alias
src/gflow_cli/errors.py                       waf_refusal_final() raise helper (no new class, no EXIT_CODE_MAP edit)
src/gflow_cli/data/redaction.py               token prefixes beyond 0cAF
src/gflow_cli/data/recorder.py, data/_recording_image.py, video recording   route + visibility fields
src/gflow_cli/json_output.py                  route, visibility, retried in --json
src/gflow_cli/services/doctor.py              env.deprecated_vars names GFLOW_CLI_HEADLESS
src/gflow_cli/worker/daemon.py, worker/queue.py   submit_refused -> claimed; route after the effective cap
src/gflow_cli/worker/codec.py                 browser_visibility payload key (image, video, upscale)
src/gflow_cli/_cli_helpers.py                 client_visibility(); --browser-visibility option
src/gflow_cli/cli_*.py, image_batch.py, chain.py, cli_movie.py, mcp/tools.py, mcp/character_tools.py,
  services/*.py                               route-aware client construction (Task 12)
README.md, README.zh-CN.md                    purpose lead and limitations (Task 30); the per-command table (Task 22)
AGENTS.md, docs/ARCHITECTURE.md (history note), docs/ACCOUNT_SAFETY.md, KNOWN_ISSUES.md,
  skills/gflow-cli/SKILL.md, pyproject.toml comments   documentation honesty (Task 30)
reference docs listed in Task 23
```

### Wave map and file ownership (parallel dispatch)

No two tasks in one wave edit the same file. A file shared across waves has one owner per wave.

| Wave | Tasks | Files each task owns in that wave |
|---|---|---|
| 1 | 1, 3, 4, 5, 6, 7, 8, 20, 28 | 1: `test_direct_http_contract.py` · 3: `submit_policy.py`, `test_domain_import_graph.py` · 4: `generation_route.py` · 5: `config.py`, `doctor.py`, `recaptcha.py`, `.env.template` · 6: `paths.py` · 7: `data/redaction.py` · 8: `errors.py`, `dto.py`, `worker/daemon.py`, `worker/queue.py` · 20: `tests/features/direct_routing*` · 28: `scripts/dev/spike_selected_video_model.py`, its spike doc |
| 2 | 2 | the `direct_http` package (all modules) |
| 3 | 9, 11 | 9: `_gateway.py`, `_session.py`, `_images.py` · 11: `client.py`, `_flow_session.py` |
| 4 | 10 | `_mint.py`, `_gateway.py`, `_images.py` |
| 5 | 13 | `_flow_retry.py`, `_flow_generation.py`, `_transport.py` |
| 6 | 14, 15 | 14: `_video.py`, `_parse.py`, `frame_builder.py`, `_transport.py`, `_flow_generation.py` · 15: `_upscale.py`, `_flow_media_edit.py` |
| 7 | 12, 17, 29 | 12: `transports/__init__.py`, `_cli_helpers.py`, `cli_*.py`, `image_batch.py`, `chain.py`, `cli_movie.py`, `mcp/tools.py`, `mcp/character_tools.py`, `services/*.py`, `worker/daemon.py` · 17: `data/recorder.py`, `data/_recording_*.py`, `json_output.py`, `_flow_retry.py` · 29: `generation_route.py`, `_session.py`, `_video.py` |
| 8 | 18 | `_cli_helpers.py`, `cli_image.py`, `cli_video.py`, `cli_run.py` |
| 9 | 19 | `mcp/tools.py`, `worker/codec.py`, `tests/mcp/*` |
| 10 | 23 | `docs/BROWSER_MODES.md`, `docs/CONFIGURATION.md`, `docs/DIRECT_TRANSPORT.md`, `docs/MCP.md`, `docs/ERRORS.md`, `docs/USAGE.md`, `docs/INDEX.md`, `PLAN.md`, `CHANGELOG.md`, `website/docs/` |
| 11 | 30, 25 | 30: `README.md`, `README.zh-CN.md`, `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/ACCOUNT_SAFETY.md`, `KNOWN_ISSUES.md`, `skills/gflow-cli/SKILL.md` + plugin copy, `pyproject.toml`, `website/docs/`, `tests/docs/test_no_stale_headless_claims.py` · 25: source files named by review findings (never docs, README, `pyproject.toml`) |
| 12 | 22 | `README.md`, `README.zh-CN.md`, `tests/docs/test_browser_modes_table.py` |
| 13 | 24, 26 | no source edits (ledger and PR text) |
| 14 | 27 | — |

Tiers: **mechanical** (moves, renames, one-rule helpers: a fast model), **standard** (one module
plus tests), **complex** (wire, concurrency, lease or money rules: a frontier model with extended
reasoning). Every worker brief repeats: long commands run with `run_in_background`, never kill a
process by name, leases before browsers, and committed text is formal and neutral (public
repository).

**E2E policy (per task):** each Flow-touching task runs its e2e test **headless once on one
profile** (ffroliva or denon82). The refusal-retry e2e (Task 13) also has a headed variant that
runs only under an explicit `GFLOW_CLI_BROWSER_VISIBILITY=headed`. The full matrix, headless and
headed on both ffroliva and denon82, runs once in Task 24.

Each task starts with its red tests. A task's commit lands green; where a task splits into a red
commit and a green commit, the red commit message carries the failing line and the green commit
follows directly.

---

## Task 1 — Golden master for `direct_http` (test scaffold)

**Tier:** mechanical · **Wave:** 1 · **Depends on:** —

**What:** Pin every name and signature of `direct_http.py` that the package split must preserve,
and record the baseline metrics.

**Files:**
- `tests/api/transports/test_direct_http_contract.py`, as `test_migrated_composer_contract.py`
  does.

**Steps:**
- [ ] List every name read from `gflow_cli.api.transports.direct_http` by `src/` and `tests/`
  (registry import `transports/__init__.py:46`, the `_flow_generation.py:585` string compare, and
  the test patch targets `build_ogiz0b_body`, `build_maseq_body`, `read_project`, `_MINT_RETRY_S`,
  `_MINT_BUDGET_S`, `_CHARACTER_READ_S`, `_WIRE_MODEL`, `direct_can_serve`, `run_direct_images`,
  `run_direct_upload`, `DirectHttpTransport`).
- [ ] Pin the names and the `inspect.signature` of each callable.
- [ ] Prove it can fail: delete one import from `direct_http.py`, watch the test go red, revert.
- [ ] Record the baseline: `radon cc` plus coverage per function of `direct_http.py`, `client.py`
  and `_flow_generation.py` (CRAP table) in the PR description draft.

**Tests created (passing on develop by design):**
- [ ] `test_direct_http_reexports_every_name_callers_read`
- [ ] `test_direct_http_signatures_unchanged`

---

## Task 2 — Split `direct_http.py` into a same-path package (move only)

**Tier:** mechanical · **Wave:** 2 · **Depends on:** 1, 4, PR #1552 merged

**What:** Move code into `direct_http/` along session / mint / gateway / images / transport
without changing a body, an error or a log event; `direct_can_serve` now comes from
`generation_route` (Task 4) and is re-exported.

**Files:**
- `src/gflow_cli/api/transports/direct_http/{__init__,_session,_mint,_gateway,_images,_transport}.py`
- `tests/api/transports/test_direct_http.py` (patch targets follow their code; the list goes in
  the commit message)

**Steps:**
- [ ] Move `_session_headers`, `_shell`, `_proxy_configured` to `_session.py`; `_mint` to
  `_mint.py`; `_post_request`, `_send_once`, `_client`, `_reconcile_error`,
  `_DirectFallbackError` to `_gateway.py`; `run_direct_images`, `run_direct_upload`,
  `_resolve_character` to `_images.py`; `DirectHttpTransport` to `_transport.py`.
- [ ] Delete `direct_can_serve` and its helpers from the moved code; `__init__.py` re-exports
  `generation_route.direct_can_serve` and every other name Task 1 pins.
- [ ] `uv run python scripts/ci/check_size.py` green.

**Tests:**
- [ ] Task 1's contract test green, unchanged.
- [ ] `tests/api/transports/test_direct_http.py` and `tests/features/test_direct_http_transport_steps.py`
  green with patch targets moved and no assertion edited.

---

## Task 3 — Domain: visibility, submit outcome, retry step, re-arm rule

**Tier:** complex · **Wave:** 1 · **Depends on:** —

**What:** One pure module whose types make the illegal states unrepresentable and whose functions
hold every money and refusal rule.

**Files:**
- `src/gflow_cli/api/submit_policy.py`
- `tests/api/test_submit_policy.py`, `tests/test_domain_import_graph.py`

**Steps:**
- [ ] `BrowserVisibility` (`HEADLESS`, `HEADED`); `VisibilitySetting` (`AUTO`, `HEADLESS`,
  `HEADED`).
- [ ] `SubmitOutcome` as frozen dataclasses: `NotSent(reason, auth: bool)` (no byte of the billed
  POST left the process), `Accepted(body)`, `RefusedNotBilled(rpcid, reason)`,
  `Indeterminate(rpcid, cause, status)`. No variant carries both a body and a refusal.
- [ ] `classify_reply(rpcid, status, body) -> SubmitOutcome`: `RefusedNotBilled` only for HTTP 200,
  an `RpcError` frame for `rpcid` whose reasons contain `UNUSUAL_ACTIVITY`, and no generation record
  in the same body (reuse `batchexecute` frame parsing). A bare status (`[5]`), another reason, an
  unparseable body, 3xx, 401, 403, 429 and 5xx are `Indeterminate` (or the existing typed terminal
  error for a named policy reason).
- [ ] `next_step(outcome, *, setting, retried) -> RetryStep` with
  `RetryStep` = `Done | RetryDirect(visibility) | FallbackUi(visibility) | Stop(kind)`:
  `RefusedNotBilled`, not yet retried → `RetryDirect(launch_visibility(setting))`, which is
  HEADLESS under `AUTO` and an explicit `HEADLESS`, and HEADED only under an explicit `HEADED`;
  refused again → `Stop(waf_final)`; `Indeterminate` → `Stop(reconcile)` always;
  `NotSent(auth=True)` → `Stop(reauth)`; other `NotSent` → `FallbackUi(HEADED)` under `AUTO` /
  `HEADED`, `FallbackUi(HEADLESS)` under an explicit `HEADLESS` (the UI path stays headed in
  v0.108.0 unless the user chose otherwise).
- [ ] `needs_rearm(*, last_mint_wall, now_wall) -> bool`: the wall-clock gap since the last mint
  exceeds 600 s (a design value; covers suspend because wall time includes it).
- [ ] `MAX_IN_FLIGHT = 4`; `starts_exceed(history, now, *, limit=8, window_s=120) -> bool` (a
  warning signal only; nothing blocks on it).
- [ ] `RecaptchaTokenSource` Protocol: `async def mint(self, action: str) -> str`.

**Tests created (red first):**
- [ ] `test_refusal_requires_http_200_error_frame_reason_and_no_record` (row 1)
- [ ] `test_bare_status_reply_is_indeterminate_not_refused` (row 4)
- [ ] `test_3xx_401_403_after_send_are_indeterminate` (row 4)
- [ ] `test_timeout_after_send_is_indeterminate` (row 3)
- [ ] `test_refused_auto_retries_once_headless` (row 1)
- [ ] `test_refused_retry_is_headed_only_under_explicit_headed` (rows 1, 22)
- [ ] `test_refused_after_retry_stops_waf_final` (row 2)
- [ ] `test_indeterminate_never_retries_in_any_state` (rows 3, 4; parametrised over every setting ×
  retried combination)
- [ ] `test_not_sent_auth_stops_reauth_without_retry` (row 19)
- [ ] `test_not_sent_falls_back_to_ui_headed_under_auto` (row 7)
- [ ] `test_rearm_on_wall_clock_gap_with_monotonic_unchanged` (row 16, clock injected)
- [ ] `test_starts_exceed_true_for_the_ninth_start_in_120s` (row 14)
- [ ] `test_submit_policy_and_generation_route_import_no_playwright_or_httpx`

---

## Task 4 — Domain: route predicate and measured allowlists

**Tier:** standard · **Wave:** 1 · **Depends on:** —

**What:** Decide direct or UI from the request shape and settings, before any browser launches.
Writes `generation_route.py` and its test only.

**Files:**
- `src/gflow_cli/api/generation_route.py`, `tests/api/test_generation_route.py`

**Steps:**
- [ ] `choose_image_route(request, env)`: the current `direct_can_serve` rules (copied here as
  `direct_can_serve`; Task 2 deletes the original and re-exports this one), plus env: flow host
  `labs.google` → UI; proxy configured → UI.
- [ ] `choose_video_route(request, env)`: first normalise an i2v `model=None` to
  `I2V_DEFAULT_MODEL` (Veo 3.1 Lite, `api/video.py:93`), which is what the UI binds. DIRECT only
  when `mode` is T2V or I2V with a start frame only, `count == 1`, the effective `max_credits` is
  `None` (the caller passes the value it resolved from the request and `GFLOW_CLI_MAX_CREDITS`),
  and the exact `(model, mode, duration, resolution, aspect)` tuple is in `MEASURED_VIDEO_KEYS`,
  which maps it to the wire key and aspect slot. Seed, one row per measured run:
  `(OMNI_1_2_FLASH, T2V, 4, "360p", 16:9)` and `(…, 9:16)` → `macaron_cookie_t2v_4s_360p`;
  `(OMNI_1_2_FLASH, I2V, 4, "360p", 16:9)` → `macaron_cookie_i2v_4s_360p`;
  `(VEO_3_1_LITE, T2V, None, None, 16:9)` → `veo_3_1_t2v_lite`;
  `(VEO_3_1_LITE, I2V, None, None, 9:16)` → `veo_3_1_i2v_lite` (the default `gflow video i2v`
  shape). End frame, ingredients, references, characters, extend, unlisted aspects → UI.
- [ ] A t2v `model=None` routes UI in this task; Task 29 replaces that rule with a key resolved
  from the account's selected model. An Omni tuple with `duration` or `resolution` left `None` is
  unlisted, because the key names both. No key enters the table without a live run.
- [ ] `choose_upscale_route(scale, catalog_row)`: DIRECT only for `1080p` and a source found in
  the local catalog with its `workflow_id`, aspect and a model key in `MEASURED_UPSCALE_SOURCES`
  (the measured `macaron_cookie` 360p clip). A source not in the local catalog, or 4K, 720p, GIF,
  Veo Lite / Quality sources → UI (the menu keeps the #1338 exit 22). No network read is made to
  decide the route.
- [ ] `plan_visibility(setting, routes) -> BrowserVisibility`: `AUTO` and every planned route
  DIRECT → HEADLESS; `AUTO` and any route UI → HEADED; `HEADED` → HEADED; explicit `HEADLESS` →
  HEADLESS (the user's choice; for UI shapes unsupported in v0.108.0, see #1554).
- [ ] An offline assertion that every key in `MEASURED_VIDEO_KEYS` and every image wire model
  exists in the recorded `HTrJv` fixture (`tests/fixtures/flow_htrjv_model_families.json`).

**Tests created (red first):**
- [ ] `test_unmeasured_shapes_route_ui` — r2v, extend, edit, end frame, ingredients, 4K upscale,
  Omni i2v at 9:16 (rows 6, 10)
- [ ] `test_credit_capped_video_routes_ui` (rows 11, 33)
- [ ] `test_video_direct_only_for_exact_measured_tuple` (row 9)
- [ ] `test_i2v_model_none_normalises_to_veo_3_1_i2v_lite_and_routes_direct` (row 9)
- [ ] `test_omni_with_unset_duration_or_resolution_routes_ui` (row 9)
- [ ] `test_proxy_routes_ui` (row 23), `test_labs_host_routes_ui` (row 24)
- [ ] `test_upscale_source_not_in_catalog_routes_ui` (row 26)
- [ ] `test_plan_visibility_headed_when_any_route_is_ui` (rows 6, 34)
- [ ] `test_auto_ui_route_launches_headed` — every UI-bound shape: r2v, extend, edit, end-frame
  i2v, 4K / 720p / GIF upscale, local `--ref` i2i, capped video (rows 6, 35)
- [ ] `test_explicit_headless_keeps_ui_shapes_headless` (row 22)
- [ ] `test_measured_keys_exist_in_htrjv_fixture`

---

## Task 5 — Setting: `GFLOW_CLI_BROWSER_VISIBILITY` with the deprecated alias

**Tier:** standard · **Wave:** 1 · **Depends on:** —

**What:** One tri-state setting; `GFLOW_CLI_HEADLESS` keeps its meaning as an alias and is flagged
by `gflow doctor`.

**Files:**
- `src/gflow_cli/config.py`, `src/gflow_cli/services/doctor.py`, `src/gflow_cli/api/recaptcha.py`
  (hint strings), `.env.template`
- `tests/test_config.py`, `tests/services/test_doctor.py`

**Steps:**
- [ ] `browser_visibility: Literal["auto", "headless", "headed"] = "auto"`.
- [ ] Alias: `GFLOW_CLI_HEADLESS=true` → `headless`, `=false` → `headed`; an explicit
  `GFLOW_CLI_BROWSER_VISIBILITY` wins. The alias default is not flipped.
- [ ] `env.deprecated_vars` names `GFLOW_CLI_HEADLESS` with the successor and the note that
  `false` pins headed. No new `CHECK_IDS` entry.
- [ ] `recaptcha.py:120,128-129` hints name `GFLOW_CLI_BROWSER_VISIBILITY=headed` and drop
  "headless detection by Google" as a likely cause.
- [ ] Field descriptions that state the old model are rewritten in this task, since it owns
  `config.py` and `.env.template`: `config.py:561-568` (`headless`: "requires headed Chrome —
  reCAPTCHA Enterprise rejects headless"), `:574-575` (window position "still a real headed
  Chrome"), `:305-306` and `:658-659` (patchright "on the headed path … NOT a headless unlock");
  `.env.template:173-180`, `:182-183`, `:200-201` likewise.

**Tests created (red first):**
- [ ] `test_browser_visibility_defaults_auto` (row 22)
- [ ] `test_headless_alias_maps_true_and_false` (row 22)
- [ ] `test_explicit_visibility_wins_over_alias` (row 22)
- [ ] `test_doctor_flags_gflow_cli_headless` (row 22)

---

## Task 6 — Stdlib image dimensions for the i2v crop box

**Tier:** mechanical · **Wave:** 1 · **Depends on:** —

**What:** Read `(width, height)` from PNG, JPEG, GIF and WebP headers without Pillow (an optional
extra).

**Files:**
- `src/gflow_cli/paths.py` (beside `extension_from_magic`), `tests/test_paths.py`

**Steps:**
- [ ] `image_dimensions(data: bytes) -> tuple[int, int] | None`; JPEG walks SOF markers; WebP
  covers VP8, VP8L and VP8X.

**Tests created (red first):**
- [ ] `test_image_dimensions_for_each_format`
- [ ] `test_image_dimensions_none_for_truncated_or_unknown`

---

## Task 7 — Redaction: wider token prefixes

**Tier:** mechanical · **Wave:** 1 · **Depends on:** —

**What:** Scrub reCAPTCHA tokens beyond the `0cAF` prefix. (Applying redaction to httpx exception
text is in Task 9, which owns the call site.)

**Files:**
- `src/gflow_cli/data/redaction.py`, `tests/data/test_redaction.py`, `tests/test_diagnostics_sanitize.py`

**Tests created (red first):**
- [ ] `test_redacts_03AF_and_0cAF_tokens` (row 29)

---

## Task 8 — Agent-safe terminal refusal and the `submit_refused` checkpoint

**Tier:** standard · **Wave:** 1 · **Depends on:** —

**What:** A final refusal tells agents not to retry; a refusal before the retry marks the queued
job as not charged.

**Files:**
- `src/gflow_cli/errors.py`, `src/gflow_cli/api/dto.py`, `src/gflow_cli/worker/daemon.py`,
  `src/gflow_cli/worker/queue.py`
- `tests/test_errors.py`, `tests/worker/test_daemon_checkpoint.py`, `tests/worker/test_queue.py`

**Steps:**
- [ ] `waf_refusal_final(*, reason, attempts)` returns a `WafRejectionError` (exit 10) with
  `charged="no"`, `retryable=False`, `next_action="none"`, and a remediation that says both
  attempts were refused and nothing was charged, names `--browser-visibility headed`, and points
  to the cooldown in `docs/ACCOUNT_SAFETY.md`. No new class; `EXIT_CODE_MAP` unchanged.
- [ ] `GenerationPhase` gains `"submit_refused"`. The daemon observer maps it to
  `save_checkpoint("claimed", may_have_spent=False)`; queue recovery already treats `claimed` as
  pre-submit.

**Tests created (red first):**
- [ ] `test_waf_refusal_final_is_not_retryable_and_not_charged` — problem details and the MCP
  error dict (rows 2, 32)
- [ ] `test_waf_refusal_final_remediation_names_headed_and_cooldown` (row 2)
- [ ] `test_submit_refused_checkpoint_resets_to_claimed` (row 12)
- [ ] `test_recovery_after_submit_refused_marks_failed_not_indeterminate` (row 12)

---

## Task 20 — Offline BDD scaffold for routing and the refusal retry

**Tier:** standard · **Wave:** 1 · **Depends on:** —

**What:** Eight offline scenarios, bound and marked `xfail(strict=True)` (a scenario that starts
passing forces its marker's removal). Each marker is removed by exactly one named task.

**Files:**
- `tests/features/direct_routing.feature`, `tests/features/test_direct_routing_steps.py`

**Scenarios (8):**

| # | Scenario | Row | xfail removed by |
|---|---|---|---|
| 1 | A refused direct submit is retried once from a fresh headless browser | 1 | Task 13 |
| 2 | A refusal on both attempts stops without a third submit | 2 | Task 13 |
| 3 | A timeout after send is never resent | 3 | Task 13 |
| 4 | A signed-out profile reports the UI path's auth error with no retry | 19 | Task 13 |
| 5 | An explicit headed setting with no display fails before launch, naming the display | 20 | Task 13 |
| 6 | A credit-capped video takes the UI path | 11 | Task 12 |
| 7 | An unmeasured shape takes the UI path headed | 6, 10 | Task 12 |
| 8 | `GFLOW_CLI_HEADLESS=false` pins headed | 22 | Task 12 |

---

## Task 28 — Spike: where the account's selected video model can be read

**Tier:** standard · **Wave:** 1 · **Depends on:** —

**What:** Gate-0 evidence for Task 29. The default `gflow video t2v` sends no model
(`api/video.py:295`); the UI composes the key from the picker selection, which gflow today only
touches by opening the settings pane and clicking the picker (`_mc_settings_pane.py:150-158`). No
existing code reads the selection without that click: `flow_wire` allows only `nzlxg` and `pGCYOe`
(`api/flow_wire.py:57`); `model_catalog.py` reads `HTrJv` families from a recorded fixture only and
names no selection (`api/model_catalog.py:1-13`); the documented `videoFx.getUserSettings` fields
(`docs/superpowers/spikes/2026-07-12-ui-cohort-backend-config.md`,
`docs/superpowers/specs/2026-08-27-changelog-modal-precondition-design.md:37-43`) include no model
selection.

**Files:**
- `scripts/dev/spike_selected_video_model.py`, `docs/superpowers/spikes/2026-11-27-selected-video-model.md`

**Steps (zero credits; nothing clicked, nothing submitted):**
- [ ] On the armed mint page, record every reply the project load already makes (`HTrJv`, tRPC
  queries, other batchexecute reads) and `localStorage` / `sessionStorage` keys, on two accounts
  whose last selections differ (set once by hand in a normal session beforehand).
- [ ] Pre-registered readings: a field whose value changes with the hand-set selection and matches
  the key the app then builds (via the existing `--capture-defaults` route-aborted capture) is a
  carrier; record its source, path and whether it needs the page (in-page reply or storage) or can
  be read browser-free (a batchexecute or tRPC read through `flow_wire`).
- [ ] No carrier found is recorded as "not found in the listed sources", not as "unreadable".

**Output:** the spike doc names the carrier (or none) and the selection-to-key mapping observed.

---

## Task 9 — Gateway: phase-tracked attempt returns a `SubmitOutcome`

**Tier:** complex · **Wave:** 3 · **Depends on:** 2, 3, 7

**What:** Replace `_DirectFallbackError` / `_reconcile_error` with typed outcomes decided by phase,
and remove the post-send UI fallback.

**Files:**
- `src/gflow_cli/api/transports/direct_http/_gateway.py`, `_session.py`, `_images.py`
- `tests/api/transports/test_direct_http_gateway.py`, `tests/api/transports/test_direct_http.py`

**Steps:**
- [ ] An attempt object tracks `sent`: it flips to `True` the moment `client.send` is entered for
  a billed write rpcid. Before that, failures (cookie read, shell tokens, mint, connect error) are
  `NotSent`; after it, **every** failure, whatever its type (reply, parse, a later
  `_session_headers` cookie error, poll, read, download), is `Indeterminate`. Tasks 14 and 15 run
  their poll, read and download inside the same attempt.
- [ ] A connect error before any byte (`ConnectError`, `ConnectTimeout`, `PoolTimeout`) is
  `NotSent`; one reconnect is allowed for that case only.
- [ ] Remove `300 <= status < 400 or status in (401, 403)` → fallback (`direct_http.py:296-297`
  before the split); every non-200 after send is `Indeterminate`, and the cached `at` is dropped.
- [ ] `_post_request` takes `source_path`. Separate allowlists: write `ogiZ0b`, `maseQ`, `YhhmEf`,
  `eb1hJf`, `p0UkFb`; read `jwpduf`, `as29s`. A write rpcid outside the list raises before sending.
- [ ] Shell tokens: on a shell GET 401, drop `at` and refetch once before the mint; never after a
  send. Session headers are read from the live context before every request.
- [ ] The `Indeterminate` cause text passes through `redact_sensitive_text` before it reaches an
  error detail or a log. Token bodies are never logged.

**Tests created (red first):**
- [ ] `test_connect_error_before_send_is_not_sent_and_reconnects_once` — asserts zero bytes sent
  (row 5)
- [ ] `test_post_send_403_is_indeterminate_never_ui_fallback` (row 4)
- [ ] `test_read_timeout_after_send_is_not_resent` (row 3)
- [ ] `test_any_failure_after_send_is_indeterminate` — parse error and a cookie error raised after
  the POST (row 3)
- [ ] `test_shell_401_refreshes_at_before_mint` (row 18)
- [ ] `test_cookies_read_live_before_each_request` (row 18)
- [ ] `test_httpx_exception_with_fsid_url_is_scrubbed_in_detail` (row 29)
- [ ] `test_write_rpcid_outside_allowlist_refused_before_send`

---

## Task 10 — Mint adapter: mint-only lock, cap 4, quiesce, re-arm and discard

**Tier:** complex · **Wave:** 4 · **Depends on:** 9

**What:** `PageTokenSource(page, visibility)` implements `RecaptchaTokenSource`; only the mint
holds the page; at most 4 billed POSTs run at once; the armed page stays armed for the client's
life until it is re-armed or discarded; the gateway can be quiesced for a relaunch.

**Files:**
- `src/gflow_cli/api/transports/direct_http/_mint.py`, `_gateway.py`, `_images.py`
- `tests/api/transports/test_direct_http_mint.py`

**Steps:**
- [ ] Injection: `run_direct_images` / `run_direct_upload` (and later the video and upscale
  runners) take a `tokens: RecaptchaTokenSource` argument instead of a page. `DirectHttpTransport`
  builds one `PageTokenSource` at `setup()` and passes it; every test in this task uses a
  `FakeTokenSource`, which is why the Protocol exists.
- [ ] The lock covers navigation-if-needed + `TokenMinter.mint` only; POST, poll and download run
  outside it under `asyncio.Semaphore(MAX_IN_FLIGHT)` (4) per transport instance.
  `GFLOW_CLI_CONCURRENCY` sizes the UI page pool only.
- [ ] `quiesce()` (async context manager): blocks new starts and waits until every in-flight
  attempt has finished; used by `relaunch()` (Task 11).
- [ ] Before each mint, `needs_rearm` on `time.time()`; a re-arm opens a new page in the same
  context and closes the old one.
- [ ] Any refusal discards the page whose token was refused: `PageTokenSource.discard()` marks it,
  and the next mint opens a new page in the same context unless a relaunch replaces the context
  first. The spike showed a refused page stays refused for 20 minutes while fresh pages are
  accepted.
- [ ] Log-only start-rate warning: each billed start is recorded per transport; when
  `starts_exceed` is true, emit `direct.start_rate_high` (count, window_s) once per window. Nothing
  waits or refuses on it.
- [ ] The page does not navigate per project (a token from one project page is accepted for
  another, measured 3/3).

**Tests created (red first):**
- [ ] `test_lock_spans_mint_only_posts_overlap` (row 14)
- [ ] `test_at_most_four_posts_in_flight` (row 14)
- [ ] `test_concurrency_setting_cannot_raise_direct_cap` (row 14)
- [ ] `test_start_rate_warning_logged_not_blocking` (row 14)
- [ ] `test_quiesce_blocks_new_starts_and_waits_for_in_flight` (row 8)
- [ ] `test_rearm_opens_new_page_after_wall_gap` (row 16)
- [ ] `test_refusal_discards_page_next_mint_uses_new_page` (rows 7, 16)
- [ ] `test_one_page_serves_two_projects_without_navigation` (row 17)

**E2E (headless once, one profile):**
- [ ] `tests/e2e/test_direct_image_e2e.py::test_four_images_in_flight_from_one_armed_page`
  (`e2e_image`; rows 14, 17)

---

## Task 11 — `FlowApiClient.relaunch(visibility)` and the display check

**Tier:** complex · **Wave:** 3 · **Depends on:** 3, 5

**What:** The client owns visibility; it can relaunch (a new browser process, at the same or a
new visibility) after draining all in-flight work, without releasing the profile lease, and it
refuses a headed launch with no display before Playwright runs.

**Files:**
- `src/gflow_cli/api/client.py`, `src/gflow_cli/api/_flow_session.py`
- `tests/api/test_client_relaunch.py`

**Steps:**
- [ ] `FlowApiClient(visibility: BrowserVisibility = HEADED)` replaces `headless: bool` (no
  transitional keyword; Task 12 updates every caller); `_persistent_context_kwargs` maps it.
- [ ] Split `_close_browser_resources` so the lease release is a separate step that only
  `__aexit__` (and the partial-setup guard) runs.
- [ ] `relaunch(visibility)`: enter `transport.quiesce()` when the transport has one (blocks new
  direct starts, waits for in-flight POST, poll and download), then wait until every pool page is
  checked back in (this covers `client.download` through `page.request`,
  `_flow_generation.py:210-217`), then close context and driver, relaunch, reopen the pool and
  re-run `_setup_transport`, all under the held `ProfileLease`. The refusal retry calls it with
  the client's current visibility (headless under `auto`); a UI fallback calls it with HEADED.
- [ ] A relaunch that fails leaves the lease held and the client unusable; it raises
  `RelaunchFailed`, which the retry loop turns into the final not-charged refusal (Task 13). The
  lease is released only in `__aexit__`; nothing re-acquires it.
- [ ] Before any headed launch on Linux (an explicit `headed` setting, or a UI-bound shape), no
  `DISPLAY` and no `WAYLAND_DISPLAY` raises `UiModeUnavailableError` (exit 28) naming the missing
  display and `xvfb-run`. A headless launch needs no display.

**Tests created (red first):**
- [ ] `test_relaunch_keeps_the_same_lease_instance` (row 8)
- [ ] `test_relaunch_raises_no_profile_locked_in_process` (row 8)
- [ ] `test_other_process_still_sees_the_lock_during_relaunch` — a subprocess calls
  `ProfileLease.try_acquire()` mid-relaunch and gets `False` (an in-process probe would hit the
  registry, `profile_lease.py:300-311`) (row 8)
- [ ] `test_relaunch_waits_for_checked_out_pages_and_quiesce` (row 8)
- [ ] `test_failed_relaunch_keeps_lease_until_aexit` (row 8)
- [ ] `test_headed_launch_without_display_raises_before_playwright` (row 20)
- [ ] `test_headless_launch_needs_no_display` (row 20)
- [ ] `test_relaunch_resets_transport_on_new_page` (row 7)

---

## Task 13 — The refusal retry loop (images)

**Tier:** complex · **Wave:** 5 · **Depends on:** 8, 9, 10, 11

**What:** One loop runs a direct attempt, applies `next_step`, relaunches and retries once, and
raises the right typed error. This task wires images and uploads; Tasks 14 and 15 wire video and
upscale.

**Files:**
- `src/gflow_cli/api/_flow_retry.py`, `src/gflow_cli/api/_flow_generation.py` (images),
  `src/gflow_cli/api/transports/direct_http/_transport.py`
- `tests/api/test_flow_retry.py`, `tests/worker/test_cancel_direct.py`
- `tests/e2e/test_refusal_retry_e2e.py`

**Steps:**
- [ ] `DirectHttpTransport` raises `DirectAttemptEnded(outcome)` for every non-accepted outcome and
  for every route mismatch, including the `upload_reference` fallback reasons
  (`direct_http.py:482-483`, `:494-505` before the split, now `NotSent`). It never calls the UI
  path in place. The `_park_composer_page` call after direct operations is removed (parking stays
  on the UI path).
- [ ] The loop: `RetryDirect(v)` → `client.relaunch(v)` (a new headless browser process under
  `auto`) then one direct retry; `FallbackUi(v)` → relaunch to `v` when the page was headless, then
  emit `submit_attempted` through the #1198 gate (`queue.py:304-332`) and run the UI path (the UI
  transport never receives `on_before_submit`, so the loop is the one place this checkpoint can be
  written); `Stop(waf_final)` or `RelaunchFailed` → `waf_refusal_final`; `Stop(reconcile)` → the
  reconcile error (`next_action="reconcile"`, `charged="maybe"`, `retryable=False`);
  `Stop(reauth)` → the class the UI path raises for a signed-out session.
- [ ] On the direct route the transport receives `on_before_submit`, which emits
  `submit_attempted` immediately before the billed POST; a refusal emits `submit_refused` before
  the relaunch. Any exception raised by `on_before_submit` (for example `TaskCancelledError` from
  the gate, `daemon.py:756-757`) propagates unchanged; it is never mapped to `NotSent`.
- [ ] A refusal while another job is in flight on the same client: one relaunch; it runs only
  after `quiesce()` lets the accepted job finish its poll and download; the other job is not
  retried.
- [ ] Human mode prints one stderr line before the relaunch ("Flow refused the token; retrying
  once from a fresh browser"); no window appears under `auto`; `--json` stays clean.

**Tests created (red first):**
- [ ] `test_refusal_retries_once_from_new_headless_process` — exactly two sends, the second after
  a relaunch at HEADLESS (row 1)
- [ ] `test_refusal_retry_headed_only_under_explicit_headed` (rows 1, 22)
- [ ] `test_second_refusal_raises_final_waf_no_third_send` (row 2)
- [ ] `test_indeterminate_raises_reconcile_and_sends_once` (row 3)
- [ ] `test_not_sent_falls_back_to_ui_after_headed_relaunch` (row 7)
- [ ] `test_route_mismatch_and_upload_fallback_raise_not_sent_no_in_place_ui` (rows 6, 7)
- [ ] `test_checkpoint_written_just_before_post_and_reset_on_refusal` (row 12)
- [ ] `test_ui_fallback_passes_submit_gate_before_ui_submit` (row 12)
- [ ] `test_cancel_during_ui_fallback_reports_abandoned_exit_22` (row 13)
- [ ] `test_cancel_during_direct_post_reports_abandoned_exit_22` (row 13)
- [ ] `test_on_before_submit_exception_propagates_unchanged` (rows 12, 13)
- [ ] `test_two_in_flight_one_refused_accepted_one_completes` (rows 8, 14)
- [ ] `test_relaunch_failure_raises_final_refusal_not_charged` (row 8)
- [ ] `test_signed_out_profile_matches_ui_auth_error` (row 19)
- [ ] `test_explicit_headed_without_display_fails_before_launch` (row 20)
- [ ] `test_no_about_blank_park_after_direct_call`
- [ ] Task 20 scenarios 1–5 lose their `xfail` marker.

**E2E (headless once, one profile; the full matrix is Task 24):**
- [ ] `tests/e2e/test_refusal_retry_e2e.py` (`e2e_image`, opt in with
  `GFLOW_CLI_E2E_RUN_REFUSAL=1`): the first mint is corrupted on purpose, as
  `test_migrated_refusal_live_bdd.py` does; asserts two submits, the second from a new headless
  browser process, `retried=true`, `visibility="headless"`, an image written, and an unchanged
  balance. A headed variant of the same test runs only when
  `GFLOW_CLI_BROWSER_VISIBILITY=headed` is set. Probe profile only.

---

## Task 14 — Direct text-to-video and image-to-video

**Tier:** complex · **Wave:** 6 · **Depends on:** 4, 6, 13

**What:** Port the captured request frames and run t2v and start-frame i2v over direct HTTP, with
poll, read and download inside the phase-tracked attempt, and wire video into the retry loop.

**Files:**
- `src/gflow_cli/api/transports/direct_http/_video.py`, `_parse.py`, `_transport.py`
  (`generate_video` override), `src/gflow_cli/api/transports/frame_builder.py`,
  `src/gflow_cli/api/_flow_generation.py` (`generate_video` calls the loop)
- `tests/api/transports/test_direct_http_video.py`
- `tests/e2e/test_direct_video_e2e.py`

**Steps:**
- [ ] Builders port the **captured** frames (`--capture-defaults`): the t2v item ends with the
  duration slot (`[…, ids, null, null, [4]]`), the batch tail is `["<uuid>", 1]`, the aspect slot
  comes from the allowlist row (`1` = 9:16, `2` = 16:9). The `eb1hJf` item carries the centred crop
  box (fractions, `0` sent as `null`) from `image_dimensions`; the start frame goes through the
  direct `maseQ` upload. Actions: `VIDEO_GENERATION` (t2v), `UPLOAD_IMAGE` then `VIDEO_GENERATION`
  (i2v).
- [ ] `_parse.py`: an exact-id record finder (also used by Task 15 for `<uuid>_upsampled`);
  "exists" means the record carries a media id (a missing id answers a stub with status 4).
- [ ] Poll `jwpduf` with the existing poll budget (interval injected; 0 under test); read `as29s`
  with the bare workflow id; download through the existing allowlisted downloader,
  `follow_redirects=False`, no cookies, size checked against the record. All of it runs after
  `sent`, so any failure is `Indeterminate` and the clip is never resubmitted.
- [ ] An i2v retry after a refusal reuses the uploaded media id and does not re-upload.
- [ ] `VideoResult` carries `transport="direct_http"`.

**Tests created (red first):**
- [ ] `test_yhhmef_frame_matches_captured_default` (`[4]` slot, tail `1`; tokens and UUIDs elided)
- [ ] `test_eb1hjf_frame_matches_captured_default` (`veo_3_1_i2v_lite`, aspect slot `1`)
- [ ] `test_eb1hjf_crop_box_for_1200x630` — 16:9 `[null, 0.033333, 1, 0.966667]`, 9:16
  `[null, 0.352344, 1, 0.647656]`
- [ ] `test_cookie_failure_during_jwpduf_poll_is_reconcile_one_send` (row 3)
- [ ] `test_jwpduf_status_4_without_media_is_not_done` (row 25)
- [ ] `test_as29s_without_video_url_raises_reconcile_without_resubmit` (row 25)
- [ ] `test_download_host_outside_allowlist_refused` (row 27)
- [ ] `test_video_refusal_retries_once_from_new_headless_process` (row 1)
- [ ] `test_i2v_retry_reuses_uploaded_media_id`
- [ ] `test_unmeasured_video_shape_raises_not_sent_route` (rows 9, 10)

**E2E (headless once, one profile; spends credits):**
- [ ] `tests/e2e/test_direct_video_e2e.py` (`e2e_video`, `GFLOW_CLI_E2E_RUN_VIDEO=1`): t2v at an
  explicit measured key and the default `gflow video i2v` shape; asserts route `direct`,
  visibility `headless`, an mp4 of the expected dimensions, and a balance drop equal to the key's
  cost.

---

## Task 15 — Direct 1080p upscale

**Tier:** complex · **Wave:** 6 · **Depends on:** 4, 13, and Task 14's `_parse.py` finder (same
wave: Task 15 imports it and rebases after Task 14 lands; it does not edit `_parse.py`)

**What:** `p0UkFb` replaces the menu click for 1080p on catalogued measured sources, inside the
retry loop; everything else keeps the menu.

**Files:**
- `src/gflow_cli/api/transports/direct_http/_upscale.py`, `src/gflow_cli/api/_flow_media_edit.py`
- `tests/api/transports/test_direct_http_upscale.py`
- `tests/e2e/test_direct_upscale_e2e.py`

**Steps:**
- [ ] `upsample_video` looks the media id up in the local catalog and calls
  `choose_upscale_route`; not catalogued → menu path, decided before launch.
- [ ] Frame per the spike (32-slot item, `veo_3_1_upsampler_1080p`, `source-path` on the edit
  route); poll `<workflow_id>_upsampled` with the exact-id finder; read with `as29s`; check
  1920x1080 with `mp4_track_dimensions`; all inside the attempt.
- [ ] A Flow refusal for the item maps to `UpscaleUnavailableError` (exit 22), as the menu path
  does (#1338). `UNUSUAL_ACTIVITY` goes through the loop like any submit.

**Tests created (red first):**
- [ ] `test_p0ukfb_frame_carries_workflow_and_aspect` (row 26)
- [ ] `test_upsampled_suffix_found_by_exact_id` (row 26)
- [ ] `test_uncatalogued_source_uses_menu` (row 26)
- [ ] `test_wrong_dimensions_raise_existing_error`
- [ ] `test_flow_refusal_maps_to_exit_22`

**E2E (headless once, one profile; 0 credits for the upscale):**
- [ ] `tests/e2e/test_direct_upscale_e2e.py` (`e2e_video`, `GFLOW_CLI_E2E_RUN_VIDEO=1` for the
  source clip): route `direct`, 1920x1080, balance unchanged by the upscale.

---

## Task 12 — Route-aware client construction and the direct default

**Tier:** standard · **Wave:** 7 · **Depends on:** 4, 5, 11, 13, 14, 15

**What:** One helper turns the setting and the planned routes into the launch visibility; every
`FlowApiClient` construction calls it once; `direct_http` becomes the default transport.

**Files:**
- `src/gflow_cli/api/transports/__init__.py`, `src/gflow_cli/_cli_helpers.py`, the 23
  `headless=settings.headless` sites (`cli_character.py` 5, `cli_image.py` 6, `cli_run.py`,
  `cli_scene.py` 2, `cli_video.py`, `mcp/character_tools.py`, `mcp/tools.py` 4 at :1723, :1809,
  :2052, :2379, `services/credits.py`, `services/media_recovery.py`, `services/media_trash.py`), the
  other `headless=` forms (`image_batch.py` :834, :878, :1422; `cli_instructions.py`,
  `cli_data.py`, `worker/daemon.py`), `chain.py`, `cli_movie.py`
- `tests/api/transports/test_registry.py`, `tests/test_cli_routing.py`
- `tests/e2e/headless_direct.feature`, `tests/e2e/test_headless_direct_bdd.py`

**Steps:**
- [ ] `client_visibility(settings, routes=(Route.UI,))` = `plan_visibility(setting, routes)`.
  UI-only commands pass nothing and launch headed under `auto`.
- [ ] Generation sites pass the routes of every item they will run: one item for t2i / i2i / t2v /
  i2v / upscale; every row for `run` and `image batch`; every clip for `video chain` and
  `movie run`. Any UI item → headed launch for the whole run (the simplest rule; direct items then
  mint headed). Each item still routes on its own inside the run.
- [ ] The worker and the MCP tools compute the route after resolving the effective credit cap
  (`resolve_max_credits`, as `daemon.py:800` does for extend), so a capped job never launches
  headless.
- [ ] `_DEFAULT_TRANSPORT = "direct_http"`; `ui_automation` stays a production choice and is the
  documented kill switch.
- [ ] `rg "headless=" src/gflow_cli --glob '!api/client.py'` shows no construction left on the old
  keyword.

**Tests created (red first):**
- [ ] `test_default_transport_is_direct_http` and `test_ui_automation_kill_switch`
- [ ] `test_unmeasured_shape_constructs_headed_client` (row 6)
- [ ] `test_ui_only_commands_launch_headed_under_auto` (row 6)
- [ ] `test_chain_with_a_ui_clip_launches_headed` (row 34)
- [ ] `test_movie_run_with_a_ui_clip_launches_headed` (row 34)
- [ ] `test_run_and_image_batch_with_a_ui_row_launch_headed` (row 34)
- [ ] `test_all_direct_chain_launches_headless_and_reuses_page` (row 34)
- [ ] `test_worker_routes_after_effective_cap` and `test_mcp_capped_video_constructs_headed_client`
  (rows 11, 33)
- [ ] Task 20 scenarios 6–8 lose their `xfail` marker.

**E2E (headless once, one profile):**
- [ ] `tests/e2e/test_headless_direct_bdd.py` — the Scenario Outline in SCENARIO.md (t2v at a
  measured key, upscale 1080p) and the four-row image batch from one armed page; JSON asserts
  `route`, `visibility`, `retried`.

---

## Task 29 — Bare `gflow video t2v` goes direct with the account's selected model

**Tier:** complex · **Wave:** 7 · **Depends on:** 14, 28

**What:** A t2v request with no model resolves the key Flow would build from the account's
current selection, using the carrier Task 28 found, and goes direct when that key is in
`MEASURED_VIDEO_KEYS`; otherwise, or when the read fails, it takes the UI path.

**Files:**
- `src/gflow_cli/api/generation_route.py` (selection-to-key mapping; t2v `model=None` rule),
  `src/gflow_cli/api/transports/direct_http/_session.py` (the read),
  `src/gflow_cli/api/transports/direct_http/_video.py` (use the resolved key)
- `tests/api/test_generation_route.py`, `tests/api/transports/test_direct_http_video.py`
- `tests/e2e/test_direct_video_e2e.py` (one new case)

**Steps (the shape depends on Task 28's finding; the plan records which applies before coding):**
- [ ] **Carrier readable browser-free** (a batchexecute or tRPC read): add its rpcid to
  `flow_wire`'s read allowlist, read it before launch, and route with the resolved tuple; the
  route stays a pre-launch decision.
- [ ] **Carrier readable only on the page** (a reply the project load already makes, or page
  storage): `choose_video_route` returns DIRECT for t2v `model=None` (the run launches headless);
  the transport reads the selection on the armed mint page with no navigation and no click, maps
  it to a key, and raises `DirectAttemptEnded(NotSent("t2v_key_unresolved"))` when the read fails
  or the key is unmeasured, which the retry loop turns into `FallbackUi(HEADED)`.
- [ ] **No carrier found:** t2v `model=None` stays UI (Task 4's rule); this task is a Decision log
  entry and the item stays in Deferred.
- [ ] The resolved key is logged (`direct.t2v_key_resolved`, source) and appears in `--json` as
  the model that ran.

**Tests created (red first):**
- [ ] `test_selection_maps_to_measured_key_routes_direct` (row 9)
- [ ] `test_unmeasured_selection_routes_ui` (row 9)
- [ ] `test_selection_read_failure_falls_back_to_ui_not_sent` (rows 7, 9)

**E2E (headless once, one profile; spends credits):**
- [ ] `tests/e2e/test_direct_video_e2e.py::test_bare_t2v_uses_account_selection` (`e2e_video`):
  bare `gflow video t2v`; asserts route `direct`, visibility `headless`, and the key that ran equals
  the account's selection.

---

## Task 17 — Observability: route, visibility, retry

**Tier:** standard · **Wave:** 7 · **Depends on:** 13, 14, 15

**What:** Logs, `--json` and the recorder say which path ran and whether it was retried. This task
does not edit `cli_*.py` (Task 12 owns them in this wave); the JSON fields go through
`json_output.py`.

**Files:**
- `src/gflow_cli/data/recorder.py`, `src/gflow_cli/data/_recording_image.py`, the video recording
  module, `src/gflow_cli/json_output.py`, `src/gflow_cli/api/_flow_retry.py`

**Steps:**
- [ ] Events: `mint.retried` (visibility, new_process=true), `direct.video_post`,
  `direct.video_poll`, `direct.indeterminate`, `direct.upscale_post`, `mint.rearmed`
  (`reason=wall_gap|refused`); `direct.start_rate_high` is emitted by Task 10. Never the token,
  cookies, prompt or signed URL.
- [ ] Recorder rows for direct submits carry the same fields as UI submits plus `route`,
  `visibility` and `retried`; prompt redaction respected.
- [ ] `--json` results carry `route`, `visibility`, `retried`.

**Tests created (red first):**
- [ ] `test_direct_submit_recorded_like_ui_plus_route_visibility_retried` (row 28)
- [ ] `test_json_output_carries_route_visibility_retried` (row 30)
- [ ] `test_logs_never_carry_token_or_signed_url` (row 29)

---

## Task 18 — CLI surface: `--browser-visibility`

**Tier:** standard · **Wave:** 8 · **Depends on:** 5, 12

**What:** One shared option on the leaves that can mint: `image t2i`, `image i2i`,
`image generate`, `video t2v`, `video i2v`, `video generate`, `video upscale`, `run`.

**Files:**
- `src/gflow_cli/_cli_helpers.py`, `src/gflow_cli/cli_image.py`, `src/gflow_cli/cli_video.py`,
  `src/gflow_cli/cli_run.py`, `tests/test_cli_browser_visibility.py`

**Steps:**
- [ ] `--browser-visibility [auto|headless|headed]`, env `GFLOW_CLI_BROWSER_VISIBILITY`; help text
  self-contained: `auto` (default) mints headless and retries a not-charged refusal once from a
  fresh headless browser; shapes the direct path cannot send open a visible browser; `headed`
  always shows the window; an explicit `headless` also runs Flow UI shapes headless, unsupported in
  v0.108.0.

**Tests created (red first):**
- [ ] `test_option_overrides_env` (row 22)
- [ ] `test_headed_option_disables_headless_mint` (row 22)
- [ ] `test_help_text_names_env_and_values`

---

## Task 19 — MCP surface mirror

**Tier:** standard · **Wave:** 9 · **Depends on:** 17, 18

**What:** The MCP twins take the same parameter, route the same way on the direct and queued
paths, and report the same fields. Mirror axes per `skills/check/SKILL.md` step 1b.

**Files:**
- `src/gflow_cli/mcp/tools.py` (`gflow_generate_image`, `gflow_generate_video`,
  `gflow_upscale_video`; the OpenAI image route inherits from `gflow_generate_image`),
  `src/gflow_cli/worker/codec.py`, `tests/mcp/test_cli_parity.py`, `tests/mcp/test_server.py`,
  `tests/worker/test_codec.py`
- `tests/e2e/test_direct_video_mcp_e2e.py`

**Steps:**
- [ ] `browser_visibility: str | None` (case-insensitive; unknown → the existing 400 envelope, as
  `ui_mode`).
- [ ] Queued payload key `"browser_visibility"` written by the tools and decoded in
  `worker/codec.py` for image, video and upscale.
- [ ] Tool results carry `route`, `visibility`, `retried`; a final refusal surfaces with
  `retryable=false`, `next_action="none"`.
- [ ] Docstrings: grep for "editor", "menu", "headed", "browser" in the three tools and correct any
  claim the direct path made false.

**Tests created (red first):**
- [ ] `test_every_cli_option_reaches_its_mcp_tool` covers `--browser-visibility` (row 31)
- [ ] `test_browser_visibility_round_trips_through_codec` (row 31)
- [ ] `test_mcp_max_credits_video_routes_ui` (row 33)
- [ ] `test_mcp_final_refusal_envelope_not_retryable` (row 32)

**E2E (headless once, one profile):**
- [ ] `tests/e2e/test_direct_video_mcp_e2e.py` (`e2e_video`): `gflow_generate_video` at a measured
  key, direct (`wait=true`) and queued through the worker; `gflow_upscale_video` 1080p. Asserts
  `route`, `visibility` and a written file for each path.

---

## Task 23 — Documentation

**Tier:** standard · **Wave:** 10 · **Depends on:** 19, 29

**What:** The reference docs for the new behaviour: one page answers "does this command open a
window"; the wire, configuration, MCP and error references match the code; the CHANGELOG records
v0.108.0 as a milestone. The narrative rewrite of how the project describes itself (README,
AGENTS.md, ARCHITECTURE.md, ACCOUNT_SAFETY.md, KNOWN_ISSUES.md, the agent skill) is Task 30.

**Decision — new `docs/BROWSER_MODES.md`, not an extension of `docs/DIRECT_TRANSPORT.md`.** The
per-command table covers every command, most of which never touch the direct transport.
`DIRECT_TRANSPORT.md` stays the wire reference and links to the new page.

**Files:**
- `docs/BROWSER_MODES.md` (new), the single source of the table
  `command | browser mode in v0.108.0 | why`, in three groups:
  - **Headless (direct HTTP):** `image t2i`, `image i2i` (measured shape), `image generate`,
    `run` and `image batch` (image rows only), `video t2v` / `video i2v` / `video generate` for the
    rows of `MEASURED_VIDEO_KEYS` (the "why" cell names those rows; bare `video t2v` as Task 29
    settled it), `video upscale --scale 1080p` on catalogued measured sources, `video chain` /
    `movie run` when every clip is direct, and the same rules under `gflow serve` and the MCP
    server.
  - **Headed (Flow UI):** `auth login`, `video r2v`, `video extend`, `video edit`, end-frame i2v,
    `video upscale` at 4K / 720p / GIF, `scene create`, `character create`, `instructions`, the
    agent-only composer, `media trash` / `restore`, i2i with a local `--ref`, any capped video, and
    a chain or run with at least one UI item.
  - **No browser:** `video assemble`, `video captions`, `video transcribe`, `audio narrate`,
    `docs`, `data`, `telemetry`, `update`, `models`, `capabilities`, `cancel`; plus `auth renew`
    and the `credits` direct read, already headless or browserless today.
  Then: the route rule; the retry rule (a not-charged refusal is retried once from a fresh
  headless browser; a second refusal is final, with the cooldown in `ACCOUNT_SAFETY.md`); a Mermaid
  flowchart (request → route predicate before launch → direct: headless mint → accepted / refused
  → one retry from a fresh headless browser → final refusal; UI-bound → headed UI path; no browser
  → local); platform status (measured on Windows; macOS and Linux unmeasured); the cap and re-arm
  design values; how to force headed; the kill switch; and: "An explicit
  `--browser-visibility headless` (or `GFLOW_CLI_BROWSER_VISIBILITY` / the deprecated
  `GFLOW_CLI_HEADLESS=true`) also runs Flow UI shapes headless at the user's choice; this is
  unsupported in v0.108.0, see #1554." Check every listed leaf against `gflow --help --json`
  before writing.
- Rewrites: `docs/CONFIGURATION.md` (`GFLOW_CLI_BROWSER_VISIBILITY`, the alias, `:420-425`
  `GFLOW_CLI_HEADLESS`, `:429-434` window position, `:530` patchright), `docs/DIRECT_TRANSPORT.md`
  (`:1` title "image transport, experimental", `:8` "off unless you select it", `:64` parking,
  `:81` the 3xx/401/403 rule, the event list, the retry rule), `docs/MCP.md`
  (`gflow_generate_video` i2v prose, the lease wording, `gflow_upscale_video`), `docs/ERRORS.md`
  (the refusal's `charged="no"`, `retryable=false`), `docs/USAGE.md:2939` (the
  `FlowApiClient(headless=…)` example).
- `CHANGELOG.md` `[Unreleased]`, written as a milestone entry: a headline section (headless by
  default; the browser only mints the reCAPTCHA token; the direct HTTP shapes; what still needs
  the Flow UI, tracked by #1554); migration notes (`GFLOW_CLI_BROWSER_VISIBILITY` /
  `--browser-visibility`, the deprecated `GFLOW_CLI_HEADLESS` alias and what `false` now pins,
  the `GFLOW_CLI_TRANSPORT=ui_automation` kill switch, the resend-once-on-refusal behaviour
  change); and a short credit: the direct-transport roadmap was confirmed and accelerated by the
  independent project flowfast (<https://github.com/JvanLeeuwen4/flowfast>, MIT), which built on
  gflow-cli and published its wire map and measurements; gflow-cli re-derived every shape from its
  own captures.
- `PLAN.md` decision log: ADR 16 (direct with a headless mint is the default for measured shapes;
  one retry from a fresh headless browser on a not-billed refusal; supersedes Q1's fail-loud
  default; D3 unchanged); an amendment row to ADR 2 (domain modules flat beside the transport
  package).
- `docs/INDEX.md`, the bundled docs catalog, `website/docs/` (regenerated).

**Steps:**
- [ ] `check_doc_links.py`, `generate_website_docs.py --check` green.

**Tests:**
- [ ] Doc gates above; row 21 (macOS unmeasured) appears in `BROWSER_MODES.md`.

---

## Task 30 — Documentation honesty: how the project describes itself

**Tier:** standard · **Wave:** 11 · **Depends on:** 23 · **Parallel with:** 25

**What:** Every place the project describes itself states the current, measured truth, in formal
third-person wording, led by its purpose: gflow-cli exists for automation. Agents and harnesses
generate images and videos on a user's own Google Flow account, predictably, and spend the
credits the user pays for along a correct, accountable path; for these users Flow's UI is noise.
The browser is used only where strictly necessary, to mint the reCAPTCHA token, and headless by
default. Statements are honest, not promotional: no claim beyond the evidence, and the remaining
limits are stated plainly.

**What is true in v0.108.0 (the content every rewrite draws on):** a headless mint is accepted
(the spike: 15/15 headless cold mints on two accounts, 30/30 counting both visibilities; headless and headed video 6/6 each on fresh
browsers); the measured shapes go over direct HTTP; the browser's remaining roles are minting the
token (headless), the one-time interactive `auth login`, and the UI-transport surfaces, which stay
headed (#1554); unmeasured: macOS, Linux, accounts Flow serves on labs.google.

**Inventory (33 statements; each is rewritten or deleted):**

| # | Location | Statement today |
|---|---|---|
| 1 | `README.md:24` | drives the session "in a real Chrome window" |
| 2 | `README.md:78` | requirement: "sign-in and generation drive a real Chrome window" |
| 3 | `README.md:79` | a display for sign-in (still true; reword to "only for the one-time sign-in and UI-only commands") |
| 4 | `README.md:84` | Linux "(x86_64 / Xvfb)" as a platform requirement |
| 5 | `README.md:163` | "gflow uses a real Chrome window with randomised interaction timing" |
| 6 | `README.md:227-229` | FAQ: generation drives a real Chrome window, needs Xvfb on Linux, pure HTTP video blocked |
| 7 | `README.md:308` | diagram: "Playwright Chromium (headed — login AND generation, by default)" |
| 8 | `README.md:325` | "What's blocked: a pure HTTP transport for video … cannot create [a token] in a headless browser" |
| 9 | `README.md:327` | "How you can help" plea to drive the API outside real Chrome |
| 10 | `README.md` "Architecture & current limitations" section | the whole section, rewritten (not appended to) |
| 11 | `README.zh-CN.md:71` | display requirement (as #3) |
| 12 | `README.zh-CN.md:76` | Linux Xvfb (as #4) |
| 13 | `README.zh-CN.md:270` | FAQ (as #6) |
| 14 | `README.zh-CN.md:342` | diagram (as #7) |
| 15 | `README.zh-CN.md:361` | help plea (as #9), and the counterparts of #1, #2, #5, #8, #10 |
| 16 | `AGENTS.md:55` | heading "Headed-browser dependency (architectural reality)" |
| 17 | `AGENTS.md:57` | "drives Flow via a real Chrome session … reCAPTCHA stack rejects browsers that advertise automation, and most headless approaches" |
| 18 | `AGENTS.md:60` | "Requires a saved Chrome profile, a display server … ~150 MB" |
| 19 | `AGENTS.md:61` | "Cannot run on serverless / headless CI workers" |
| 20 | `AGENTS.md:62` | "Per-account horizontal concurrency is capped by what one warm Page pool can drive" |
| 21 | `AGENTS.md:64` | plea to unblock a pure HTTP video transport |
| 22 | `docs/ARCHITECTURE.md:451` | section "Headed-browser dependency — current limitation" |
| 23 | `docs/ARCHITECTURE.md:455-461` | "Why headed": headless browsers trip fingerprinting; bare HTTP clients get 401 |
| 24 | `docs/ARCHITECTURE.md:491` | display server requirement (true for login only) |
| 25 | `docs/ARCHITECTURE.md:499-502` | help asks (HAR of headed generation; official API "bypasses the headed-browser dependency") |
| 26 | `docs/ACCOUNT_SAFETY.md:40-44` | "It drives real, headed Chrome … Headless Chromium is rejected by reCAPTCHA Enterprise with an immediate 403" |
| 27 | `docs/ACCOUNT_SAFETY.md:91` | "No headless 'unlock'" (reword: the headless mint announces `HeadlessChrome`; it is not stealth) |
| 28 | `KNOWN_ISSUES.md:1157-1192` | the `PUBLIC_ERROR_UNUSUAL_ACTIVITY` entry: no retry; add the single fresh-browser retry, the refused-page finding, the cooldown |
| 29 | `skills/gflow-cli/SKILL.md:12` | "agents suggest GFLOW_CLI_HEADLESS=true …; correct fix is =false" |
| 30 | `skills/gflow-cli/SKILL.md:20` | product framing (drives Flow's UI) |
| 31 | `skills/gflow-cli/SKILL.md:327` | "reCAPTCHA refuses to mint (headless detected) → set GFLOW_CLI_HEADLESS=false" |
| 32 | `skills/gflow-cli/SKILL.md:357` | "headless mode *causes* bot-detection" |
| 33 | `pyproject.toml:158-162` | comments: patchright "on the headed path … Not a headless unlock" (the `description` at `:4` is accurate and stays) |

Statements in `config.py`, `.env.template` and `recaptcha.py` are rewritten in Task 5, which owns
those files; `docs/CONFIGURATION.md`, `docs/DIRECT_TRANSPORT.md`, `docs/MCP.md`, `docs/USAGE.md`
in Task 23. `CLAUDE.md` carries no such statement (checked by grep). `website/docs/` is a
generated mirror and is regenerated, not edited.

**Files:**
- `README.md`, `README.zh-CN.md` (purpose lead and the limitations section; the table is Task 22),
  `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/ACCOUNT_SAFETY.md`, `KNOWN_ISSUES.md`,
  `skills/gflow-cli/SKILL.md` and its plugin byte copy, `pyproject.toml` (comments),
  `website/docs/` (regenerated)
- `tests/docs/test_no_stale_headless_claims.py`

**Steps:**
- [ ] README: lead with the automation purpose and predictable credit use; leave an anchor where
  Task 22 places the per-command table; rewrite "Architecture & current limitations" to the
  current truth and remaining limits.
- [ ] AGENTS.md: rewrite the "Headed-browser dependency" section to the current architecture (the
  browser mints the token headless; direct HTTP for measured shapes; UI surfaces still headed,
  #1554; unmeasured platforms). Rename the heading and update every link to its anchor.
- [ ] One historical note, in `docs/ARCHITECTURE.md` only, replacing the "Headed-browser
  dependency — current limitation" section: how the product worked before v0.108.0 (a headed
  browser for generation, because reCAPTCHA Enterprise rejected the earlier headless approaches
  and the UI transport drove Flow's page), and what changed in v0.108.0, linking
  `docs/superpowers/spikes/2026-11-27-direct-video-and-headless-mint.md` and #1371. The rewritten
  README section and the AGENTS.md section link to it. Everywhere else states the current truth
  only, with no scattered historical caveats.
- [ ] ACCOUNT_SAFETY.md: the browser section, the cooldown a final refusal points to, and
  `HeadlessChrome` announced (not stealth). KNOWN_ISSUES.md and the skill as listed.
- [ ] `tests/docs/test_no_stale_headless_claims.py` (a few lines): fails if any of a short list of
  phrases ("rejects headless", "Headless Chromium is rejected", "requires headed Chrome",
  "headless mode *causes*", "in a headless browser") appears in `README*.md`, `AGENTS.md`,
  `KNOWN_ISSUES.md`, `skills/**/SKILL.md` or `docs/*.md`, skipping the marked history block in
  `ARCHITECTURE.md` (`<!-- history -->` … `<!-- /history -->`).
- [ ] `check_doc_links.py`, `generate_website_docs.py --check`, `generate_plugin_skills.py --check`,
  `check_website_docs_pii.py` green.
- [ ] This task completes before the release `doc-review` gate.

**Tests created (red first):**
- [ ] `test_no_stale_headless_claims` — red on the current tree (inventory #8, #17, #26, #32), green
  after the rewrite.
- [ ] Row 21 (macOS unmeasured) appears in `ACCOUNT_SAFETY.md` and the README limitations section.

---

## Task 22 — README: the per-command table (English and Chinese)

**Tier:** standard · **Wave:** 12 · **Depends on:** 23, 30

**What:** Place the table under Task 30's purpose lead. Copy it verbatim from `docs/BROWSER_MODES.md` into `README.md` and
`README.zh-CN.md` (the only translation; header and "why" cells translated), add one line on the
single retry from a fresh headless browser and macOS being unmeasured, and link to the doc. (The
stale README claims are rewritten in Task 30.)

**Files:**
- `README.md`, `README.zh-CN.md`, `tests/docs/test_browser_modes_table.py`

**Tests created (red first):**
- [ ] `test_browser_modes_command_column_matches_in_doc_readme_and_zh` — parses the three tables
  and compares the command column and group of each row.
- [ ] `check_doc_links.py` and `check_website_docs_pii.py` green.

---

## Task 25 — Review fixes (bounded)

**Tier:** standard · **Wave:** 11 · **Depends on:** 1–21, 23, 28, 29 (not 22 or 30)

**What:** Run `/gflow:branch-review`, `/code-review` and `/ponytail-review` on the branch and fix
their findings. Scope is bounded to those findings plus removing dead code the change left behind
(`_DirectFallbackError` remnants); nothing else. Runs before the live matrix so the matrix
verifies the final code.

**Steps:**
- [ ] Fix each finding with a test where behaviour changes.
- [ ] `uvx pylint --disable=all --enable=duplicate-code src/gflow_cli/api`: no new duplicate block
  (watch `flow_wire.py` against `_gateway.py`).
- [ ] Full offline suite and Task 1's contract test green.

---

## Task 24 — Live verification matrix

**Tier:** standard · **Wave:** 13 · **Depends on:** 22, 25, 30

**What:** Run every e2e test this plan names, headless and headed, on profiles ffroliva and
denon82, and write the ledger.

**Steps:**
- [ ] For each of `test_direct_image_e2e.py`, `test_refusal_retry_e2e.py` (probe profile),
  `test_direct_video_e2e.py` (including the bare-t2v case), `test_direct_upscale_e2e.py`,
  `test_headless_direct_bdd.py`, `test_direct_video_mcp_e2e.py`:
  `GFLOW_CLI_BROWSER_VISIBILITY` in {headless, headed} × profile in {ffroliva, denon82}, profiles
  in parallel, each run backgrounded.
- [ ] Paste the redacted summary lines into the PR; write `docs/LIVE_VERIFICATION_v0.108.0.md` via
  `/gflow:live-verify`.
- [ ] Named blockers: macOS and Linux (no hardware on hand).

---

## Task 26 — Metrics

**Tier:** standard · **Wave:** 13 · **Depends on:** 25

**Steps:**
- [ ] CRAP per function (`radon cc` × coverage) for every new or changed function: new functions
  under 10, no changed function worse than Task 1's baseline.
- [ ] Coverage on new code at least 90 % (scoped `pytest --cov` and the SonarCloud new-code figure).
- [ ] Mutation testing on `submit_policy.classify_reply`, `submit_policy.next_step`, `_flow_retry`
  and the gateway's `sent` phase rule (mutmut under WSL on an ext4 copy, since it does not run on
  Windows; otherwise a scripted run that flips each comparison and constant in turn). Zero
  surviving mutants on the retry step and the no-resend rule; each survivor gets a behaviour test
  or its dead code is deleted.
- [ ] The table goes in the PR description.

---

## Task 27 — Full gates and release preparation

**Tier:** mechanical · **Wave:** 14 · **Depends on:** 24, 26

**Steps:**
- [ ] `/gflow:check` green (the Impeccable Routine plus step 1b's six mirror axes).
- [ ] `/gflow:sonar` zero new issues.
- [ ] Offline suite time within the 3-minute budget; no new test over 1.5 s.

---

## Scenario coverage

| Row | Severity | Test (task) |
|---|---|---|
| 1 | Critical | `test_refusal_requires_http_200_error_frame_reason_and_no_record`, `test_refused_auto_retries_once_headless`, `test_refused_retry_is_headed_only_under_explicit_headed` (3), `test_refusal_retries_once_from_new_headless_process` (13), `test_video_refusal_retries_once_from_new_headless_process` (14), BDD 1, `test_refusal_retry_e2e.py` (13) |
| 2 | Critical | `test_refused_after_retry_stops_waf_final` (3), `test_second_refusal_raises_final_waf_no_third_send` (13), `test_waf_refusal_final_is_not_retryable_and_not_charged`, `test_waf_refusal_final_remediation_names_headed_and_cooldown` (8), BDD 2 |
| 3 | Critical | `test_timeout_after_send_is_indeterminate` (3), `test_read_timeout_after_send_is_not_resent`, `test_any_failure_after_send_is_indeterminate` (9), `test_cookie_failure_during_jwpduf_poll_is_reconcile_one_send` (14), `test_indeterminate_raises_reconcile_and_sends_once` (13), BDD 3 |
| 4 | Critical | `test_3xx_401_403_after_send_are_indeterminate` (3), `test_post_send_403_is_indeterminate_never_ui_fallback` (9) |
| 5 | High | `test_connect_error_before_send_is_not_sent_and_reconnects_once` (9) |
| 6 | Critical | `test_unmeasured_shapes_route_ui`, `test_auto_ui_route_launches_headed`, `test_plan_visibility_headed_when_any_route_is_ui` (4), `test_route_mismatch_and_upload_fallback_raise_not_sent_no_in_place_ui` (13), `test_unmeasured_shape_constructs_headed_client` (12), BDD 7 |
| 7 | Critical | `test_not_sent_falls_back_to_ui_headed_under_auto` (3), `test_refusal_discards_page_next_mint_uses_new_page` (10), `test_relaunch_resets_transport_on_new_page` (11), `test_not_sent_falls_back_to_ui_after_headed_relaunch` (13), `test_selection_read_failure_falls_back_to_ui_not_sent` (29) |
| 8 | Critical | `test_relaunch_keeps_the_same_lease_instance`, `test_relaunch_raises_no_profile_locked_in_process`, `test_other_process_still_sees_the_lock_during_relaunch` (subprocess), `test_relaunch_waits_for_checked_out_pages_and_quiesce`, `test_failed_relaunch_keeps_lease_until_aexit` (11), `test_quiesce_blocks_new_starts_and_waits_for_in_flight` (10), `test_two_in_flight_one_refused_accepted_one_completes`, `test_relaunch_failure_raises_final_refusal_not_charged` (13) |
| 9 | High | `test_video_direct_only_for_exact_measured_tuple`, `test_i2v_model_none_normalises_to_veo_3_1_i2v_lite_and_routes_direct`, `test_omni_with_unset_duration_or_resolution_routes_ui` (4), `test_selection_maps_to_measured_key_routes_direct`, `test_unmeasured_selection_routes_ui` (29), `test_direct_video_e2e.py` incl. `test_bare_t2v_uses_account_selection` (14, 29) |
| 10 | High | `test_unmeasured_shapes_route_ui` (4), `test_unmeasured_video_shape_raises_not_sent_route` (14) |
| 11 | Critical | `test_credit_capped_video_routes_ui` (4), `test_worker_routes_after_effective_cap` (12), BDD 6. Direct capped video is deferred: no browser-free quote is established (Decision log) |
| 12 | High | `test_submit_refused_checkpoint_resets_to_claimed` (8), `test_checkpoint_written_just_before_post_and_reset_on_refusal`, `test_ui_fallback_passes_submit_gate_before_ui_submit`, `test_on_before_submit_exception_propagates_unchanged` (13) |
| 13 | High | `test_cancel_during_direct_post_reports_abandoned_exit_22`, `test_cancel_during_ui_fallback_reports_abandoned_exit_22` (13) |
| 14 | High | `test_lock_spans_mint_only_posts_overlap`, `test_at_most_four_posts_in_flight`, `test_concurrency_setting_cannot_raise_direct_cap`, `test_start_rate_warning_logged_not_blocking` (10), `test_starts_exceed_true_for_the_ninth_start_in_120s` (3), `test_four_images_in_flight_from_one_armed_page` (10). Start-rate enforcement is deferred; the warning is log-only |
| 15 | High | Superseded: the retry no longer switches to headed, so there is no visibility to keep sticky; each request retries at most once from a fresh headless browser, bounded by row 2's stop (`test_second_refusal_raises_final_waf_no_third_send`, 13) |
| 16 | High | `test_rearm_on_wall_clock_gap_with_monotonic_unchanged` (3), `test_rearm_opens_new_page_after_wall_gap`, `test_refusal_discards_page_next_mint_uses_new_page` (10) |
| 17 | Medium | `test_one_page_serves_two_projects_without_navigation` (10), e2e (10) |
| 18 | High | `test_shell_401_refreshes_at_before_mint`, `test_cookies_read_live_before_each_request` (9) |
| 19 | Medium | `test_not_sent_auth_stops_reauth_without_retry` (3), `test_signed_out_profile_matches_ui_auth_error` (13), BDD 4 |
| 20 | High | `test_headed_launch_without_display_raises_before_playwright`, `test_headless_launch_needs_no_display` (11), `test_explicit_headed_without_display_fails_before_launch` (13), BDD 5 |
| 21 | High | `docs/BROWSER_MODES.md`, `docs/ACCOUNT_SAFETY.md` (23); e2e blocked on hardware, named in Task 24 |
| 22 | Medium | Task 5 tests, `test_explicit_headless_keeps_ui_shapes_headless` (4), `test_refused_retry_is_headed_only_under_explicit_headed` (3), `test_option_overrides_env`, `test_headed_option_disables_headless_mint` (18), BDD 8 |
| 23 | Medium | `test_proxy_routes_ui` (4) |
| 24 | Medium | `test_labs_host_routes_ui` (4) |
| 25 | High | `test_jwpduf_status_4_without_media_is_not_done`, `test_as29s_without_video_url_raises_reconcile_without_resubmit` (14) |
| 26 | High | `test_upscale_source_not_in_catalog_routes_ui` (4), Task 15 tests, `test_direct_upscale_e2e.py` (15) |
| 27 | Medium | `test_download_host_outside_allowlist_refused` (14) |
| 28 | Medium | `test_direct_submit_recorded_like_ui_plus_route_visibility_retried` (17) |
| 29 | High | `test_redacts_03AF_and_0cAF_tokens` (7), `test_httpx_exception_with_fsid_url_is_scrubbed_in_detail` (9), `test_logs_never_carry_token_or_signed_url` (17) |
| 30 | Medium | `test_json_output_carries_route_visibility_retried` (17) |
| 31 | High | `test_every_cli_option_reaches_its_mcp_tool`, `test_browser_visibility_round_trips_through_codec` (19), `test_direct_video_mcp_e2e.py` (19) |
| 32 | High | `test_waf_refusal_final_is_not_retryable_and_not_charged` (8), `test_mcp_final_refusal_envelope_not_retryable` (19) |
| 33 | Critical | `test_credit_capped_video_routes_ui` (4), `test_mcp_capped_video_constructs_headed_client` (12), `test_mcp_max_credits_video_routes_ui` (19) |
| 34 | Medium | `test_chain_with_a_ui_clip_launches_headed`, `test_movie_run_with_a_ui_clip_launches_headed`, `test_run_and_image_batch_with_a_ui_row_launch_headed`, `test_all_direct_chain_launches_headless_and_reuses_page` (12) |
| 35 | Medium | `test_auto_ui_route_launches_headed` (4): every UI surface stays headed under `auto`; headless UI is #1554 |

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80 % overall; ≥ 90 % on new code)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] Docs updated (Tasks 23 and 30), `docs/BROWSER_MODES.md` registered in `docs/INDEX.md`, README
  and README.zh-CN tables match the doc (Task 22 test), no stale headless claim
  (`test_no_stale_headless_claims`), all before the release `doc-review` gate
- [ ] BDD feature files cover every Critical and High scenario (row 15 is superseded)
- [ ] Every Critical and High row in the coverage table has a passing test
- [ ] Task 24's matrix run (headless and headed × ffroliva and denon82), output pasted (redacted);
  macOS and Linux named as hardware blockers
- [ ] MCP twins run separately from the CLI (Iron Law, second law)
- [ ] No `# TODO` in diff without a tracked issue link
- [ ] Task 1's golden master seen failing on a deliberate mutation and passing on develop
- [ ] Before/after metrics recorded in the PR (Task 26)
- [ ] SonarCloud gate green (zero new issues)
- [ ] Worktree clean: no untracked scratch files, no leftover clones or temp dirs

## Deferred (tracked)

- A page kept warm between commands (#1548; needs a D3 amendment and an idle-release rule).
- Headless UI transport (#1554), with the spike's "Headless UI automation" result as evidence.
- Direct video under a credit cap: needs a browser-free quote. The 2026-11-20 quote spike found
  the quote only in the settings pane DOM; `HTrJv` integers were not matched to prices and no
  price RPC fires on settings changes
  (`docs/superpowers/spikes/2026-11-20-credit-quote-before-submit.md:46`, `:113-119`, `:154-157`).
  A static table from the measured debits (4 credits for `macaron_cookie_t2v_4s_360p`, 10 for
  `veo_3_1_i2v_lite`, one account) would be a guess across plan tiers, and `enforce_credit_cap`
  fails closed on an unknown quote (`src/gflow_cli/api/credit_cap.py:36-44`). A follow-up spike can
  test the `HTrJv` usage integers against these measured debits.
- A retry from a new page in the same browser context instead of a new process (cheaper; the spike
  measured only new-process fresh pages after a refusal).
- Start-rate enforcement (v0.108.0 only logs `direct.start_rate_high`; enforce when the warnings
  are seen to correlate with refusals). Spike evidence: 4 and 8 in flight clean; one refusal window
  after about 14 generations in about 90 s that did not reproduce at a faster pace (16 in about
  55 s, then 4/4 fresh pages); the refusal is not billed and is retried once.
- A telemetry counter for retries (`retried` stays in `--json` and the recorder).
- Re-arm on page age and mint count (one observation each).
- Concurrency above 8 and several warm pages per profile.
- End frames, ingredients, extend, Omni i2v at 9:16 and 4K upscale over direct HTTP.
- Bare `gflow video t2v` direct, if Task 28 finds no carrier for the selected model.
- Direct upscale for sources not in the local catalog.
- A macOS and a Linux live run.
- An active reconcile (reading the project after an indeterminate submit); v0.108.0 reports
  `next_action="reconcile"` as today.

## Decision log

Entries are written as decision, rationale and the alternative rejected. "Review A" and "review B"
are the correctness and simplicity reviews of this plan.

| Date | Decision | Rationale | Rejected |
|---|---|---|---|
| 2026-11-27 | The browser is used only to mint the reCAPTCHA token, headless; a window opens only for a surface that still needs the Flow UI or under an explicit `headed` setting | Users want generations without a browser interrupting them; the token is the one step that needs a browser | Showing a browser whenever something goes wrong |
| 2026-11-27 | A refused submit is retried once from a new headless browser process, at the launch visibility of the setting (headed only under an explicit `headed`); a second refusal is final, not charged, and names `--browser-visibility headed` and the cooldown | In the spike, a refused page stayed refused for 20 minutes, while fresh pages in new processes were accepted in both visibilities (+20.5 min headless, +20.8 min headed): page freshness, not visibility, was the variable. A full relaunch is used because the only fresh-page arm measured accepted after a refusal was a new process; a new page in the same context is unmeasured and deferred | An automatic headed fallback (it shows a window and the spike does not show headed helps); a same-context new page (unmeasured) |
| 2026-11-27 | Bare `gflow video t2v` goes direct with a key built from the account's selected model, read without a click; Task 28 finds the carrier first, Task 29 implements whichever read it finds; an unread or unmeasured selection takes the UI path | No existing code reads the selection without opening the settings pane (`_mc_settings_pane.py:150-158`); the known `getUserSettings` fields carry no model; a spike is cheaper than guessing | Requiring `--model` for direct t2v; assuming the measured Omni key |
| 2026-11-27 | Credit-capped video stays on the UI path in v0.108.0 | No browser-free quote is established (2026-11-20 quote spike, lines 46, 113-119, 154-157); a static table would be a guess across tiers, and the cap must not be guessed | A local price table from two measured debits |
| 2026-11-27 | Warmth is per process; D3 unchanged; the about:blank park after direct calls is removed | The park forced a second navigation per i2i / i2v; a cross-command warm page would hold the lease and make every CLI command on the profile exit 11 (STOP in the pre-implementation analysis) | A cross-command warm page |
| 2026-11-27 | Direct with a headless mint is the default for measured shapes, macOS included; `ui_automation` is the kill switch | Headless and headed tokens were accepted at comparable rates; macOS is documented as unmeasured and the retry is the mitigation | Keeping direct opt-in for one release; headed on darwin until measured |
| 2026-11-27 | v0.108.0 keeps every UI-transport surface headed under `auto`; the route is decided before launch | Release scope: the headless mint and direct HTTP for measured shapes. Evidence for #1554: the spike measured headless equal to headed, 3/3 each on one account, on four read-only UI surfaces with no challenge or redirect, and drove the submit path to the aborted click. Unmeasured: a billed headless UI submit, characters, a video media view, a second account | Moving the UI transport headless behind a billed e2e gate |
| 2026-11-27 | An explicit `headless` (option, `GFLOW_CLI_BROWSER_VISIBILITY`, or the deprecated `GFLOW_CLI_HEADLESS=true`) keeps today's meaning and runs UI shapes headless at the user's choice; documented as unsupported in v0.108.0, see #1554 | Review A13; `FlowApiClient` already honours the setting for the UI composer (`client.py:192`) | Forcing UI shapes headed regardless of an explicit setting |
| 2026-11-27 | The domain modules sit beside the transport package, not inside it | Importing any `direct_http` submodule runs the package `__init__`, which loads the UI transport; flat modules match `services/speech_ports.py` and ADR 2 | Top-level layer folders; modules inside `direct_http/` |
| 2026-11-27 | `RecaptchaTokenSource` is a Protocol with one production adapter, injected into the runners as a `tokens` argument | Review B11: kept because Task 10's tests run against a `FakeTokenSource` | No port, using `TokenMinter`'s page seam |
| 2026-11-27 | `SubmitOutcome` has four variants (`NotSent` added) | A failure before any byte reaches no Flow endpoint and may route elsewhere | Three variants with a sentinel exception |
| 2026-11-27 | The outcome is decided by phase: after the billed POST's bytes are sent, every failure is `Indeterminate` | Review A1: today a cookie error raises `_DirectFallbackError` whatever the phase (`direct_http.py:179-195`), which after a send would route to the UI and bill twice | Classifying by exception type |
| 2026-11-27 | Post-send 3xx / 401 / 403 are indeterminate | Unmeasured on batchexecute; the old UI fallback after send could submit twice | Treating them as not accepted |
| 2026-11-27 | `relaunch()` quiesces the gateway and waits for every checked-out pool page before closing the context | Review A2: an accepted job's poll or `client.download` (`page.request`, `_flow_generation.py:210-217`) would die with the context | Relaunching while work is in flight |
| 2026-11-27 | A failed relaunch ends in the final not-charged refusal; the lease is released only in `__aexit__` | Review A9: falling back to the UI after a failed relaunch would re-acquire the lease and could lose it to another process | `NotSent` → UI → re-acquire |
| 2026-11-27 | On `FallbackUi` the loop writes `submit_attempted` through the #1198 gate before the UI submit; exceptions from `on_before_submit` propagate unchanged | Reviews A3, A4: the UI transport never receives `on_before_submit`, so a cancel would otherwise be missed; a swallowed `TaskCancelledError` would turn a cancel into a fallback | Leaving the checkpoint to the transport |
| 2026-11-27 | The transport raises `DirectAttemptEnded(NotSent)` on every route mismatch, including the `upload_reference` fallback | Review A10: the in-place UI calls (`direct_http.py:482-483`, `:494-505`) would run the composer on a headless page | Keeping in-place fallbacks for uploads |
| 2026-11-27 | The cross-process lease test runs `try_acquire()` in a subprocess | Review A11: an in-process probe hits the lease registry (`profile_lease.py:300-311`) | In-process probe |
| 2026-11-27 | A run with any UI item (chain, movie, run, image batch) launches headed for the whole run | Review A6; one rule at construction is smaller than a mid-run relaunch | Relaunching headed before the first UI item |
| 2026-11-27 | An i2v `model=None` is normalised to `I2V_DEFAULT_MODEL` before the lookup | Review A7; the UI binds Veo 3.1 Lite for i2v (`_mc_settings_pane.py:151-156`), measured direct | Leaving i2v `None` unrouted |
| 2026-11-27 | Upscale routes direct only for a source in the local catalog; no network read decides the route | Review A5: a pre-launch `as29s` read would need the browser session the route is meant to choose | Resolving workflow and aspect with `as29s` during routing |
| 2026-11-27 | The worker and MCP route after resolving the effective credit cap; `image_batch.py` and the four `mcp/tools.py` constructions are in the routing task | Review A8 (the cited `daemon.py:800` is the extend path's cap resolution; the same pattern applies) | Routing on the request's cap alone |
| 2026-11-27 | Video builders port the captured app frames | The spike replayed them verbatim and both were accepted | The hand-built frames |
| 2026-11-27 | The retry is wired per task: images in 13, video in 14, upscale in 15; docs after MCP; review fixes before the live matrix | Review A12 | One task wiring all three surfaces |
| 2026-11-27 | Wave map with one owner per file per wave; Task 10 follows Task 9; `_transport.py` owned by 13 in its wave; Task 4 writes only `generation_route.py`; the exception-text scrub moves to Task 9 | Reviews B1, B2 | Parallel tasks editing the same file |
| 2026-11-27 | Route-aware construction and the default flip are one task (23 `headless=settings.headless` sites plus the other `headless=` forms), done by hand | Review B3: the site count does not justify a codemod | A separate codemod task and a separate routing task |
| 2026-11-27 | No transitional `headless=` keyword on `FlowApiClient` | Review B4 | Keeping it one release |
| 2026-11-27 | Re-arm on the wall-clock gap only, 600 s; any refusal discards that page, so the next mint uses a new page | Review B5, extended: the suspend refusal is the measured failure, and the spike showed a refused page stays refused while fresh pages are accepted; age and count rest on one observation each | Age and mint-count triggers; reusing a refused page |
| 2026-11-27 | No escalation telemetry counter; no sticky downgrade | Review B6; with a headless retry there is no visibility to keep sticky; `retried` stays in `--json` and the recorder | Both in this release |
| 2026-11-27 | Golden master pins names, signatures and the can-fail proof | Review B7: source hashes break on any move | Per-function source hashes |
| 2026-11-27 | The per-command table lives once in `docs/BROWSER_MODES.md`; the README task copies it into `README.md` and `README.zh-CN.md` (the only translation) and an offline test compares the command columns | Review B8; a follow-up would ship a Chinese README that contradicts the English one | A README-owned table; a translation follow-up |
| 2026-11-27 | Per-task e2e runs headless once on one profile; the refusal-retry e2e has a headed variant only under an explicit `headed`; the full matrix runs once in Task 24 | Review B10 | The full matrix in every task |
| 2026-11-27 | `MAX_IN_FLIGHT = 4`; a log-only `direct.start_rate_high` warning when more than 8 submits start within 120 s on one profile; enforcement deferred until warnings correlate with refusals | Review B12: 8 in flight measured clean twice; the burst refusal did not reproduce, is not billed and is retried once; the warning gathers the evidence enforcement would need | A blocking token-bucket limit; no signal at all |
| 2026-11-27 | A headed launch with no display raises `UiModeUnavailableError` (exit 28); a headless launch needs no display | SCENARIO.md row 20; exit 11 collides with `ProfileLockedError` | `ConfigurationError` (exit 11) |
| 2026-11-27 | `--browser-visibility` ships on the CLI with an MCP twin | SCENARIO.md row 22 | Env-only visibility |
| 2026-11-27 | Direct upscale only for measured source keys | `p0UkFb` was measured on one 360p clip; the menu already gives exit 22 where Flow offers no 1080p (#1338) | Sending `p0UkFb` for every source |
| 2026-11-27 | A separate documentation-honesty task (30) rewrites the 33 inventoried statements in which the project describes itself as depending on a headed browser, calls headless rejected, or frames the UI as the product; README leads with the automation purpose | The product exists for automation; those statements are contradicted by the spike and would mislead users and agents. Splitting it from Task 23 keeps reference docs and narrative rewrites as separate commits with separate file ownership | Folding the rewrites into Task 23; appending caveats to the old sections |
| 2026-11-27 | One historical note on the pre-v0.108.0 headed-browser design, in `docs/ARCHITECTURE.md`, linked from the README limitations section and AGENTS.md; current truth everywhere else | Architecture history belongs with the architecture; `BROWSER_MODES.md` stays a current-state user page | Scattered caveats; the note in `BROWSER_MODES.md` |
| 2026-11-27 | A short phrase-list test guards against the stale claims returning | A few lines, and a stale claim survives wherever nobody thought to look | No guard; a broader prose linter |
| 2026-11-27 | The v0.108.0 CHANGELOG entry is a milestone entry with a headline section, migration notes and a credit to flowfast | It changes the default transport and the browser's role, and adds a setting with a deprecated alias; flowfast's published wire map and measurements confirmed and accelerated the roadmap | A routine entry |
| 2026-11-27 | The JSON field is `retried`, the loop module `_flow_retry.py` | The retry no longer changes visibility, so "escalated" would mislead | Keeping the `escalated` name |
