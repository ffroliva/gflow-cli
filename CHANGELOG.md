# Changelog

All notable changes to `gflow-cli` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- `NEW_PROJECT_SELECTORS` now covers all 14 supported locales (EN / PT / ES /
  FR / DE / IT / NL / JA / ZH / KO / PL / RU / TR / ID) and leads with
  locale-stable icon selectors (`add_2` Material Symbols ligature on
  `<button>` and on `[role='button']` ARIA-role variants, plus an anchored
  `^\+\s+\S+$` regex for `+ <word>` host elements). English-only
  `[aria-label*='New project']` and `[aria-label*='Project']` ARIA fallbacks
  removed.
  `SUBMIT_BUTTON_SELECTORS` drops its English-only
  `button[aria-label*="Create"]` fallback — the preceding `arrow_forward` icon
  entries already cover this button in every locale. Both selector tuples are
  now fully locale-invariant for non-English Chrome profiles. The `--lang=en-US`
  Chromium launch arg is retained only to stabilise `IMAGE_MODEL_OPTION_SELECTORS`
  (English product names); its removal is tracked as issue #24 Phase 5 (#94).

### Fixed

- Running the pytest suite no longer writes fixture rows into the developer's
  production `gflow.db` catalog. A new autouse `_isolate_settings` fixture in
  `tests/conftest.py` redirects `GFLOW_CLI_HOME` and `GFLOW_CLI_DB_PATH` to
  per-test `tmp_path` dirs and clears the `get_settings()` `lru_cache` before
  and after every test, preventing the cached singleton from ever resolving to
  a `platformdirs` production path. Closes
  [#86](https://github.com/ffroliva/gflow-cli/issues/86).

- `gflow video i2v` no longer silently breaks on non-English Chrome profiles.
  PR #70's structural-first `_attach_frame` cascade matched **zero** real
  slots — its anchor selector assumed the `swap_horiz` icon used class
  `google-symbols` (it uses `material-icons`) and the slots were `<button>`
  (they're `<div type="button">`). Production I2V therefore relied on the
  English-text fallback, which silently misses on any non-EN profile (pt-BR
  shows `Inicial`/`Final`, DE shows `Anfang`/`Ende`, etc.). Replaced
  `FRAME_SLOTS_STRUCT` with the locale-free pattern
  `div[type='button'][aria-haspopup='dialog']` and added a `.first`-of-remaining
  fallback for the End-frame case (after Start is attached, only one slot
  matches and the prior `.nth(slot_index)` went out-of-bounds). Live-verified
  with `tests/e2e/test_transports_e2e.py::test_e2e_i2v_start_end_frame_attach`
  on `ffroliva` + `GFLOW_CLI_LOCALE=de-DE` (Chrome rendered pt-BR; both
  non-EN). Closes [#63](https://github.com/ffroliva/gflow-cli/issues/63).

### Changed

- `gflow data media <id>` now searches across **all** profiles by default,
  matching the cross-profile default of `gflow data list`. Pass
  `--profile NAME` to disambiguate the rare case where the same Flow
  media ID exists under multiple profiles (the command refuses to
  guess and prints the list of candidate profiles, each annotated with
  its `kind`). Closes
  [#87](https://github.com/ffroliva/gflow-cli/issues/87).

### Fixed

- `gflow data list` no longer crashes with `no such table: assets` on a
  missing or freshly-created catalog DB. The query path now routes through
  `DataStore.open`, which applies schema migrations on first connect —
  first-time users and anyone recovering from a wiped DB get an empty
  table and exit 0 instead of a `DataStoreError`. Closes
  [#88](https://github.com/ffroliva/gflow-cli/issues/88).
- `gflow auth list` no longer crashes with `UnicodeEncodeError` on Windows
  consoles whose code page cannot encode the default-profile marker `●`
  (cp1252 in PowerShell / cmd by default). The renderer now picks a glyph
  safe for the active `sys.stdout.encoding` — `●` on UTF-8, ASCII `*` on
  cp1252 / ascii / latin-1 / unknown. Closes [#82](https://github.com/ffroliva/gflow-cli/issues/82).

### Documentation

- `PLAN.md` refreshed to reflect develop state through v0.9.0 — marks Phase 6
  (data layer) shipped via PR #58 + #78 + #81, Phase 7 Issue #24 Phase 2
  shipped via PR #70, Phase B I2V/R2V shipped via PR #48, and resolves the
  duplicate Phase 7 numbering (pluggable storage renumbered to Phase 8).

## [0.9.0] — 2026-05-25

> **Maturity & Visibility release.** Surfaces the SQLite catalog (PR #52/#58)
> via a read-only `gflow data list {projects,images,videos,profiles}` CLI,
> publishes `ROADMAP.md`, and ships the locale-agnostic media-dialog
> selectors that unblock non-English Chrome profiles. Plus the previously-
> unreleased video model picker, i2v/r2v, and the I2I ref-attach + model-
> select fixes. Sponsorship wiring will land in a follow-up patch release
> once GitHub Sponsors / Buy Me a Coffee accounts are fully provisioned.

### Added

- `gflow data list {projects,images,videos,profiles}` — read-only catalog
  query CLI over the local SQLite data layer. Flags: `--limit` (1..1000,
  default 20), `--offset` (≥0, default 0), `--profile NAME`, `--json`.
  Rich table on TTY, JSONL on pipe or `--json`. Default sort: newest first.
  `DataStoreError` family maps to exit code 16. See
  [`docs/DATA_LAYER.md § Querying the data layer`](docs/DATA_LAYER.md#querying-the-data-layer).
- `ROADMAP.md` at repo root — themed milestones from v0.9 through v1.0 (no
  dates).
- `gflow video t2v` model picker: `--model` (`omni-flash` | `veo-lite` |
  `veo-fast` | `veo-quality` | `veo-lite-lp`), `--duration` (`4`/`6`/`8`, plus
  `10` for `omni-flash` only), and `--count` (1–4). Driven via the editor's
  generation-settings panel; live-verified against a Pro/Ultra profile.
- `gflow video i2v <image> "<prompt>"` — image-to-video with a start frame and
  an optional `--end-image` (interpolation). Fires
  `batchAsyncGenerateVideoStartImage` / `…StartAndEndImage`.
- `gflow video r2v "<prompt>" --ref <img> [--ref …]` — reference-to-video
  (Flow "ingredients"). Model-aware reference cap (omni_flash ≤7, veo_3_1_* ≤3)
  enforced in the request DTO; the transport stops gracefully if Flow hides the
  add-media button at the cap. Fires `batchAsyncGenerateVideoReferenceImages`.
- `GFLOW_CLI_LOCALE` env var — overrides Playwright's launch `locale=` parameter
  (default: `en-US`). Controls `Accept-Language` only; Chrome's UI language is
  still forced to en-US via `--lang=en-US`. Prep for issue #24 (locale-agnostic
  selectors); live-verified end-to-end with `GFLOW_CLI_LOCALE=pt-BR` against a
  Pro/Ultra account. See `docs/CONFIGURATION.md § GFLOW_CLI_LOCALE`.
- **Local data layer** — `gflow-cli` now keeps a SQLite catalog of every new
  image, batch, and video operation under `$GFLOW_CLI_DB_PATH` (default:
  `~/.local/share/gflow-cli/data.db`). Records profile, project, asset
  (model / aspect / dimensions / Flow media ID), operation provenance
  (mode / prompt / model / timing / error), input↔output links, and
  downloaded local files. New `gflow data media <id>` command resolves a
  Flow media ID to its origin. `DataRepository` exposes seed-image resolvers
  (`resolve_seed_image_by_path` / `resolve_seed_image` /
  `resolve_latest_image`) — foundation for the upcoming I2V seed-reuse
  path. Pre-Flow store failures exit `16` (`DataStoreError` /
  `DataMigrationError` / `DataIntegrityError`); post-success store
  failures warn and exit `0` (Flow already charged the credits). See
  [`docs/DATA_LAYER.md`](docs/DATA_LAYER.md). (PR #58, stacked on #52.)

### Changed

- `MAX_REFERENCE_IMAGES` (in `api/video.py`) now tracks the `omni_flash`
  ceiling of **7** (was **3**). The tighter per-model cap (`veo_3_1_* ≤ 3`) is
  still enforced in `GenerateVideoRequest.__post_init__` when the model is
  known; the constant is only the absolute upper bound. Anyone pinning to the
  old value of 3 should re-check against the per-model caps.

### Fixed

- `FlowApiClient.__aenter__` now tears down a partially-launched browser if any
  step after the Playwright driver starts raises (e.g. the persistent-context
  launch, the bootstrap navigation, or `transport.setup`). Python does not call
  `__aexit__` when `__aenter__` raises, so an unguarded failure orphaned the
  chrome process, which then held the profile's user-data-dir lock — the next
  run could not acquire it and spiralled into rapid `about:blank` tabs +
  `TargetClosedError`. Context close + driver stop are now shared by
  `__aenter__`'s guard and `__aexit__` via `_close_browser_resources`.
- `gflow image i2i --ref <local-file>` now binds the reference through the
  editor's media dialog instead of the REST `uploadImage` endpoint (which 401s —
  same root as #15/#39). Local-path refs ride a new `GenerateImageRequest.ref_paths`
  field and are attached via the inherited R2V `_attach_references` (the image-mode
  add-media dialog is the same `add_2` surface). Bare-UUID `--ref` still flows
  through `refs` unchanged. Re-introduces #50 (reverted in #57 for the account/
  locale variant tracked in #56); the media-dialog selectors are now
  locale-agnostic (see the next entry).
- The media-dialog upload selectors are now **locale-agnostic** (issue #56/#24).
  `UPLOAD_MEDIA_BUTTON` matched localized text (`has-text('Upload media')`), so on
  a non-English Chrome profile (Flow follows the *Chrome profile* language, which
  the `--lang=en-US` arg cannot override) the click missed and the file chooser
  never opened — a silent ~34s hang. It now anchors on the locale-free `upload`
  icon ligature (`:text-is('upload')`, exact, so it doesn't grab the `Uploads`
  tab), with the original English-text selector kept as a graceful **fallback
  tier** (matches if Google ever changes the icon but keeps the English label);
  'Add to Prompt' (which has no icon) is selected structurally as the only
  iconless button in the open dialog. If neither tier opens a chooser,
  `_upload_via_open_dialog` raises a clear error + writes a screenshot (no silent
  hang) and points the operator at the Chrome-profile-language workaround. Fixes
  I2I/I2V/R2V upload alike.
- `gflow image t2i/i2i --model` now actually selects the requested model. It was
  a no-op under `ui_automation` (the wire field was set but the model picker was
  never clicked, so Flow used its UI default). Adds `_select_image_model`.
- Video selector mismatches: the output-count selector `[id*=-trigger-1]`
  collided with the `-trigger-10` duration tab; the aspect selector matched a
  non-existent `aria-controls*=9_16`; the video-mode tab match was ambiguous.
  All now use exact `[id$=-trigger-X]` suffixes + aria-label text.

### Build

- **Wheel build no longer emits duplicate ZIP entries.** An earlier attempt at
  tagging v0.9.0 was rejected by PyPI with HTTP 400 ("Duplicate filename in
  local headers") because `pyproject.toml` had
  `[tool.hatch.build.targets.wheel.force-include]` and
  `[tool.hatch.build.targets.sdist.force-include]` blocks pointing at
  `src/gflow_cli/data/migrations`, on top of the already-comprehensive
  `packages = ["src/gflow_cli"]` directive — hatchling included the
  migrations directory twice (both `__init__.py` and `0001_initial.sql`). The
  force-include blocks have been removed; hatchling's default package
  inclusion already covers `.sql` files inside the package tree. (PR #74.)

### Notes

- I2V/R2V image inputs bind through the editor's media dialog (frame slot /
  add-media → "Upload media" → file chooser → "Add to Prompt"). `set_input_files`
  on the generic hidden input only adds to the library and Flow then ignores the
  image (plain Text route). The editor is forced to English via the
  `--lang=en-US` Chromium launch arg because the slot/dialog labels are localized
  with no locale-free anchor.

## [0.8.1] — 2026-05-23

### Documentation

- README rewritten as a hybrid router (~150 lines, was 398). New: prominent unofficial-tool + headed-browser callouts above the fold, polished 60-second quick start, in-depth-quick-start link, "For AI agents & LLMs" routing table, ripgrep-style documentation TOC.
- New [AGENTS.md](AGENTS.md) at repo root — universal agent spec consumed by 60k+ repos' tooling (Cursor, Codex, Aider, Gemini CLI, Claude Code, Copilot, opencode, etc.). Closes the gap left by having Claude-Code-only memory.
- New [llms.txt](llms.txt) at repo root — llmstxt.org-format summary for end-users feeding the project into an LLM. Forward-staged for a future docs site.
- New [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) — moved the full milestone table out of README; added lifecycle policy section.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) absorbed the ASCII system diagram + Auth strategy paragraphs from README; gained a new "Headed-browser dependency — current limitation" subsection that documents the three retired HTTP transports and invites contributor work on a pure-REST path.
- [CLAUDE.md](CLAUDE.md) trimmed from ~80 to ~25 lines; universal coding-agent rules now live in AGENTS.md, with CLAUDE.md carrying Claude-Code-specific session protocol only.
- All `v0.7.0` references that meant "current" updated to `v0.8.1`. Historical mentions preserved in CHANGELOG and `docs/LIVE_VERIFICATION_v0.7.0.md`.
- New per-release evidence file [docs/LIVE_VERIFICATION_v0.8.1.md](docs/LIVE_VERIFICATION_v0.8.1.md).

### Released

- PyPI: https://pypi.org/project/gflow-cli/0.8.1/ — refreshes the stale README rendering left over from v0.8.0.

## [0.8.0] — 2026-05-23

> **Multi-image-prompt release + transport hardening.** Introduces the
> `gflow image batch` subcommand backed by a stay-mounted editor session,
> restores `gflow video t2v` with first-class auto-download, and ships the
> image/video mode-switch symmetry invariant that closes the historical
> "first-attempt listener-miss flake." Also clears all SonarCloud findings
> on the multi-image-prompt PR (cognitive-complexity refactors of
> `_set_count`, `parse_tsv_manifest`, and `_generate_images_batch_locked`).

### Added

- `gflow image batch <manifest>` subcommand for batch image generation from
  JSON or TSV manifests, with `--continue-on-error`. `MAX_BATCH_PROMPTS = 5`.
  All prompts share one Flow project; jitter (3–7 s default) spaces the
  submission clicks for anti-bot cadence, not completion wait. Closes
  [#14](https://github.com/ffroliva/gflow-cli/issues/14) part 2.
- Application-layer structlog events for image-batch submission:
  `image_batch.submission_attempt`, `image_batch.submission_result`,
  `image_batch.row_completed`, `image_batch.inter_submission_latency_ms`.
  Use these to debug Flow throttling regressions without re-instrumenting.
- `BatchPartialError` (in `errors`) — raised by fail-fast batch when
  earlier prompts produced downloadable images before the failing one;
  carries `partial_results` so the orchestrator can salvage them.
- `BatchIntegrityError` (in `errors`) — raised by the orchestrator when
  post-download file count does not match the expected count.
- `BatchSubmissionResult` (in `api.dto`) — new transport-layer per-prompt
  outcome with `project_id`, `prompt_idx`, `prompt_hash` fields. Public
  `list[BatchOutcome]` orchestrator return is unchanged.
- `ui_automation.image_mode_entered` structlog event — emitted when the
  editor is switched into Image mode. Companion to the existing
  `ui_automation_video.video_mode_entered`.
- `ui_automation.orphaned_project_warning` structlog event — emitted when
  `_enter_editor` succeeded but a later setup step
  (`_dismiss_blocking_overlays` / `_switch_to_image_mode`) raises, so the
  user can find their server-side project record.
- `ui_automation.batch_403_body` structlog event — emitted (warning level)
  with a 200-char body prefix when a `batchGenerateImages` response is HTTP
  403 (WAF / reCAPTCHA), immediately before the `WafRejectionError` raise.
- `VideoResult` dataclass — return type of `generate_video`, carries
  `status` and `local_path` ([#29](https://github.com/ffroliva/gflow-cli/issues/29)).
- `UiAutomationTransport._download_video` — downloads a generated mp4 via
  `media.getMediaUrlRedirect` using the authenticated page; falls back to
  `self._out_dir` then `tmp/` when no `out_dir` is supplied
  ([#29](https://github.com/ffroliva/gflow-cli/issues/29)).
- `FlowApiClient.download_video(media_id, out_path)` — public API, mirrors
  `download_image` ([#29](https://github.com/ffroliva/gflow-cli/issues/29)).
- `gflow video t2v PROMPT` restored — generates and downloads a video
  end-to-end on `UiAutomationTransport`; supports `--aspect`
  (`9:16` / `16:9`), `--profile`, and `--out-dir`
  ([#29](https://github.com/ffroliva/gflow-cli/issues/29)).
- `UiAutomationTransport._switch_to_image_mode` static method + module-level
  `IMAGE_TAB_IN_MENU_SELECTORS` cascade — mirror of the video side's
  `_switch_to_video_mode`. Called from both `generate_images` and
  `_generate_images_batch_locked` after `_dismiss_blocking_overlays`,
  before `_configure_generation_settings`.

### Changed

- `gflow image batch` editor session is now persistent across all prompts
  in a batch. The transport's stay-mounted-session pattern is the
  canonical shape; same-project semantics are the only supported mode.
- `_attach_batch_response_listener` now returns `(captured, detach_fn)`;
  callers that used the single-list return need to unpack accordingly.
- `UiAutomationTransport.generate_video` now accepts `download: bool = True`
  and returns `VideoResult` instead of `VideoStatus` — **breaking change
  for direct transport callers** (the `FlowApiClient` boundary is
  unaffected). Pass `download=False` to skip the auto-download step.
- `_set_count` (count-tab selector) is locale-invariant: regex
  `^(1x|x[2-4])$` (Flow renders digit+x identically in every locale) +
  positional `.nth(count - 1)` fallback when read-back text is
  unrecognised. Partial fix for
  [#24](https://github.com/ffroliva/gflow-cli/issues/24); `ONBOARDING_SELECTORS`
  still localized text — see KNOWN_ISSUES.

### Fixed

- `gflow image t2i` and `gflow image batch` now explicitly select Image
  mode in the Flow editor before submitting. Previously, if the account
  was last in Video mode, prompts were silently routed to the video
  endpoint — no `batchGenerateImages` response was observed and the
  listener timed out after 3 minutes. Also resolves the historical
  "first-attempt listener-miss flake" recorded in `phase-b-followups`
  memory item #1. Live-verified on profile `ffroliva` (1 t2i shot + full
  batch e2e); evidence in
  [`docs/LIVE_VERIFICATION_image_batch.md`](docs/LIVE_VERIFICATION_image_batch.md)
  § Post-mode-switch-fix verification.
- `gflow image batch` now actually shares one Flow project across all
  prompts in a batch. Previously the `--same-project=1` flag was a no-op
  at the `ui_automation` transport layer; each prompt landed in its own
  Flow project. ([spec](docs/superpowers/specs/2026-05-22-stay-mounted-batch-session-design.md))
- `gflow image t2i -n N` now makes one transport call using Flow's native
  xN count selector instead of fanning out N parallel single-image
  submissions. Closes [#14](https://github.com/ffroliva/gflow-cli/issues/14) part 1.
- Structlog now uses `cache_logger_on_first_use=False` so per-test
  `LogCapture` fixtures see events fired from production modules
  (previously the cached logger froze the processor chain at import).
- All SonarCloud findings on the multi-image-prompt PR (S5655 / S5890 /
  S1192 / S1172 / S3776 ×3) — see PR #40 commit `a0cb010` for the
  cognitive-complexity refactors and the cast+pragma pattern for
  `dataclasses.replace`.

### Removed

- **BREAKING:** `--same-project` flag on `gflow image batch`. The flag
  collapsed to a single behaviour (always-same-project) — no toggle
  remains. For different-project results, loop `gflow image t2i`
  externally.
- **BREAKING:** `--seed` flag from `gflow image t2i` and `gflow image i2i`.
  The flag was a no-op under the active UI transport since v0.7.0
  (silently discarded inside the client before reaching the transport).
  If reproducibility via user-controlled seed becomes possible again —
  either through Flow UI exposing a seed control or via HTTP transport
  revival — the surface will be re-introduced at that layer. The
  wire-format body builder retains its `seed` / `batch_id` parameters for
  the experimental HTTP transports' internal use.
- **BREAKING (library):** `FlowApiClient.generate_image` no longer accepts
  `seed=` or `batch_id=` kwargs. `FlowApiClient.generate_images_batch` no
  longer accepts `seeds=`. Callers passing these will get a `TypeError`.
  Same justification as the CLI removal.
- **BREAKING (library):** `project_title` parameter removed from
  `run_manifest_image_batch` — the transport now owns project creation
  via `_enter_editor`, making this orchestrator-side knob dead weight.

## [0.7.0] — 2026-05-20

> **Downstream-worker ergonomics release.** Hardens `FlowApiClient` for
> long-lived integrations: standard exception module name, optional
> `project_id`, `health_check()` for liveness probes, `out_dir` for
> debug-screenshot plumbing, and a stable library-owned error when the
> underlying browser session dies. Plus auth-flow fixes from issues #15 and
> #17 and overlay-dismiss for first-run profiles (#26).

### Added

- `gflow_cli.exceptions` module as a standard alias for `gflow_cli.errors` — both module names resolve identically. Closes [#16](https://github.com/ffroliva/gflow-cli/issues/16).
- `FlowApiClient.health_check()` async method — returns `True` if browser context is alive and on a Google domain; safe to call from long-lived workers without try/except. Closes [#16](https://github.com/ffroliva/gflow-cli/issues/16).
- `FlowApiClient(out_dir=...)` constructor argument — when set, the resolved transport stores it as `_out_dir` so internal `_capture_debug_screenshot` calls inside `UiAutomationTransport._generate_images_locked` (entering the editor, dismissing overlays, sending prompts) save artifacts to that directory. Long-lived workers can now diagnose selector failures without restructuring their call sites. Closes [#18](https://github.com/ffroliva/gflow-cli/issues/18).
- `BrowserSessionClosedError` (`gflow_cli.errors`, exit code 15) — raised from `FlowApiClient.generate_image()` / `generate_images_batch()` when the underlying Playwright page/context is closed mid-call (Playwright `TargetClosedError`). Callers can now catch a stable library-owned class and recreate the client via `async with FlowApiClient(...)` instead of importing from `playwright._impl._errors`. Closes [#18](https://github.com/ffroliva/gflow-cli/issues/18).
- `UiAutomationTransport._dismiss_blocking_overlays(page)` — generic overlay-dismiss helper that detects Flow changelog ("What's new") iframes and dismisses them via a close-button selector cascade with an Escape-key fallback. Invoked after editor entry on both image and video flows so first-run profiles no longer fail on the next click. Closes [#26](https://github.com/ffroliva/gflow-cli/issues/26).
- Release tags must now be **signed annotated tags** (`git tag -s vX.Y.Z`). CI's release job rejects unsigned tags so the GitHub release surfaces as Verified ([#30](https://github.com/ffroliva/gflow-cli/issues/30)).
- New documentation: [`docs/DEBUGGING.md`](docs/DEBUGGING.md) — evergreen reference for debugging, testing, and troubleshooting (listener log keys, selector-cascade discipline, lifecycle errors, Windows console encoding, test-suite memory). [`docs/LIVE_VERIFICATION_v0.7.0.md`](docs/LIVE_VERIFICATION_v0.7.0.md) — per-release end-to-end evidence (every CLI aspect ratio live-tested).

### Changed

- `FlowApiClient.generate_image()` and `generate_images_batch()`: `project_id` is now optional (`str | None = None`). When omitted, a new Flow project is created automatically. Existing callers passing an explicit `project_id` are unaffected. Closes [#16](https://github.com/ffroliva/gflow-cli/issues/16).
- `gflow video t2v/i2v/batch` now report "temporarily unavailable" — video generation is being rebuilt on the UI-automation transport (Phase A ships the T2V transport; CLI commands return in Phase B).

### Removed

- The 401-dead HTTP video API path (`FlowApiClient.generate_video`, `get_video_status`) — retired in favour of the new UI-automation transport (`VideoGenerationMixin` in `api/transports/ui_automation_video.py`).

### Fixed

- `gflow auth login` now verifies a real Flow app session before reporting
  success — fixes issue [#15](https://github.com/ffroliva/gflow-cli/issues/15), where a Google-only sign-in was wrongly accepted
  and later failed with HTTP 401.
- **`gflow auth login --browser internal` now fails fast when Google rejects
  Playwright's bundled Chromium**, returning `AuthBrowserRejectedError` exit
  code 14 with guidance to rerun using real Chrome
  (`gflow auth login --browser chrome`) or set
  `GFLOW_CLI_AUTH_BROWSER=chrome` ([#17](https://github.com/ffroliva/gflow-cli/issues/17)).
- **`gflow image t2i --aspect 1:1` aspect-ratio tab regression** — Flow's
  `1:1` tab is now selected via an exact-match (`:text-is`) cascade against
  the labels `1:1`, `Square`, `1×1`, `1x1` instead of the prior
  `:has-text("1:1")` substring match. The substring selector was matching an
  invisible parent on some Flow UI variants, causing a 3 s timeout and a
  silent fallback to Flow's default aspect. All five CLI aspect ratios
  (`16:9`, `9:16`, `1:1`, `4:3`, `3:4`) are now live-verified.
- `UiAutomationTransport._attach_batch_response_listener` now emits a
  `ui_automation.batch_response_seen` log for every `batchGenerateImages`
  URL observed (BEFORE the per-project filter) and a
  `ui_automation.batch_response_dropped_project_id_mismatch` log when the
  filter rejects a response. Eliminates the silent black-hole that hid
  listener-miss bugs during live verification.

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

[Unreleased]: https://github.com/ffroliva/gflow-cli/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/ffroliva/gflow-cli/compare/v0.8.1...v0.9.0
[0.8.1]: https://github.com/ffroliva/gflow-cli/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/ffroliva/gflow-cli/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/ffroliva/gflow-cli/compare/v0.6.0a6...v0.7.0
[0.6.0a6]: https://github.com/ffroliva/gflow-cli/compare/v0.6.0a5...v0.6.0a6
[0.6.0a5]: https://github.com/ffroliva/gflow-cli/compare/v0.6.0a4...v0.6.0a5
[0.6.0a1]: https://github.com/ffroliva/gflow-cli/compare/v0.5.0a1...v0.6.0a1
[0.5.0a1]: https://github.com/ffroliva/gflow-cli/compare/v0.4.0a2...v0.5.0a1
[0.3.0a1]: https://github.com/ffroliva/gflow-cli/releases/tag/v0.3.0a1
[0.2.0a1]: https://github.com/ffroliva/gflow-cli/releases/tag/v0.2.0a1
[0.1.0]: https://github.com/ffroliva/gflow-cli/releases/tag/v0.1.0
