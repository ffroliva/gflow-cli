# Changelog

All notable changes to `gflow-cli` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `gflow_cli.exceptions` module as a standard alias for `gflow_cli.errors` — both module names resolve identically.
- `FlowApiClient.health_check()` async method — returns `True` if browser context is alive and on a Google domain; safe to call from long-lived workers without try/except.

### Changed

- `FlowApiClient.generate_image()` and `generate_images_batch()`: `project_id` is now optional (`str | None = None`). When omitted, a new Flow project is created automatically. Existing callers passing an explicit `project_id` are unaffected.

### Fixed

- `gflow auth login` now verifies a real Flow app session before reporting
  success — fixes issue #15, where a Google-only sign-in was wrongly accepted
  and later failed with HTTP 401.
- **`gflow auth login --browser internal` now fails fast when Google rejects
  Playwright's bundled Chromium**, returning `AuthBrowserRejectedError` exit
  code 14 with guidance to rerun using real Chrome
  (`gflow auth login --browser chrome`) or set
  `GFLOW_CLI_AUTH_BROWSER=chrome`.

## [0.6.0a6] — 2026-05-17

> **Stability & code-quality release.** Fixes a concurrency bug in image
> generation, restores a green CI pipeline (the test job had been hanging
> indefinitely), and clears every open SonarCloud issue so the project's
> Quality Gate passes.

### Fixed

- **Concurrent `generate_images` calls are now serialized**, and every batch
  creates a fresh Flow project — prevents project-reuse races when multiple
  image generations overlap.
- **CI test job no longer hangs.** `RealChromeStrategy` launches Chrome with
  `asyncio.create_subprocess_exec`, but its tests patched `subprocess.Popen`;
  asyncio's POSIX subprocess transport uses `Popen` internally, so the mock
  left the event loop's child watcher unresolved forever — the test job ran
  until cancelled and never wrote a coverage report. Tests now patch
  `asyncio.create_subprocess_exec` directly.
- **structlog log-capture test isolation** — a `browser_manager` test asserted
  on a log event that an earlier test had already cached onto the production
  logger chain (`cache_logger_on_first_use=True`). It now patches in a fresh
  logger proxy and passes regardless of suite order.

### Changed

- **All open SonarCloud issues resolved** and the Quality Gate now passes:
  the S6418 BLOCKER and 10× S5443 CRITICAL test findings, 16 mechanical
  issues, async-hygiene rules (S7503 / S7487 / S7493), and 5
  cognitive-complexity (S3776) extractions. The two remaining Security
  Hotspots — `random`-based retry jitter and protocol-mandated SHA-1 in
  `sapisidhash` — were reviewed and marked Safe.

### Security / Compliance

