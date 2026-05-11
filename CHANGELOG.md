# Changelog

All notable changes to `gflow-cli` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0a1] — 2026-05-11

### Breaking — package + env-var rename

- **Python package renamed: `flow_cli` → `gflow_cli`.** All imports must
  change: `from gflow_cli...` (was `from flow_cli...`). The PyPI distribution
  name (`gflow-cli`), the CLI binary (`gflow`), and the user data directory
  (`gflow-cli/` under `platformdirs`) are unchanged.
- **Env var prefix renamed: `FLOW_CLI_*` → `GFLOW_CLI_*`.** Affected vars:
  `GFLOW_CLI_HOME`, `GFLOW_CLI_OUTPUT_DIR`, `GFLOW_CLI_PROFILE`,
  `GFLOW_CLI_HEADLESS`, `GFLOW_CLI_LOG_LEVEL`, `GFLOW_CLI_LOG_FORMAT`,
  `GFLOW_CLI_PROVIDER`, `GFLOW_CLI_TIMEOUT_SECONDS`, `GFLOW_CLI_CONCURRENCY`.
- **Backwards-compat shim.** Legacy `FLOW_CLI_*` env vars continue to work
  in v0.4.x; on first encounter the process emits a single
  `DeprecationWarning` to stderr summarising the promoted keys. The shim
  will be removed in v0.5.0 — update your `.env` files and shell exports.

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

[Unreleased]: https://github.com/ffroliva/gflow-cli/compare/v0.3.0a1...HEAD
[0.3.0a1]: https://github.com/ffroliva/gflow-cli/releases/tag/v0.3.0a1
[0.2.0a1]: https://github.com/ffroliva/gflow-cli/releases/tag/v0.2.0a1
[0.1.0]: https://github.com/ffroliva/gflow-cli/releases/tag/v0.1.0
