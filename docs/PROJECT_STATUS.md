# Project Status

> Where gflow-cli is in its lifecycle, by release. Updated on every signed tag.

## Current release

**v0.11.0 — alpha.** v0.11.0 is a reliability release — it repairs `gflow video i2v` (on v0.10.0 every i2v run silently produced text-to-video output that ignored the start/end frames, issue #125), fixes create-project generation under Flow's "Agent" composer mode, and hardens image-model selection for non-English Flow UIs (#94). The v0.10.0 feature baseline stands: Image (T2I / I2I / upload) + Video T2V / I2V / R2V live end-to-end on the `ui_automation` transport against live Pro/Ultra accounts, with a video `--model` picker (5 Veo models) and `--duration` / `--count`. New in v0.10.0: machine-readable `--json` output across every generation command (`image t2i/i2i`, `video t2v/i2v/r2v`, `auth list`) plus a `gflow models` catalog command, so worker schedulers can drive the CLI without scraping Rich tables; per-model reference-image caps for `i2i` and `r2v` (with Veo 3.1 Quality correctly rejecting R2V); Google-account identity persisted to every profile on disk with auto-rename of the first-run `default` profile to the email local-part (issue #92); external cloud storage to S3 / MinIO / GCS via `GFLOW_CLI_STORAGE_URI`; a `gflow data prune` maintenance command and aggregated asset listing; and a structlog→stderr fix that keeps stdout pure JSON. Only video `batch` (manifest runner) is still queued for Phase B — use a shell for-loop until then ([USAGE](USAGE.md#gflow-video-batch)). Three earlier HTTP transport strategies live under `src/gflow_cli/api/transports/experimental/` (`evaluate_fetch` / `bearer` / `sapisidhash`); the production path is `ui_automation`.

**Develop (unreleased, post-v0.11.0):** *(empty — develop is the staging branch for the next release).*

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
| `gflow video t2v` model picker (5 Veo models) + `--duration` / `--count` | ✅ done (v0.9.1) |
| `gflow video i2v` (start + optional end frame) on `ui_automation` | ✅ done (v0.9.1) |
| `gflow video r2v` (reference-to-video, model-aware ref cap omni≤7 / veo≤3) | ✅ done (v0.9.1) |
| `gflow image t2i/i2i --model` actually selects the model (was a no-op) | ✅ done (v0.9.0) |
| Local SQLite catalog (data layer) recording every project / image / video / operation | ✅ done (v0.9.0) |
| `gflow data list {projects,images,videos,profiles}` read CLI over the catalog | ✅ done (v0.9.0) |
| `ROADMAP.md` published (themed milestones through v1.0) | ✅ done (v0.9.0) |
| Locale-agnostic media-dialog upload selectors (fixes non-English Chrome profiles) | ✅ done (v0.9.0) |
| Wheel-build fix (removed redundant `force-include` causing duplicate ZIP entries) | ✅ done (v0.9.0 hotfix, PR #74) |
| `--json` machine-readable output across `image t2i/i2i`, `video t2v/i2v/r2v`, `auth list` + `gflow models` catalog | ✅ done (v0.10.0) |
| Per-model reference-image caps for `i2i` / `r2v` (Veo 3.1 Quality rejects R2V) | ✅ done (v0.10.0) |
| Google-account identity persisted per profile + auto-rename of first-run `default` (issue #92) | ✅ done (v0.10.0) |
| External cloud storage (S3 / MinIO / GCS) via `GFLOW_CLI_STORAGE_URI` | ✅ done (v0.10.0) |
| `gflow data prune` + aggregated asset listing (`--all-copies`) + cross-profile count fixes (#111, #113) | ✅ done (v0.10.0) |
| Layered cost-stratified e2e test strategy (`e2e_auth`/`e2e_image`/`e2e_video`/`e2e_batch`/`e2e_data`/`smoke`) | ✅ done (v0.10.0) |
| `gflow video i2v` routes to the Veo i2v endpoint (no silent T2V fallback) + `veo-lite` default (issue #125) | ✅ done (v0.11.0) |
| Create-project generation works under Flow's "Agent" composer mode | ✅ done (v0.11.0) |
| Image-model selection hardened for non-English Flow UIs (selector cascade, #94) | ✅ done (v0.11.0) |
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