- **Removed accidentally tracked artefacts** — 7 files were untracked from git:
  `denon82/.gflow-cdp.lock`, `test_assets/debug_editor/buttons.json`,
  `test_assets/debug_settings/settings_panel.json`, and 4 AI-generated JPGs
  in `test_assets/smoke_e2e_*/`. None contained credentials or API tokens, but
  the CDP lock file exposed a profile name and browser PID and the debug JSON
  files contained Flow UI text. Files were removed from HEAD forward (no history
  rewrite — see decision rationale in `PLAN.md` ADR #3).

- **`.gitignore` hardened** — added `*.jpg`, `*.jpeg`, `**/.gflow-cdp.lock`,
  `test_assets/smoke_*/`, `test_assets/debug_*/`, and `gflow-output/` to
  prevent recurrence. Fixture allowlist added (`!test_assets/fixtures/**/*.jpg`).

- **Hygiene gate added to CI** — `scripts/ci/check_repo_hygiene.py` runs on
  every push and PR before lint. Fails if tracked files match the denylist or
  if any `scripts/**/*.py` contains a hardcoded Windows absolute path or writes
  output to `test_assets/`.

- **`.pre-commit-config.yaml` added** — ships ruff (lint + format) and the
  hygiene gate as pre-commit hooks. Install with:
  `pip install pre-commit && pre-commit install`.

- **Debug scripts de-hardcoded** — `scripts/debug_editor.py`,
  `scripts/debug_gen_settings.py`, `scripts/debug_settings.py` previously
  contained `PROFILE = r"C:\Users\ffrol\..."` (Windows username + Google
  profile name) and wrote output to `test_assets/`. Replaced with argparse
  `--profile` flag + `auth.profile_dir(args.profile)` and output redirected to
  `tmp/debug/<name>/`.

- **CI workflow scrubbed** — removed a hardcoded profile name (`denon82`) from
  a comment in `.github/workflows/ci.yml`.

### CI / Tooling

- **GitHub Actions migrated to Node.js 24** ahead of the June 2026 forced
  migration (`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24`).
- **SonarCloud Quality Gate badge** added to the README.

## [0.6.0a5] — 2026-05-16

> **CLI transport proven end-to-end.** The `ui_automation` transport now
> generates images correctly from the `gflow image t2i` command — count,
> aspect ratio, and file download all work. Root cause of the persistent 403
> was `headless=True`; reCAPTCHA Enterprise immediately rejects headless
> Chromium.

### Fixed

- **`headless` default changed `True` → `False`** in `config.py` and
  `FlowApiClient.__init__` — the `ui_automation` transport requires a headed
  (visible) Chrome window; reCAPTCHA Enterprise scores headless browsers as
  bots and returns an immediate 403 on `batchGenerateImages`.
- **13 unit test mock regressions** fixed after the v0.6.0a4 transport rewrite:
  - `add_init_script = AsyncMock()` added to `_patch_playwright` and
    `fake_context` fixtures (`test_client.py`, `test_concurrency.py`).
  - `keyboard.insert_text = AsyncMock()` added to `_make_prompt_page`
    (transport now uses `insert_text` instead of `type`).
  - `_FakeHttpxResponse.headers` added (download auto-detects `.jpg`/`.png`
    from `Content-Type`).
  - `_capture_batch_response` / `_await_captured` return `list[dict]` —
    test assertions updated throughout `test_ui_automation.py`.

## [0.6.0a4] — 2026-05-17

> **Unified output resolution + batch orchestration refactor.** This release
> aligns the CLI output structure across all commands and refactors the batch
> runner to be more generic, preparing the codebase for Phase 6.

### Added

- **`resolve_batch_output_dir` helper** in `paths.py` — centralizes the
  date-partitioned output directory logic used by all generation commands.
- **`parse_batch_item_dict` helper** in `image_batch.py` — deduplicates JSON
  prompt validation between `gflow run` and other batch sources.

### Changed

- **`gflow run` output directory** — now defaults to date-partitioned
  `$GFLOW_CLI_OUTPUT_DIR/images/<YYYY-MM-DD>/` instead of the legacy
  `out/<UTC-timestamp>/`, matching the `gflow image` convention.
- **Refactored `run_image_batch`** into a generic `run_sequential_batch`
  orchestrator — now accepts a swappable worker callback, allowing for uniform
  video and image batch handling in the future.

### Fixed

- Removed ~80 lines of duplicate validation logic from `cli_run.py`.
- Corrected test imports and expectations for unified output resolution.

## [0.6.0a3] — 2026-05-17

> **Deterministic timeouts + agent-friendly exit codes.** This release hardens
> the auth login flow for unattended / agentic use: timeouts now raise distinct
> errors with dedicated exit codes instead of silently swallowing failures.

### Added

- **`AuthLoginTimeoutError`** (exit code **12**) — raised by both strategies
  when the user/agent does not complete sign-in within `timeout_seconds`.
  Distinct from `ConfigurationError` (11) and `SecurityError` (13) so agents
  can branch on failure type without parsing stderr.
- **`SecurityError`** exit code **13** — now registered in `EXIT_CODE_MAP`.
- **`timeout_seconds=600` parameter** on both `RealChromeStrategy` and
  `InternalChromiumStrategy` — configurable upper bound for the login window.
- **Broad `GFlowError` catch** in `auth_login` CLI command — previously only
  caught `ConfigurationError`; now looks up any `GFlowError` subclass in
  `EXIT_CODE_MAP` and exits with the correct code plus a `remediation_hint`.

### Fixed

- `InternalChromiumStrategy` had an infinite `while True:` polling loop that
  never timed out; replaced with a bounded loop that raises
  `AuthLoginTimeoutError` on expiry.
- `auth login --browser chrome` when Chrome is missing now exits with code
  **11** (ConfigurationError) instead of 1.

## [0.6.0a2] — 2026-05-16

> **Real Chrome auth strategy — G12 block resolved.** This release restores
> `gflow auth login` reliability by implementing a new **Passive Capture**
> strategy. This method providing a 100% clean browser environment by launching
> your system's real Google Chrome as a standard process, completely bypassing
> Google's bot-detection.

### Added

- **`--browser [auto|chrome|internal]` flag** on `gflow auth login` — selects
  the browser strategy. `chrome` uses real system Chrome (**Passive Capture**).
  `internal` falls back to bundled Chromium. `auto` (default) probes for real
  Chrome and falls back gracefully.
- **`GFLOW_CLI_AUTH_BROWSER` env var** — overrides the browser strategy without
  a CLI flag.
- **`RealChromeStrategy`** (`src/gflow_cli/auth/real_chrome.py`) — zero-automation
  login flow: launches clean Chrome, waits for user to close window, then extracts
  the session.
- **`InternalChromiumStrategy`** — extracted from the previous `auth.py` monolith
  as an explicit fallback strategy.
- **`AuthStrategyFactory`** — routes `auto`/`chrome`/`internal` to the
  appropriate strategy based on system state.

### Fixed

- **G12 bot-detection block** — Google's "browser not secure" rejection (`/v3/signin/rejected`)
  is bypassed by the Passive Capture workflow. By removing all automation signals
  (CDP, WebDriver flags) during login, the browser is indistinguishable from a
  regular user session.
- **Privacy Guard** — `RealChromeStrategy` validates that `profile_dir` is inside
  `GFLOW_CLI_HOME` and raises `SecurityError` if it is not, preventing accidental
  interference with your primary personal Chrome profile.
  use of the user's primary system Chrome profile.
- **`ConfigurationError` on missing Chrome** — clear "Chrome binary not found"
  message with install guidance when `--browser chrome` is requested but Chrome
  is not on the system.
- **Two pyright `TypedDict` errors** in cookie access (`c["name"]` → `c.get("name")`).

### Changed

- `src/gflow_cli/auth.py` promoted to `src/gflow_cli/auth/` package with
  `__init__.py`, `base.py`, `factory.py`, `internal_chromium.py`,
  `real_chrome.py`, `strategies.py`.
- `gflow auth login` now prints the launch strategy announcement before opening
  any browser window.



> **Shell-friendly multi-prompt `t2i` + performance hardening.** This release 
> promotes `gflow image t2i` to a variadic command that can consume multiple 
> prompts from positional arguments, a line-delimited text file, or standard 
> input. Core generation logic has been consolidated into a shared 
> `image_batch` module, ensuring architectural consistency between shell runs 
> and JSON-described batches. This version also ships critical resource 
> cleanup fixes for SQLite connections and OOM protection for stdin streams.

### Added

- **Variadic `gflow image t2i`** — now accepts multiple positional prompts. 
  Example: `gflow image t2i "prompt 1" "prompt 2"`.
- **`--prompts-file <PATH>` and `--stdin`** — read batches of prompts from 
  text files or pipes. All prompts in a batch share a single Flow session 
  and project, significantly reducing reCAPTCHA and project-init overhead.
- **Shared `image_batch` logic** (`src/gflow_cli/image_batch.py`) — unified 
  orchestration, validation, and rendering for all multi-prompt generation 
  surfaces.
- **Memory safety for stdin** — bounded read on standard input prevents 
  memory exhaustion when piping large or infinite streams.
- **`examples/multi_prompt_t2i.py` + `examples/sample_prompts.txt`** — 
  runnable template for the new shell-multi-prompt surface.

### Fixed

- **Resource leaks in SQLite** — ensured all `sqlite3` connections are 
  properly closed via `try...finally` blocks, resolving resource exhaust 
  warnings and potential hangs in long-running processes.
- **Output directory partitioning** — `t2i` batches now correctly land in 
  date-partitioned folders (`images/YYYY-MM-DD/`) by default, aligning 
  with the core design spec.

### Changed

- **CLI validation alignment** — `t2i` and `i2i` subcommands now use 
  authoritative domain constants for model, aspect, and count validation, 
  ensuring UI help text and defaults stay in perfect sync with the engine.

## [0.5.0a1] — 2026-05-12

> **Pluggable image transport + JSON-described batch runs.** The image
> generation surface now ships a new default `ui_automation` transport —
> a Playwright-driven UI mimicry strategy validated end-to-end against
> real Flow on a Google AI Pro/Ultra profile. Three earlier HTTP
> transport strategies (`evaluate_fetch`, `bearer`, `sapisidhash`) move
> into a new `experimental/` subpackage; they remain importable for
> research but are hidden from the CLI by default. New top-level
> `gflow run --config <file>` command drives JSON-described sequential
> batches through one shared session.

### Added

- **`UiAutomationTransport`** (`gflow_cli.api.transports.ui_automation`)
  — new default transport. Drives the Flow editor on a logged-in
  profile through a Playwright-managed persistent context (internal CDP
  port; no externally-exposed debug port). Mirrors the validated
  reference flow in `scripts/smoke_worker_style.py`.
- **`gflow run --config <file>`** — sequential JSON-described batch
  command. Schema covers `profile`, `transport`, `output_dir`, and a
  `prompts` list (1–50 entries) with per-prompt `text`,
  `aspect_ratio`, `model`, `count`, and `output_filename`. Supports
  `--continue-on-error` (default) and `--fail-fast` semantics; final
  exit code is the max per-prompt exit code. ONE `FlowApiClient`
  session wraps the whole loop so the browser/project persist across
  prompts.
- **`examples/` directory** — three runnable scripts (`single_image_t2i.py`,
  `batch_from_config.py`) + a copy-and-edit `sample_config.json` + an
  index `examples/README.md`. All sanitised: no hardcoded profile
  names, generic placeholder prompts, parameterised via `--profile` /
  `$GFLOW_EXAMPLE_PROFILE`.
- **Opt-in real-Flow smoke test** at `tests/smoke/test_real_flow.py`,
  gated by `GFLOW_E2E=1` + `GFLOW_E2E_PROFILE`. Runs the full
  `UiAutomationTransport` flow against real Flow and asserts a
  non-trivial PNG was written.
- **`EXPERIMENTAL_TRANSPORTS` constant** + **`transport_choices()`
  helper** in `gflow_cli.api.transports`. The factory continues to
  accept every registered key; the CLI `--transport` Choice list is
  the gated surface (default = `ui_automation` only;
  `GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1` expands to all four).
- **Download host allow-list** in `UiAutomationTransport._download`
  (`googleusercontent.com`, `googleapis.com`, `google.com` suffix
  match). `follow_redirects=False`. Prevents session cookies from
  reaching a non-Google host through a malformed or compromised
  `fifeUrl`.

### Changed

- **Default `--transport` flag** flipped from `evaluate_fetch` to
  `ui_automation` across `gflow image t2i`, `gflow image i2i`, and
  `gflow image upload`. The change is transparent to existing scripts
  unless they pinned `--transport evaluate_fetch` explicitly.
- **`evaluate_fetch` / `bearer` / `sapisidhash` strategies moved** to
  `gflow_cli.api.transports.experimental.*`. Public registry keys
  (the strings used by `make_transport()` and the
  `GFLOW_CLI_TRANSPORT` env var) are unchanged. Import paths within
  the package are the only user-visible delta.
- **Debug screenshots** captured by the strategy on `_enter_editor` /
  `_send_prompt` failures are now **viewport-only**
  (`full_page=False`) and emit a `WARNING` log line noting the file
  may contain identifying information from the authenticated session.

### Fixed

- Listener-attach race in `generate_images`. The earlier
  `asyncio.create_task(_capture_batch_response(page))` scheduled the
  listener registration AFTER the next event-loop tick; on a busy
  loop the prompt click could fire before the listener attached,
  causing the capture to time out. Refactored into a synchronous
  `_attach_batch_response_listener(page)` + an `async
  _await_captured(captured, ...)`. No more orphaned task on partial
  failure.

### Removed

- Dead `_extract_image_urls(response)` helper on
  `UiAutomationTransport` and its five tests.
  `generate_images` parses `body.media[]` directly through
  `GeneratedImage.from_response_item`; the parallel helper was
  unreachable.

### Documentation

- `BrowserManager` module docstring updated to make explicit that the
  module is retained for research / non-Flow use, not on the
  v0.5.0a1 image-generation critical path. No behavior change.
- README "Project status" table updated with v0.5.0a1 row.
- `docs/USAGE.md` gains a `gflow run` section.

## [0.4.0a2] — 2026-05-11

> **Documentation polish.** Same release surface as v0.4.0a1; this tag fixes
> a doc-council pass: four broken Python snippets in the README, a shell
> exit-code branching example that silently dropped failures, a stale anchor
> link in `AUTHENTICATION.md`, three USER_GUIDE journeys the target audience
> needs (credit budgeting, pipeline wiring, error recovery), and a sweep of
> "planned v0.3 / v0.4" callouts across 9 files that had been overtaken by
> the Phase 4 release. No code changes. No tests changed.

### Fixed (docs)

- **`README.md` Python quick-start snippet rewritten** — the prior block had
  four real bugs (`from gflow_cli.paths import profile_dir` → import error,
  `upload_image(path, project_id)` args reversed, `generate_video(prompt=,
  start_asset=, aspect=)` wrong kwargs, `poll_video_status` method does not
  exist). Snippet now uses the same invocation pattern as
  `gflow_cli.cli_video._run_i2v` and would actually run.
- **`docs/USAGE.md` exit-code branching example** — `if ! cmd; then case $?`
  always saw `0` because the `if` consumed the exit code; rewritten to
  capture `rc=$?` first. Exit code `2` re-labelled "Bad CLI usage" (auth is
  exit `3`).
- **`docs/AUTHENTICATION.md` anchor link** to the Phase 4 PLAN heading
  fixed (was `#phase-4--hardening--post-v030a1`, did not exist).
- **`CHANGELOG.md` footer** — added `[0.4.0a1]:` and `[0.4.0a2]:` compare
  links; reset `[Unreleased]` to compare from v0.4.0a2.
- **`docs/USER_GUIDE.md` Journey 2** endpoint name `flowMedia:batchGenerateVeoVideo`
  → real route `/v1/video:batchAsyncGenerateVideoText`.
- **`docs/USER_GUIDE.md` Journey 5.2** invalid placeholder UUID
  (`media-uuid-abc-...`) → canonical hex shape.
- **`docs/USER_GUIDE.md` Journey 7.1** `echo $?` placement — previously
  captured the exit code of an intermediate command, not the failing batch.
- **`docs/USER_GUIDE.md` Journey 7.3** softened the "Flow doesn't re-bill"
  claim — billing is a private-API contract we cannot assert.
- **`KNOWN_ISSUES.md` same-profile examples** swapped `gflow image batch`
  (does not exist) → `gflow video batch`.

### Added (docs)

- **`docs/USER_GUIDE.md` Journey on credit budgeting** — rule-of-thumb credit
  cost per `video t2v` / `video i2v` / `image t2i` / `image i2i` call, links
  to Flow's credit-balance UI, batch-cost math example.
- **`docs/USER_GUIDE.md` Journey on wiring outputs into a pipeline** —
  deterministic output-dir layout, `find` (POSIX) + `Get-ChildItem`
  (PowerShell) recipes, `ffmpeg` consumer example.
- **`docs/USER_GUIDE.md` Journey on `ContentPolicyError` / `RateLimitError`
  recovery** — what each error means, how long to wait, prompt-rewrite
  pattern, when retry is futile.
- **`README.md` doc-nav** now links `docs/USER_GUIDE.md` (was missing).
- **`README.md` Stack table** lists `tenacity` and `structlog` (were
  shipped in v0.4.0a1 but not in the stack overview).

### Changed (docs)

- **`CHANGELOG.md` [0.4.0a1] section reordered** — `Added — Phase 4
  hardening` now appears before `Breaking`. The hardening release was
  user-visible value; the env-var rename was a one-line update for most
  users.
- **Per-class exit codes 3–7** promoted from a bullet to its own "Migration
  notes" subsection in the [0.4.0a1] block.
- **Version-time-warp sweep across 9 files** — every `(planned v0.3)`,
  `(planned v0.4)`, `v0.3+ will add`, `v0.4 will add`, and `current scaffold
  ignores this` line either describes shipped behaviour or points at v0.5+.
  Files touched: `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `PLAN.md`,
  `KNOWN_ISSUES.md`, `CONFIGURATION.md`, `AUTHENTICATION.md`,
  `ARCHITECTURE.md`, `DISCLAIMER.md`, `SECURITY.md`, `CONTRIBUTING.md`,
  `.env.template`.
- **`docs/ARCHITECTURE.md` Concurrency section** describes the shipped
  `asyncio.Queue` Page pool, not the target-DDD `Semaphore` model.
- **`docs/ARCHITECTURE.md` Observability section** describes the shipped
  `error_raised` / `error_unhandled` event names, not the target-DDD
  dot-path names.
- **`docs/ARCHITECTURE.md` DDD error class names** annotated as target —
  shipped Phase 4 names (`AuthExpiredError`, `RateLimitError`,
  `ContentPolicyError`, `NetworkError`, `WireFormatError`) listed alongside.
- **`docs/CONFIGURATION.md` `GFLOW_CLI_CONCURRENCY`** describes shipped
  behaviour (per-worker Page pool, `asyncio.gather` fan-out).

## [0.4.0a1] — 2026-05-11

> **Phase 4 hardening release.** Concurrency, retry/backoff, typed errors,
> and structured logs ship — your existing scripts keep running (the
> `FLOW_CLI_*` env-var shim is in place until v0.5.0). The user-visible
> contract that changed: shell scripts can now branch on stable per-class
> exit codes (3–7) for auth / rate-limit / content-policy / network /
> wire-format failures.

### Added — Phase 4 hardening

- **Per-worker Playwright Page pool.** `FlowApiClient.__aenter__` opens
  `Settings.concurrency` Pages inside a single persistent BrowserContext.
  Operations check out a Page via `asyncio.Queue` (FIFO, bounded by
  `maxsize=N`). `GFLOW_CLI_CONCURRENCY=N` (1–16) now actually parallelizes.
- **`gflow video batch` fans out via `asyncio.gather`** over manifest
  entries — was sequential pre-v0.4.0a1.
- **`tenacity`-based retry layer** (3 attempts, exponential jittered
  backoff 1s±25% → 2s±25% → 4s±25%) on 5xx / 429 / `playwright.async_api.Error`
  / `TimeoutError`. `Retry-After` honoured, **capped at 60 s**. `reraise=True`
  so the original exception's `__cause__` chain is preserved. reCAPTCHA
  token re-minted **inside the retry loop, every attempt**, on the worker's
  own Page.
- **RFC 9457 Problem Details exception hierarchy:**
  `GFlowError → FlowApiError → {AuthExpiredError, RateLimitError,
  ContentPolicyError, NetworkError, WireFormatError}`. `except FlowApiError`
  catches the typed subclasses (back-compat). Each carries
  `problem_type` URI, `title`, `status`, `detail`, `instance`
  (`gflow:error:<correlation_id>`), `remediation_hint`, and `route`.
  `to_problem_details()` serializes to the RFC 9457 JSON shape.
- **Per-class exit codes**: 3 (auth) / 4 (rate-limit) / 5 (content-policy) /
  6 (network) / 7 (wire-format). Exit 1 = unhandled. Exit 130 = SIGINT.
- **`WireFormatError` discovery payload** — `route_name`, `http_status`,
  `content_type`, `top_level_keys`, `body_prefix_redacted` so log mining
  can propose new error subclasses for unexpected response shapes.
- **`structlog` bootstrap** with TTY auto-detection (text on TTY, JSON when
  piped). `show_locals=False` mandatory on the exception renderer so frame
  locals (which may contain auth tokens) NEVER reach the log stream.
  `correlation_id` + `cli_version` bound via `contextvars` at the process
  boundary.
- **`error_raised` and `error_unhandled` events.** `error_raised` for caught
  `GFlowError`s — carries Problem Details. `error_unhandled` for anything
  else — privacy-safe: hashes message + stack with SHA-256, never logs raw
  payload.
- **12 `pytest-bdd` scenarios** across `auth.feature`, `video.feature`,
  `image.feature` — all use a mocked `FlowApiClient`. A
  `_forbid_live_playwright` autouse tripwire fails any scenario that
  accidentally tries to start a real browser.

### Migration notes — stable exit codes

Shell scripts that previously branched on exit code `1` for any failure
can now distinguish the failure class. The mapping is locked by an
ordering-invariant test in `tests/test_errors.py`:

| Exit | Error class           | Meaning                              | Retry?         |
|------|-----------------------|--------------------------------------|----------------|
| 0    | —                     | Success                              | —              |
| 1    | (unhandled)           | Bug. Filed via `error_unhandled`     | No             |
| 2    | (Click)               | Bad CLI usage / missing arg          | Fix the call   |
| 3    | `AuthExpiredError`    | Session cookies invalidated          | After re-login |
| 4    | `RateLimitError`      | Flow returned 429                    | Yes, with wait |
| 5    | `ContentPolicyError`  | Prompt blocked upstream              | After rewrite  |
| 6    | `NetworkError`        | DNS / TLS / 5xx after retry          | Yes            |
| 7    | `WireFormatError`     | Response shape changed (Flow update) | File a bug     |
| 130  | (SIGINT)              | User Ctrl-C                          | —              |

See [`docs/USAGE.md § Exit codes`](docs/USAGE.md#exit-codes) for a
shell-script template that branches on these codes.

### Breaking — package + env-var rename

- **Python package renamed: `flow_cli` → `gflow_cli`.** All imports must
  change: `from gflow_cli...` (was `from flow_cli...`). The PyPI distribution
  name (`gflow-cli`), the CLI binary (`gflow`), and the user data directory
  (`gflow-cli/` under `platformdirs`) are unchanged.
- **Env var prefix renamed: `FLOW_CLI_*` → `GFLOW_CLI_*`.** Affected vars:
  `GFLOW_CLI_HOME`, `GFLOW_CLI_OUTPUT_DIR`, `GFLOW_CLI_PROFILE`,
  `GFLOW_CLI_HEADLESS`, `GFLOW_CLI_LOG_LEVEL`, `GFLOW_CLI_LOG_FORMAT`,
  `GFLOW_CLI_PROVIDER`, `GFLOW_CLI_TIMEOUT_SECONDS`, `GFLOW_CLI_CONCURRENCY`,
  `GFLOW_CLI_GEMINI_API_KEY`.
- **Backwards-compat shim.** Legacy `FLOW_CLI_*` env vars continue to work
  in v0.4.x; on first encounter the process emits a single
  `DeprecationWarning` to stderr summarising the promoted keys. The shim
  will be removed in v0.5.0 — update your `.env` files and shell exports.

### Changed

- `FlowApiError` re-parented under `GFlowError`. Legacy positional
  constructor `FlowApiError(status, body, *, route)` preserved (auto-detected
  via `isinstance(args[0], int) and not isinstance(args[0], bool)`).
- `_resolve_profile` and `_make_provider_dir` deduped — relocated from
  `cli_image.py` + `cli_video.py` to `gflow_cli._cli_helpers`. AST-based
  drift guard in `tests/cli/test_helpers.py` prevents regression.
- All `logging.*` callsites in `src/` migrated to `structlog`. The
  remaining `print()` in `auth.py` swapped to Rich `console.print()`.

### Internal

- New module: `gflow_cli.errors` (RFC 9457 hierarchy + `EXIT_CODE_MAP`).
- New module: `gflow_cli.observability` (structlog bootstrap + event
  emitters; `show_locals=False` via
  `ExceptionRenderer(ExceptionDictTransformer(show_locals=False))`).
- New module: `gflow_cli.api._retry` (tenacity `AsyncRetrying` +
  `Retry-After` parser, capped at 60 s).
- New module: `gflow_cli._cli_helpers` (shared CLI-boundary handlers +
  profile/provider helpers).

## [0.3.0a1] — 2026-05-10

### Added
- **`gflow image upload PATH`** — upload a single local image (PNG/JPEG) into a
  fresh Flow project and print the asset UUID + dimensions Flow inferred. The
  UUID is reusable as a starting frame for `gflow image i2i --ref` and
  `gflow video i2v`.
- **`gflow image t2i PROMPT`** — text-to-image generation (1–4 images per call)
  via Google Flow's Imagen / Nano Banana models.
  Flags: `--model {nano2|nano-pro|image4}`, `--aspect {9:16|16:9|1:1|4:3|3:4}`,
  `-n/--count` (1–4), `--seed` (single-image only), `--out DIR`, `--profile`.
  Files land date-partitioned under `$GFLOW_CLI_OUTPUT_DIR/images/<YYYY-MM-DD>/`
  by default; `--out DIR` writes flat as `<DIR>/<media_name>_<n>.png`.
- **`gflow image i2i PROMPT --ref PATH_OR_UUID`** — image-to-image generation
  with one or more reference images. Each `--ref` is classified at the CLI
  boundary: case-insensitive 8-4-4-4-12 hex UUIDs are passed through verbatim
  (no upload), anything else is canonicalized (symlinks resolved at validation
  time) and uploaded before use. `--ref` is repeatable; UUIDs and paths can mix
  freely on the same call. Same flag set as `t2i` otherwise.
- **Multi-image fan-out** — `t2i` / `i2i` with `-n {2..4}` mint a single shared
  `batch_id` and issue N parallel POSTs (one per shot, each with its own random
  seed). Same-batch images share the prompt + refs; per-shot variation comes
  from independent seeds.
- **Three image models** wired behind CLI aliases:
  `nano2` → `NARWHAL` (Nano Banana 2; default, fast/balanced),
  `nano-pro` → `GEM_PIX_2` (Nano Banana Pro; higher quality),
  `image4` → `IMAGEN_3_5` (Imagen 4; photoreal-leaning).
- **Five aspect ratios** for image generation: `9:16`, `16:9`, `1:1`, `4:3`,
  `3:4` (default `9:16`, matching the Flow web UI).
- `download_image()` on `FlowApiClient` — direct download of a generated
  image's signed `fifeUrl` to disk. Streams to a temp file and atomically
  renames on success; enforces an SSRF host allowlist (only Google-controlled
  CDNs accepted).
- `scripts/smoke_image.py` — live single-image E2E smoke script (image
  counterpart of `scripts/smoke_e2e.py` for video). Run after
  `gflow auth login` to exercise the full happy path: project create →
  `batchGenerateImages` → fifeUrl download.

### Changed
- `FlowApiClient.upload_image` now validates **PNG/JPEG/WebP/GIF magic
  bytes** and rejects files larger than **20 MB** before issuing the upload
  request. Existing callers (`gflow video i2v`, `gflow video batch`) inherit
  the stricter validation; previously-undocumented use of `upload_image` for
  non-image payloads no longer works (was never officially supported).
- Project renamed `flow-cli` → `gflow-cli` across all docs and source. The
  PyPI package and GitHub repo were already at the new name in v0.2.0a1;
  this commit completes the in-source rename. Local clones may want to
  rename their working directory to match `gh clone https://github.com/ffroliva/gflow-cli`
  behavior.

### Security
- DEBUG-level body logs now redact reCAPTCHA Enterprise tokens and other
  bearer-style fields before emission, eliminating a token-leak vector when
  users share verbose logs while filing bug reports.
- `download_image()` enforces an **SSRF host allowlist** on the signed
  `fifeUrl` returned by Flow — only Google-controlled image CDNs
  (`*.googleusercontent.com`, etc.) are followed; any other host raises
  before the GET is issued. Defends against a Flow-side bug or compromise
  redirecting downloads to an attacker-controlled origin.
- `project_id` allowlist regex `^[A-Za-z0-9-]{1,128}$` on
  `batch_generate_images_url` — closes percent-encoded slash (`%2F`),
  Unicode-lookalike (U+FF0F / U+2215 / U+29F8), and CRLF/NUL injection
  bypasses that the previous denylist guard let through.

### CI
- Test matrix now includes Python 3.13 alongside 3.11 and 3.12.

## [0.2.0a1] — 2026-05-09

### Added
- **`gflow video t2v`** — generate a video from a text prompt via Veo 3.1.
  Flags: `--aspect 9:16|16:9|1:1`, `--seed`, `--output`, `--profile`, `--poll-interval`.
- **`gflow video i2v`** — generate a video from a start image + text prompt (Veo 3.1 I2V).
- **`gflow video batch`** — run a TSV manifest of video generations against one shared project.
- `gflow_cli.api` package — low-level REST client (`FlowApiClient`) + value objects
  (`GenerateVideoRequest`, `VideoOperation`, `VideoStatus`) for video generation.
- `gflow_cli.api.recaptcha` — reCAPTCHA Enterprise token minting via Playwright `page.evaluate`.
  `TokenMinter` caches the discovered site key per session; `mint(action)` is called immediately
  before each generation request.
- `gflow_cli.manifest` — TSV manifest parser for `gflow video batch`. Supports optional
  `start_image`, `end_image`, `aspect`, `output_path` columns; skips `# `-prefixed comments.
- `GFLOW_CLI_HEADLESS` env var (`bool`, default `true`). Set to `false` if reCAPTCHA refuses
  to mint tokens in headless mode (Google bot detection fallback).
- `scripts/smoke_e2e.py` — one-shot live T2V smoke test; run after `gflow auth login` to
  verify the full happy path (create project → generate_video → poll → download).
- **`CLAUDE.md`** at repo root — project memory hub for AI coding agents
  (Claude Code reads natively; Cursor/Codex/Gemini/Aider can read as reference).
- **`.claude/`** directory — repo-local Claude Code surface for maintainers.
  - `.claude/README.md` — what goes here, how to extend.
  - `.claude/commands/release.md` — `/release` slash command that automates
    version bump + CHANGELOG migration + tag + push, with quality gates.
- `gflow_cli.profile_store` — profile inventory + default-profile persistence
  in `$GFLOW_CLI_HOME/config.toml`. Five-step resolution chain (CLI flag > env >
  config > auto-select > raise) with named exceptions
  (`NoProfilesError`, `NoDefaultProfileError`).
- New auth subcommands: bare `gflow auth`, `gflow auth list`, `gflow auth use <name>`,
  `gflow auth logout [--profile NAME] [-y]`. First login auto-sets default profile.
- `KNOWN_ISSUES.md` at repo root — open/mitigated/resolved issues with workarounds.
- `docs/` tree (INDEX, AUTHENTICATION, CONFIGURATION, ARCHITECTURE, USAGE, SECURITY).
- `.env.template` documenting every supported env var.
- GitHub Actions CI: ruff, pyright, pytest on Python 3.11 and 3.12.
- GitHub Actions release workflow: tag-triggered PyPI publish via Trusted Publishing.
- MIT license, comprehensive README, [`DISCLAIMER.md`](DISCLAIMER.md), [`CONTRIBUTING.md`](CONTRIBUTING.md).
- [`skills/gflow-cli/SKILL.md`](skills/gflow-cli/SKILL.md) — installable Claude Code Skill.

### Removed
- `gflow_cli.providers.FlowProvider` and `gflow_cli.models` — superseded by `gflow_cli.api`.
- Legacy CLI stubs: `gflow upload`, `gflow generate`, `gflow status`, `gflow download`,
  `gflow i2v`. Replaced by the wired `gflow video` subgroup.

## [0.1.0] — _unreleased_

First skeleton. Not functional end-to-end yet.

[Unreleased]: https://github.com/ffroliva/gflow-cli/compare/v0.6.0a6...HEAD
[0.6.0a6]: https://github.com/ffroliva/gflow-cli/compare/v0.6.0a5...v0.6.0a6
[0.6.0a5]: https://github.com/ffroliva/gflow-cli/compare/v0.6.0a4...v0.6.0a5
[0.6.0a1]: https://github.com/ffroliva/gflow-cli/compare/v0.5.0a1...v0.6.0a1
[0.5.0a1]: https://github.com/ffroliva/gflow-cli/compare/v0.4.0a2...v0.5.0a1
[0.3.0a1]: https://github.com/ffroliva/gflow-cli/releases/tag/v0.3.0a1
[0.2.0a1]: https://github.com/ffroliva/gflow-cli/releases/tag/v0.2.0a1
[0.1.0]: https://github.com/ffroliva/gflow-cli/releases/tag/v0.1.0
