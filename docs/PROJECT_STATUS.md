# Project Status

> Where gflow-cli is in its lifecycle, by release. Updated on every signed tag.

## Current release

**v0.8.1 — alpha (latest on PyPI).** Image (T2I / I2I / upload) + Video T2V / I2V / R2V live end-to-end on the `ui_automation` transport against live Pro/Ultra accounts, with a video `--model` picker (5 Veo models) and `--duration` / `--count`. Only video `batch` (manifest runner) is still queued for Phase B — use a shell for-loop until then ([USAGE](USAGE.md#gflow-video-batch)). Three earlier HTTP transport strategies (`evaluate_fetch` / `bearer` / `sapisidhash`) are named in the codebase history; the actual modules have not been extracted into a subpackage. The production path is `ui_automation`.

**Develop (unreleased, post-v0.8.1):** PR #37 (Sonar refactor), PR #46 (README badges), PR #48 (i2v/r2v + t2v `--model`/`--duration`/`--count` + image `--model` no-op fix), PR #51 (`GFLOW_CLI_LOCALE` env override, prep for issue #24). Targets v0.9.0.

## Milestone history

| Milestone | Status |
|---|---|
| Repo scaffold, CI, license, README, disclaimer | ✅ done |
| Auth login flow (one-time browser capture) | ✅ done |
| Video: `t2v` / `i2v` / `batch` (Veo 3.1) | ✅ done (v0.2.0a1) |
| Image generation (T2I/I2I, 1–4 per call, 5 ratios, 3 models) | ✅ done (v0.3.0a1) |
| End-to-end smoke test against live Flow | ✅ done |
| First public alpha release on PyPI | ✅ done (v0.2.0a1) |
| Batch concurrency / per-worker Page pool (`GFLOW_CLI_CONCURRENCY=N`) | ✅ done (v0.4.0a2) |
| Typed errors (RFC 9457 Problem Details) + per-class exit codes 3–7 | ✅ done (v0.4.0a2) |
| Retry / backoff + reCAPTCHA re-mint inside the retry loop | ✅ done (v0.4.0a2) |
| Structured logs (`structlog`, JSON on pipe) | ✅ done (v0.4.0a2) |
| Pluggable image transport + `ui_automation` default strategy | ✅ done (v0.5.0a1) |
| `gflow run --config <file>` sequential JSON batches | ✅ done (v0.5.0a1) |
| `examples/` directory with runnable single-image + batch scripts | ✅ done (v0.5.0a1) |
| Shell multi-prompt `gflow image t2i` (`PROMPT...`, `--prompts-file`, `--stdin`) | ✅ done (v0.6.0a1) |
| Downstream-worker ergonomics (`out_dir`, `health_check()`, optional `project_id`, `BrowserSessionClosedError`) | ✅ done (v0.7.0) |
| Signed-tag release verification + first stable (`v0.7.0`) | ✅ done (v0.7.0) |
| `gflow video t2v` restored on `ui_automation` with first-class video download | ✅ done (v0.7.0 unreleased → v0.8.0) |
| Image/video mode-switch symmetry + live verify on ffroliva (PR #40) | ✅ done (v0.8.0) |
| README + AGENTS.md + llms.txt refresh, docs governance | ✅ done (v0.8.1) |
| `gflow video t2v` model picker (5 Veo models) + `--duration` / `--count` | ✅ done (Unreleased) |
| `gflow video i2v` (start + optional end frame) on `ui_automation` | ✅ done (Unreleased) |
| `gflow video r2v` (reference-to-video, model-aware ref cap omni≤7 / veo≤3) | ✅ done (Unreleased) |
| `gflow image t2i/i2i --model` actually selects the model (was a no-op) | ✅ done (Unreleased) |
| `gflow video batch` (TSV manifest) on `ui_automation` | ⏳ Phase B |
| Persistence layer (stay-mounted batch sessions across project boundaries) | ⏳ Phase B |
| Provider abstraction for official Veo 3.1 API | ⏳ planned |
| Signed-tag CI verification automation (no manual signing in CI yet) | ⏳ planned |

## What's new in each release

For per-release deltas see [CHANGELOG.md](../CHANGELOG.md). Per-release evidence files (live verification, screenshots, smoke logs) live under `docs/LIVE_VERIFICATION_*.md`.

## Lifecycle policy

- **Alpha (`0.x.y`)** — current. APIs may change between minor versions; breaking changes are noted in the changelog.
- **`1.0.0`** — stable surface. Breaking changes require MAJOR bump + migration notes.
- **Patch releases** — bug fixes, doc refreshes (like v0.8.1), and other backward-compatible changes.

See [RELEASE.md](../RELEASE.md) for the full release protocol and the prerelease vs full-release policy.
