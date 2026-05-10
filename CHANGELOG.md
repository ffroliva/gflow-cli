# Changelog

All notable changes to `gflow-cli` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  Files land date-partitioned under `$FLOW_CLI_OUTPUT_DIR/images/<YYYY-MM-DD>/`
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

### Changed
- `FlowApiClient.upload_image` now validates **PNG/JPEG magic bytes** and
  rejects files larger than **20 MB** before issuing the upload request.
  Existing callers (`gflow video i2v`, `gflow video batch`) inherit the
  stricter validation; previously-undocumented use of `upload_image` for
  non-image payloads no longer works (was never officially supported).

### Security
- DEBUG-level body logs now redact reCAPTCHA Enterprise tokens and other
  bearer-style fields before emission, eliminating a token-leak vector when
  users share verbose logs while filing bug reports.
- `download_image()` enforces an **SSRF host allowlist** on the signed
  `fifeUrl` returned by Flow — only Google-controlled image CDNs
  (`*.googleusercontent.com`, etc.) are followed; any other host raises
  before the GET is issued. Defends against a Flow-side bug or compromise
  redirecting downloads to an attacker-controlled origin.

## [0.2.0a1] — 2026-05-09

### Added
- **`gflow video t2v`** — generate a video from a text prompt via Veo 3.1.
  Flags: `--aspect 9:16|16:9|1:1`, `--seed`, `--output`, `--profile`, `--poll-interval`.
- **`gflow video i2v`** — generate a video from a start image + text prompt (Veo 3.1 I2V).
- **`gflow video batch`** — run a TSV manifest of video generations against one shared project.
- `flow_cli.api` package — low-level REST client (`FlowApiClient`) + value objects
  (`GenerateVideoRequest`, `VideoOperation`, `VideoStatus`) for video generation.
- `flow_cli.api.recaptcha` — reCAPTCHA Enterprise token minting via Playwright `page.evaluate`.
  `TokenMinter` caches the discovered site key per session; `mint(action)` is called immediately
  before each generation request.
- `flow_cli.manifest` — TSV manifest parser for `gflow video batch`. Supports optional
  `start_image`, `end_image`, `aspect`, `output_path` columns; skips `# `-prefixed comments.
- `FLOW_CLI_HEADLESS` env var (`bool`, default `true`). Set to `false` if reCAPTCHA refuses
  to mint tokens in headless mode (Google bot detection fallback).
- `scripts/smoke_e2e.py` — one-shot live T2V smoke test; run after `gflow auth login` to
  verify the full happy path (create project → generate_video → poll → download).
- **`CLAUDE.md`** at repo root — project memory hub for AI coding agents
  (Claude Code reads natively; Cursor/Codex/Gemini/Aider can read as reference).
- **`.claude/`** directory — repo-local Claude Code surface for maintainers.
  - `.claude/README.md` — what goes here, how to extend.
  - `.claude/commands/release.md` — `/release` slash command that automates
    version bump + CHANGELOG migration + tag + push, with quality gates.
- `flow_cli.profile_store` — profile inventory + default-profile persistence
  in `$FLOW_CLI_HOME/config.toml`. Five-step resolution chain (CLI flag > env >
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
- `flow_cli.providers.FlowProvider` and `flow_cli.models` — superseded by `flow_cli.api`.
- Legacy CLI stubs: `gflow upload`, `gflow generate`, `gflow status`, `gflow download`,
  `gflow i2v`. Replaced by the wired `gflow video` subgroup.

## [0.1.0] — _unreleased_

First skeleton. Not functional end-to-end yet.

[Unreleased]: https://github.com/ffroliva/gflow-cli/compare/v0.3.0a1...HEAD
[0.3.0a1]: https://github.com/ffroliva/gflow-cli/releases/tag/v0.3.0a1
[0.2.0a1]: https://github.com/ffroliva/gflow-cli/releases/tag/v0.2.0a1
[0.1.0]: https://github.com/ffroliva/gflow-cli/releases/tag/v0.1.0
