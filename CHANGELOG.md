# Changelog

All notable changes to `gflow-cli` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.50.0] — 2026-08-04

### Added

- **Adopted MCP 2026-07-28 Tasks extension (SEP-2663) (#409).** Added `TasksExtension` subclass serving `tasks/get` and `tasks/cancel`. Generation tools (`gflow_generate_image`, `gflow_generate_video`) support non-blocking task handle responses (`wait=False`).
- **Hardened CLI and MCP `-o`/`--output` path routing (#414, #415).** Custom output paths land generated assets at target locations with automatic parent directory creation, S3 cloud storage relative path preservation, and multi-count stem suffixes (`_1`, `_2`).

### Fixed

- **PR triage notification resilience (#428).** Fallback alerts via Telegram on missing credentials or auth failure.

### Changed

- **`gflow video i2v` accepts `--model omni-flash` again — start frame only,
  and it unlocks `--duration 10` (#125).** The 2026-05-30 wire capture that
  justified the blanket exclusion (Flow silently dropping frame refs and
  billing the run as text-to-video) no longer reproduces: a 2026-08-03
  route-aborted re-capture shows Flow routing omni + start frame to
  `batchAsyncGenerateVideoStartImage` with the frame bound, and a live x1 10s
  generation confirmed the output starts on the supplied frame. The END frame
  stays gated for omni-flash (`--end-frame` exits 17 pre-spend — Flow lists
  first+last as "coming soon"), and `chain` still rejects omni-flash
  (single-clip proof does not cover N seeded links). New credit-free recon
  spike: `scripts/dev/spike_omni_flash_i2v_ui_recon.py`.

### Fixed

- **An out-of-range Playwright silently wedged every video generation; the
  dependency is now upper-bounded and the stall fails fast.** `uv tool install
  <path>` ignores `uv.lock` and resolves from the `pyproject.toml` ranges, and
  the `playwright>=1.45.0` range had no upper bound — so a local/tool install
  could pick up a Playwright this project has never tested. Observed: an
  install that resolved **1.62.0** against a project locked to **1.59.0** made
  every `gflow video i2v` run hang **silently** right after the frame upload
  (last log line `ui_automation_video.frame_attached`, browser alive, no error,
  no timeout, indefinitely); reinstalling with the locked version fixed it on
  the first try. Playwright ships the browser driver, so an untested minor is
  an untested product — the constraint is now `>=1.59.0,<1.60.0` (patch
  headroom, no untested minors), pinned by `tests/test_playwright_pin.py`.
  Independently, the prompt-submission stage now runs under a named wall-clock
  watchdog: on expiry the run aborts **pre-submit** with
  `TransportTimeoutError`, a `stage_stalled` event and a debug screenshot taken
  under its own short deadline, and the error names the stage, prints the
  installed Playwright version against the supported range, and gives the
  pinned reinstall command. Nothing is submitted, so no credit is spent — a
  silent multi-minute hang is indistinguishable from slowness in an overnight
  batch, which is what made this expensive to diagnose.
- **A missing video output-count control now refuses before submit instead of
  silently proceeding on Flow's default of x2 — which double-billed
  `--count 1` runs (#404).** `_set_output_count` raises `UiSelectorDriftError`
  (exit 23, debug screenshot) on a probe miss, matching the duration probe's
  fail-fast contract (#288), and matches count-tab labels affix-agnostically
  per digit (`xN` current / `Nx` legacy) so the next label rename degrades to
  a fallback selector rather than an outage.

## [0.48.0] — 2026-08-02

### Added

- **Explicit `-o` / `--output` flag on the core generation commands (#411).**
  `gflow image t2i`, `gflow image i2i`, `gflow video t2v` and `gflow video i2v`
  now accept an explicit **local** destination file path, so scripts control
  exactly where the generated asset lands instead of parsing the
  date-partitioned default layout. Parent directories are auto-created, and
  `-o` takes precedence over `--out`/`--out-dir`. Multi-count **image** runs
  (`--count > 1`) append a deterministic `_1`, `_2`, … suffix before the
  extension; video runs write a single file. On `t2i` the flag is
  single-prompt only — multi-prompt runs abort with a usage error pointing at
  `--out`. Known limits, tracked as follow-ups: no `s3://`/`gs://` targets and
  no `GFLOW_CLI_STORAGE_URI` interplay yet, no `-o` on `video r2v`/`chain`, no
  video multi-count suffixes (#415); the MCP generate tools do **not** carry a
  matching `output` parameter — one was cut before release when the pre-release
  audit showed the worker queue never reads it, making it a silent no-op
  (#414). Raised by a Reddit user testing scriptability (thanks u/_suren).

## [0.47.0] — 2026-08-01

### Fixed

- **Entity attachments left no trace in the catalog, making character provenance
  unrecoverable (#402).** `--reference-entity` / `--reference-entity-name` reached
  the transport but were never recorded, so no `image t2i`, `image i2i` or movie
  R2V operation carried the entity it was generated from — while `--ref` media
  refs were recorded in full, with ordering. When character identity drift showed
  up, the catalog could not answer "which entity produced this image?".
  Generation operations now persist `entity_ids` and `entity_names` in
  `operations.metadata_json`, in attach order, on succeeded and failed rows alike
  — a FAILED row carrying `entity_ids` distinguishes a rejected attach from a run
  that never requested an entity. Tool and entity provenance are composed into a
  single `set_operation_metadata` write, since that call replaces the whole
  column. Forward-only: operations recorded before this release cannot be
  Live gate: `tests/e2e/test_entity_provenance_e2e.py` (opt-in via
  `GFLOW_CLI_E2E_RUN_ENTITY_PROV=1`) verifies the recorded entity matches a
  generation Flow actually accepted, including the rejected-attach case.
- **The MCP server was broken on every fresh install.** `pyproject.toml`
  declared an unbounded `mcp>=1.0.0`. `uv.lock` pinned `1.28.1`, so CI and
  contributors stayed green — but `pip install gflow-cli` / `uv tool install
  gflow-cli` resolved the newly published `mcp` 2.0.0, which **deleted the
  `mcp.server.fastmcp` module** that `gflow_cli.mcp.server` imports at module
  load. `gflow mcp run`, `gflow serve`, and `gflow mcp setup` all failed with
  `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. The dependency is
  now bounded (`mcp>=2.0.0,<3`) and the server is migrated to the 2.x API.
  Pure-CLI paths were never affected.
- **New `resolve-drift` CI job — the actual defect.** Every other job installs
  via `uv sync`, which honours `uv.lock`, so CI never exercised the version
  *ranges* in `pyproject.toml` and could not have caught the above. This job
  installs the way a user does — from the declared ranges, newest compatible
  versions, no lockfile — then smoke-imports the MCP surface and the CLI entry
  points. It generalizes to every unbounded dependency, not just `mcp`.
- **`ui/app.py` advertised an unroutable MCP message endpoint.** mcp 2.0 dropped
  FastMCP's `sse_app(mount_path=...)` shim, which used to let the advertised
  endpoint (`/mcp/messages/`) differ from the sub-app's internal route
  (`/messages/`). Both now derive from one `message_path`, so the advertised
  path was left unrouted at the app root — a silent failure where the SSE stream
  opens and only the follow-up POSTs 404. Fixed with an explicit alias and
  pinned by a regression test.

## [0.46.1] — 2026-07-31

### Fixed

- **`image t2i` failed whenever `-n` differed from the displayed count — including
  the default `-n 1` (#404).** Flow renamed the composer's count-tab labels from
  `1x`/`x2`/… to `x1`/`x2`/…, so the count setter's label filter silently dropped
  the count-1 tab and its positional pick clicked the wrong tab (reporting
  per-click success while the value never changed). The count tab is now selected
  by the digit in its label across both label cohorts (`1x`/`x1`), the read-back recognises a
  selected `x1`, and non-convergence raises `UiSelectorDriftError` (exit 23)
  naming desired vs displayed — instead of a bare `RuntimeError` that
  observability hashed into an opaque `UnexpectedError`. The per-click
  `count_click_result` log now carries `effect_observed`, and the video output
  count setter probes both label cohorts too. Verified live on the classic
  composer (new e2e: `tests/e2e/test_classic_count_setter_e2e.py`; evidence:
  [LIVE_VERIFICATION_v0.46.1](docs/LIVE_VERIFICATION_v0.46.1.md)).

## [0.46.0] — 2026-07-28

### Changed

- **BREAKING — the prompt tools now drive any OpenAI-compatible endpoint (#387).**
  `--tool creative-director` / `reverse-engineer` / `storyboard` were hardwired to
  Google's native Gemini API, so an OpenAI-compatible gateway key could not be
  used at all. The transport now speaks **OpenAI Chat Completions**, which
  OpenAI, gateways/proxies (OpenRouter, LiteLLM, freellmapi), local runtimes
  (Ollama, LM Studio) and Google's own compatibility endpoint all accept.
  - New config: `GFLOW_CLI_LLM_BASE_URL` (the on/off switch; defaults to
    Google's compat endpoint), `GFLOW_CLI_LLM_API_KEY` (**optional** — omitted
    from the request entirely when unset, so keyless local gateways work), and
    `GFLOW_CLI_LLM_MODEL` (also the provider selector, since gateways route on
    the model string).
  - Provider keys stay with your gateway. gflow only ever holds the single
    credential it presents to the endpoint you point it at.
  - The three builtin tools no longer pin `model = "gemini-2.5-flash"`. That pin
    was sent unconditionally, so any gateway not serving that exact name
    answered 400 — silently, because the prompt tools never fail a run.

### Removed

- **BREAKING — `GFLOW_CLI_GEMINI_API_KEY` and `GFLOW_CLI_GEMINI_MODEL` (#387).**
  Set `GFLOW_CLI_LLM_API_KEY` instead; an existing Google `AIza…` key keeps
  working unchanged, because the default endpoint is Google's OpenAI-compatible
  surface. gflow prints a one-time warning if it sees the old variable — the old
  value is never forwarded. The warning exists because the prompt tools never
  fail a run: without it, prompts would quietly stop being rewritten while
  generations still billed in full.
  `GFLOW_CLI_GEMINI_MODEL` had in fact never had a live code path — its only
  reader was an uncalled helper — despite being documented as a global override.

### Fixed

- **`reverse-engineer` no longer expands a file path as if it were a prompt (#387).**
  When multimodal analysis of a URL, video, or image failed, the runtime fell
  through to text expansion of the *path string*, returning a confident but
  useless prompt that still billed a full generation. It now degrades to the
  original input.

### Security

- **The prompt-tools endpoint is treated as a trust boundary (#387).** `base_url`
  is user-supplied now, so: redirects are declined (`urllib` re-sends the
  `Authorization` header to whatever host a 302 names, which would have leaked
  the key to a hostile gateway); `base_url` is validated to `https`, or `http`
  for loopback only, with credentials-in-URL rejected; error-response bodies are
  redacted before logging, since a gateway can echo the bearer token back; and
  the resolved destination host is logged once, giving an audit trail for where
  prompts and base64 image bytes were sent.

## [0.45.0] — 2026-07-28

### Fixed

- **`gflow character create` binds portraits to the character again (#395).**
  Two independent defects made every character generation land as a plain
  project image, leaving the character empty:
  1. **Overlay dismissal Escaped Flow's own UI.** `[role='dialog']` and
     `[role='alert']` had been added to the overlay detector, but Flow's
     character composer (and the media picker) carry those roles — so gflow
     pressed Escape on the app itself, the generation went out without
     `entityContext`, and Flow filed the image against the project with no
     `parentEntityId`. Those two selectors are removed; `[role='banner']` and
     the announcement matcher remain, so the original banner-dismissal intent
     is intact.
  2. **The character route could bounce back to the project page.** Flow
     redirects `/project/{id}/character/{entityId}` when the entity is not yet
     queryable — a race gflow loses by navigating immediately after
     `flow.createEntity`. Because the project page also mounts a prompt box,
     the old readiness check was satisfied on the wrong surface and the prompt
     was typed into the **project** composer. gflow now verifies it actually
     came to rest on the character route, re-navigating a few times and failing
     loudly rather than generating on the wrong surface.

  Verified live 2026-07-28 in both directions on the same account, plus a
  read-back showing the character carrying its name and `thumbnail_media_id`.
  The wire contract is documented in
  [CHARACTER_RECON](docs/CHARACTER_RECON.md#entity-binding-entitycontext-captured-live-2026-07-28).

- **`gflow image i2i --ref <UUID>` no longer fails when the asset is out of the
  picker's reach (#393).** Flow's media picker is scoped to the active project
  and its search does **not** index media UUIDs (confirmed live 2026-07-27:
  both UUID search tiers returned zero tiles), so a `--ref` pointing at an asset
  in another project — or buried deep in a virtualised grid — could not be
  selected, and a bare `--ref <uuid>` carried no local file to fall back on. The
  CLI now resolves each UUID ref against the local catalog and attaches its
  recorded file, so the transport uploads those exact bytes instead of failing
  the run. Verified live as a controlled A/B on identical input: exit 9 before,
  exit 0 with the upload fallback after. Enrichment is best-effort (unknown
  asset, unavailable catalog, or deleted file leaves the ref untouched) and the
  **fail-loud contract is unchanged** — a reference that cannot be bound still
  aborts the generation rather than silently producing an unreferenced image.
- **A Flow web-app crash on the character-editor route is now reported as
  such.** `gflow character create` surfaced Flow's client-side crash as
  `RuntimeError: Character editor not ready: prompt textbox not visible`, which
  blames a selector that was never on the page. The existing crash classifier is
  now consulted at that gate too, yielding the typed, retryable `FlowAppError`
  (exit 31). Observed live 2026-07-27 via the incident bundle's `ui.json`
  (`title.category: flow_app_crash`).
- **Character binding failures say what actually happened.** When Flow returns a
  workflow that is not bound to the character entity, the error read "Unexpected
  response shape from Flow", sending readers after a wire-format bug. It now
  states that Flow filed the image as a plain project image and the character
  has no portrait. The guard remains strict — verified live that unbound runs
  leave the entity as "Untitled Character" with a null thumbnail.
- **Tier-aware credit confirmations:** `gflow video chain` and `gflow movie run`
  no longer present pending work as an exact one-credit-per-link/scene charge.
  Plans, confirmation prompts, `--dry-run` output, Click help, and current docs
  now report the count of *pending video operations* and direct operators to
  Google Flow for current model/duration/tier pricing, which varies by account
  tier and Flow policy. Execution, resume/stale accounting, `--yes`, JSON
  output, flags, and exit codes are unchanged.

### Added

- **Top Banner & Modal Dismissal (#369):** Expanded `_detect_overlay` and
  `_dismiss_blocking_overlays` in `ui_automation.py` to detect top banners,
  alert bars, and announcement dialogs (`[role='banner']`, `[role='alert']`,
  `[role='dialog']`, `div:has-text('What\'s new')`), force-clicking locale-stable
  close/clear/Got-it buttons and falling back to Escape before automation tasks.
- **`gflow character create --format-prompt` (#383):** Opt-in flag that clicks
  Flow's in-editor **Format** button before submitting, letting Flow rewrite the
  prompt into its character prompt-engineering shape. Applies to both the face and
  body slots. Best-effort by design — if the button is not found the prompt is
  submitted as typed rather than failing the generation. The selector cascade is
  anchored on the locale-stable `personal_recommendations` Material Symbols
  ligature (confirmed against live DOM 2026-07-27), not the "Format" label, which
  Flow localises to the Chrome profile language. Flow ships the button `disabled`
  on an empty prompt, so the driver checks enabled-ness — a disabled button is
  still *visible*, and clicking one would stall on Playwright's actionability wait.
- **Self-documenting error remediation & provider message pass-through (#380):** Added default `remediation_hint` strings across domain exception classes (`WireFormatError`, `ContentPolicyError`, `RateLimitError`, `DataStoreError`, `SceneConcatError`, `FrameExtractionError`), extracted and sanitized provider error messages from Google Flow tRPC/REST responses, and enriched MCP tool error envelopes and Rich CLI error displays with actionable recovery guidance at failure time.

## [0.44.0] — 2026-07-26

### Added

- **Dual-Side Project Naming & Management (`gflow project`)** (#381). Added
  `gflow project` subcommand group (`list`, `show`, `rename`, `create`),
  `--project-name` / `--project-title` options across `gflow image` and `gflow video`
  generation commands, prompt slugging for scratch projects, and dual-side title sync
  updating Google Flow's tRPC server/UI and local SQLite catalog in lockstep.
- **Namespaced Codex project skills.** The canonical workflows under `skills/`
  now ship as a repo-local, skills-only Codex plugin. Register and install it
  with `codex plugin marketplace add .` followed by
  `codex plugin add gflow@gflow-cli`, then invoke workflows such as
  `$gflow:status`, `$gflow:check`, and `$gflow:pr-council-review` in a new
  Codex CLI or desktop-app session. Claude Code's `/gflow:*` commands and the
  agent-agnostic skill files remain unchanged.
- **`gflow data errors` — bounded retention + export for failed-operation
  history** (#345). `gflow data errors export [--older-than AGE] [-o FILE]`
  dumps failed operations as JSONL (unbounded, newest-first) for offline
  archival — also closing the export path deferred from #341. `gflow data
  errors prune --older-than AGE [--dry-run]` deletes failures older than AGE
  (`90d` / `24h` / `30m`); `--older-than` is **required** and there is no
  automatic/background pruning — deletion is always an explicit operator
  action. Both honor `--profile` and the exit-16 `DataStoreError` convention.

### Fixed

- **`gflow character create` safely activates the current Create-Body
  composer.** Flow can autosave the body triptych over the stored portrait
  prompt when the mode switch has not settled (observed live 2026-07-25 on
  0.43.0). Current Flow reuses one Slate editor and exposes Create Body through
  the locale-independent `accessibility_new` icon beside the portrait image;
  gflow now clicks that scoped mode control, requires a new generated-face
  reference chip to mount, and verifies the shared editor owns focus **before
  any clearing or typing**. Readback failures abort before submit. The older
  `add_2`/second-box cohort remains supported through its count-rise gate.
  Post-type isolation still aborts before the credited submit. Live DOM
  evidence and the one-box failure were captured on 2026-07-26. Face-only
  creates are unchanged.

- **Profile-lock remediation message** now names the real cause and recovery.
  `ProfileLockedError` (exit 11) previously told operators to "wait for it to
  finish" — implying an unbounded wait — and to hunt for a `gflow`/Chrome
  process. In practice a blocking lock is a kernel *advisory* lock held by a
  **live** process (often a `python.exe` a `chrome.exe` scan misses); the OS
  releases it the instant the holder dies, so a leftover lock *file* never
  blocks acquisition. The message now points to the recorded owner PID
  (surfaced locally since v0.43.0) and says to just retry when nothing is
  running. The lock itself is unchanged — auto-reclaim was considered and
  rejected as unsafe (an unlink→new-inode race could put two browsers on one
  profile, the exact corruption the lease prevents). Refs #370.

## [0.43.0] — 2026-07-23

### Added

- **Private incident diagnostics** (`GFLOW_CLI_INCIDENT_CAPTURE`, default
  **on**). Relevant operational failures — Flow app crashes, agentic-cohort /
  UI-mode errors, selector drift, transport timeouts, WAF/network/wire-format
  errors, profile-lock contention, unexpected exceptions — now automatically
  write a bounded, **private** incident bundle under
  `<GFLOW_CLI_HOME>/incidents/`: an allowlisted `manifest.json`, structural
  `ui.json` (ligatures/signals/overlay geometry — never raw text),
  `network.json` + `browser.json` ring journals (host categories, canonical
  routes, status codes, text lengths — never prompts/tokens/cookies/bodies/
  signed URLs), and a `sensitive/screenshot.png` for UI-state failures that
  every surface marks review-before-sharing. Nothing is ever uploaded; remote
  MCP/HTTP/worker error envelopes carry only an opaque `{id, capture_status}`
  while the local CLI prints the bundle path. Retention is bounded
  (50 bundles / 250 MiB) and validates ownership before pruning. Raw HAR
  stays separate and opt-in; the manifest's `har_state` now reports honestly
  whether the session's HAR demonstrably finalized. See
  [DEBUGGING § Automatic incident bundles](docs/DEBUGGING.md#automatic-incident-bundles).
  Refs #369, #370, #174, #183.
- **Profile-lock owner evidence.** `ProfileLockedError` contention now
  surfaces the recorded lease owner's PID and observed start time locally
  (never in remote envelopes or logs; identities are per-command HMACs, never
  the raw owner token). The lock-file layout is v1-versioned: byte 0 is the
  kernel-locked sentinel and metadata starts at offset 1, so Windows
  contenders can read it while the lock is held (legacy byte-0 files degrade
  to "evidence unavailable"). The kernel lock remains authoritative — no
  reclaim, unlink, or PID kill is ever performed from metadata. Refs #370.
- **UI-drift debug engine** (`capture_ui_diagnostics`). On a `mode_switch_trigger`
  failure, gflow dumps the composer's **structural** DOM signature — the
  Material-Symbols ligature inventory, `crop_*` presence, textbox count, and
  bounded overlay geometry — to a JSON alongside a **full-page** screenshot,
  so a Flow frontend change is diagnosable from one artifact instead of a
  bare (often black) viewport shot. Consolidated onto the incident-bundle
  engine: the earlier in-development raw URL/title/body-text fields were
  removed before release. (#183)
- **`website/docs/` PII-leak CI guard** (`scripts/ci/check_website_docs_pii.py`).
  The published mkdocs mirror under `website/docs/` is an anonymized copy of
  canonical `docs/`; this new gate fails CI (and runs in `/gflow:check`) if a
  known private identifier — `denon82`, the OS username `ffrol`, a real
  name/email — appears in a published file, catching an anonymization miss
  before it ships (the failure mode of #362). Public references such as
  `github.com/ffroliva/gflow-cli` and the APP_AUTHOR path are trap-free by
  construction, so the guard needs no allow-list.

### Fixed

- **Incident-capture hardening from the max-effort review** (14 confirmed
  findings fixed pre-release): journals now stage first and page artifacts
  share the 8s budget via a deadline, so a fully wedged renderer can no longer
  cost the whole bundle or bypass suppression; the legacy mode-switch debug
  dump writes structural JSON only (the unmarked full-page screenshot that
  previously landed in the plain output dir on every ordinary run is gone —
  the bundle's `sensitive/` screenshot is the one imagery artifact); capture
  now also covers `generate_character_image` (which additionally gains the
  exit-15 closed-browser mapping) and `upsample_image`, plus the common
  stale-Chrome launch-crash contention path (#293); manifests report
  `har_state: complete` only when the context demonstrably closed gracefully;
  bundles staged for unexpected non-typed exceptions are now surfaced (path in
  human output and `--json`); retention runs even when capture is disabled
  (an opt-out no longer freezes previously captured bundles on disk), runs off
  the event loop, and an undeletable pending bundle can no longer condemn
  healthy ones; a Windows sharing violation during manifest finalization gets
  a bounded retry; `browser.json` records the real JS error class
  (`TypeError`/`ReferenceError`) instead of the constant `Error`; worker
  daemon tasks rebind a per-task correlation id so incident ids are unique
  and joinable to queue rows.
- **`FlowAppError` and `FlowAgentUiError` now report `retryable: true` on
  every machine-readable surface.** Both are documented (and presented to
  humans) as transient/retryable, but the CLI `--json` payload said
  `retryable: false` and the MCP / worker-queue error envelopes carried no
  retry signal at all. The classification now lives in one shared
  `errors.is_retryable()` consumed by CLI JSON, MCP tool envelopes, and
  persisted worker-queue error payloads — the three surfaces can no longer
  drift.
- **Documentation drift corrected against the code:** `gflow image batch` docs
  claimed generations run in parallel inside Flow (the transport is strictly
  serial — each prompt's generation is awaited before the next submission)
  and listed `--fail-fast` as the default (the CLI default is
  `--continue-on-error`); the root plan still labeled the MCP server as
  unshipped (shipped v0.21.0/v0.23.0).
- **Flow's new media-library / agentic A/B cohort now fails cleanly.** When a
  project opens into Flow's full-page media-library (or agentic chat) composer —
  which has no classic `crop_*` aspect/mode control — `gflow image` and
  `gflow video` now raise a clear, retryable `FlowAgentUiError` ("this cohort
  flaps; retry shortly") instead of the misleading `UiSelectorDriftError`
  "file a bug". Detection is a runtime DOM scan at a new shared
  `_mode_switch_error` raise site covering **both** the image and video paths.
  Refs #174, #183.
- **Flow web-app crashes now fail cleanly instead of "file a bug".** When Flow's
  web app renders its client-side-exception error boundary (a transient Flow
  crash) instead of the editor, the mode-switch raise site now raises a retryable
  `FlowAppError` (exit code 31) — "transient Flow error, retry shortly" — rather
  than the misleading `UiSelectorDriftError`. Detected via the Flow error-page
  title; surfaced by the #183 UI-drift debug engine.
- **Experimental transport self-lockout.** The `bearer` and `sapisidhash`
  transports discard a caller-supplied Playwright page and re-acquire the profile
  `ProfileLease` in their own `setup()`. Driving one via `GFLOW_CLI_TRANSPORT`
  inside a `FlowApiClient` (which already holds the lease) self-locked with an
  opaque `ProfileLockedError`. `FlowApiClient` now refuses these standalone-only
  transports up front with a clear `ConfigurationError` naming the transport and
  pointing to `ui_automation`/`evaluate_fetch` or standalone use. `evaluate_fetch`
  is unaffected (it shares the client's page). New `STANDALONE_ONLY_TRANSPORTS`
  set + `resolve_transport_name()` helper in `api/transports`.

## [0.42.0] — 2026-07-21

### Fixed

- Content-safety `400` responses from Flow are now classified as
  `ContentPolicyError` (exit code 5) instead of the misleading `WireFormatError`,
  so callers can branch on a policy rejection deterministically. (#359)
- Corrected a false `flow_operation_id` invariant. Live verification found
  `veo-lite` can produce a `remote_started` checkpoint with `operation_id` unset,
  contradicting the prior claim that only `omni-flash` omits the operation name.
  `flow_operation_id` is best-effort/optional; `media_id` is the canonical handle
  used by polling, download, and every CLI lookup. No behaviour change; a
  regression test now locks the operation-name parser against the real veo
  capture fixture. (#361)

### Changed

- **Replaced Gemini CLI with Antigravity (`agy`) as the supported Google coding
  agent** across skills and docs (Google retired Gemini CLI in favor of the
  Antigravity harness). Antigravity auto-discovers `AGENTS.md` natively, so the
  dedicated `GEMINI.md` hub is **removed** and Antigravity is listed among the
  auto-discovering tools in `AGENTS.md`. (#360)
- **`llm-council` skill:** the `high` tier's second external reviewer is now
  Antigravity (`agy`) instead of Gemini CLI; `agy` is promoted from an opt-in
  tool to the pinned `high`-tier slot and the `--include-agy` flag is dropped.
  When `agy` is unavailable or fails non-interactively, the skill suggests
  installing Antigravity or substituting another external CLI coding agent rather
  than failing. (#360)
- Forward-looking agent references updated for consistency across `AGENTS.md`,
  `CLAUDE.md`, `README.md`, `llms.txt`, `docs/INDEX.md`, `docs/AGENT_GUIDE.md`,
  `skills/*`, and CI helper strings. Historical release notes and past
  verification records are left unchanged. (#360)

### Removed

- Removed the dead `fail_processing_tasks` daemon-recovery method, superseded by
  the checkpoint-classifying `recover_processing` (internal cleanup; no
  user-facing behaviour change). (#361)

### Documentation

- Added an onboarding page and quickstart flow to the published docs site, and
  anonymized personal account data across it. Security vulnerabilities now report
  through GitHub's private vulnerability reporting rather than a personal email. (#362)
- Corrected the same-profile concurrency framing (MCP page and docs) to the
  cross-process `ProfileLease` fail-fast contract (`ProfileLockedError`, exit 11),
  and recorded that the POSIX `fcntl.flock` lease was verified green on the
  Windows/macOS/Linux CI matrix. (#361)

### Tests

- Added live-gated end-to-end tests for crash-recovery (a crashed post-submit task
  recovers as `indeterminate` and is never resubmitted) and cancellation-safe
  teardown (the profile lease releases on mid-launch cancel). Both were confirmed
  against real Flow on 2026-07-21. (#361)

## [0.41.0] — 2026-07-20

### Added

- **Cross-process profile lease** ([#357]): new `ProfileLease` with
  process-local guard + kernel advisory lock (`msvcrt` on Windows, `fcntl`
  on POSIX), keyed by canonical profile dir. `ProfileLockedError` (exit 11)
  on contention. Integrated at all 10 persistent-context-owning boundaries.
  Daemon's overwriteable `profile.lock` removed.
- **Versioned worker queue payloads** ([#357]): `schema_version: 1` codec
  validates before Playwright starts; unknown versions raise typed
  `QueueSchemaError` (exit 30). Legacy V0 (missing field) still accepted.
- **Atomic queue task claims** ([#357]): single SQLite `BEGIN IMMEDIATE`
  transaction (select → decode → fail-invalid-without-browser → conditional
  `pending→processing`) shared by daemon and MCP; duplicate per-profile lock
  maps removed. Checkpointed execution phases (`claimed → submit_attempted →
  remote_started → terminal`) with conservative `may_have_spent`.
- **`/gflow:live-verify` skill**: pre-flight state check + per-feature
  live-verification gate before claiming feature completion.
- **Driver honesty improvements** ([#357]): removed classic driver's
  `await_images()` that only raised; replaced late `driver._transport = self`
  mutation with typed `SupportsSendPrompt` injection; agentic submit takes
  request + expected count directly. Frozen `TransportSetup` applied through
  public `apply_setup()` seam replacing client writes to transport-private
  fields.

### Changed

- **Cancellation-safe browser teardown** ([#357]): per-step
  `shield(wait_for(...))` bounded cleanup in required order (stop work →
  cancel worker → persist state → close browser/driver → close stores →
  release lease), re-raising original cancellation last.
- **Mention-index outages fail closed** ([#357]): `MentionIndexUnavailableError`
  (exit 29) with unavailable source name; mention-free prompts stay
  pass-through. Empty source ≠ unavailable source.
- **`OperationRecorder`/chain-repo datastore ownership** ([#357]): stores
  only close the `DataStore` they create; injected stores never closed by a
  non-owner.
- External-CDP browser lifecycle removed by evidence (no production
  consumer, unauthenticated debug port, ambiguous ownership, recorded
  WAF-rejection); Chrome discovery/channel helpers preserved.

### Removed

- **`gflow video batch`** — the manifest-driven video batch command was
  removed. It never worked end-to-end; every invocation exited immediately
  with a stub error before reaching Flow. `gflow image batch` (manifest-driven
  image generation) is unaffected and remains supported.
- External-CDP browser lifecycle (no production consumer).

### Security

- Cancellation-safe teardown prevents resource leaks on interrupt.
- Profile lease kernel advisory lock prevents concurrent profile access
  across processes.

[#357]: https://github.com/ffroliva/gflow-cli/pull/357
[`docs/LIVE_VERIFICATION_v0.40.0-production-readiness.md`]: https://github.com/ffroliva/gflow-cli/blob/main/docs/LIVE_VERIFICATION_v0.40.0-production-readiness.md

## [0.40.0] — 2026-07-19

### Added

- **Prompt `@`-mention resolution for asset tagging** ([#344]): `@Name` in a
  t2i/i2i/video prompt resolves to a staged, taggable character entity
  (`services/mentions.py`), across the `image`/`video` CLI paths, the async
  worker, and MCP tools. Media-asset (non-character) `@`-mentions also work,
  but on the **image path only** — video-path media mentions are Phase 3. A
  bare character with no reference images is rejected early with a clear
  error instead of failing deep in the UI attach. De-tagged prompts are
  persisted to the catalog. See
  [`docs/REFERENCE_STRATEGIES.md`](docs/REFERENCE_STRATEGIES.md) for
  `@`-mention vs `--reference-entity` vs `--ref`, and
  [`docs/LIVE_VERIFICATION_v0.40.0.md`](docs/LIVE_VERIFICATION_v0.40.0.md)
  for the live e2e evidence.

[#344]: https://github.com/ffroliva/gflow-cli/issues/344

## [0.39.0] — 2026-07-19

### Added

- **Failed generations are now persisted to the local catalog** ([#341]):
  every paid-generation path (`video t2v/i2v/r2v`, `video chain`,
  `image t2i/i2i`, multi-prompt t2i, `gflow run`, `image batch`, `movie run`,
  and the async worker) records a terminal `status="failed"` operation row —
  with a stable `error_type` derived from the exception's RFC 9457
  `problem_type` (`waf-rejection`, `content-policy`, `auth-expired`, ...) and
  a redacted `error_detail` — before the error propagates. WAF-403 block
  onset, duration, and recovery windows are now measurable instead of
  reconstructed from memory.
- New `gflow data list errors [--profile] [--limit] [--offset] [--json]`
  subcommand: browse failed operations newest-first (Rich table on a TTY,
  JSONL otherwise).
- Videos whose poll completes with a Flow-reported failure
  (`succeeded=false`, e.g. `PUBLIC_ERROR_UNSAFE_GENERATION`) are now recorded
  as `failed` with `error_type=generation-failed` — previously they were
  recorded as `succeeded` (all paths: CLI, chain, movie, worker).

### Security

- `error_detail` values are scrubbed before persistence
  (`Bearer`/`SAPISIDHASH` tokens, auth-cookie pairs, signed URLs; 500-char
  cap) and non-`GFlowError` messages are stored only as SHA-256 hashes.
  Prompts on failed rows honor `GFLOW_CLI_HISTORY_PROMPTS`; note `store` mode
  therefore also stores prompts of content-policy-rejected generations.
- The experimental REST transports (`bearer`, `sapisidhash`,
  `evaluate_fetch`) now redact response bodies BEFORE truncating them into
  exception messages, closing a partial-secret leak into logs and (new) the
  catalog DB.
- The worker queue's `generation_queue.error_json` now applies the same
  redaction: `GFlowError` details are scrubbed and non-`GFlowError` messages
  are stored as SHA-256 hashes instead of raw text.

[#341]: https://github.com/ffroliva/gflow-cli/issues/341

## [0.38.1] — 2026-07-17

### Fixed

- **Agentic-pin recovery: opt-in reload after a real toggle-off** (#338): when a REAL
  (actionability-checked, unforced) Agent-toggle click lands but the classic media panel
  never mounts in place (the 2026-07-17 both-accounts pin), `ensure_media_mode` can now
  reload the page once and re-run its loop — a fresh load both re-rolls the server's
  per-load cohort arm and mounts the server-persisted `isAgentModeToggled=false`
  preference (`docs/superpowers/spikes/2026-07-12-ui-cohort-backend-config.md`). The
  reload is **opt-in** (`allow_reload`, threaded via `_exit_agent_mode`) and sanctioned
  only from `get_ui_driver`'s pre-bind CLASSIC path, which re-verifies the cohort after
  any navigation — mid-flow image/video mode switches keep strict no-navigation
  semantics (their bound driver's cohort must not be re-rolled underneath them).
  Robustness details from the review pass: a slow in-place panel mount gets a 4s grace
  poll before any reload (parity with the callers' old trigger-probe tolerance); after
  the reload a composer-readiness poll (up to 8s) replaces the fixed settle so the SPA
  re-mount is never probed as a blank shell; the toggle click is unforced-first (a
  forced click can flip the DOM without firing the React handler that persists the
  setting), and the force fallback re-reads `aria-pressed` before clicking — Playwright
  can raise AFTER the events dispatched, and a blind second click would re-enable agent
  mode. A force-fallback click never arms the reload (nothing was persisted).
- **Composer-render race in mode control (found by the v0.38.1 live-verification gate)**:
  `ensure_media_mode` probed the freshly-navigated page before the SPA composer rendered —
  every selector counted 0, the loop broke as "nothing actionable" in ~100 ms, and the Agent
  toggle was likely never clicked at all in the prior production failures. An initial
  composer-readiness poll (up to 8s) now absorbs the render race; live-verified breaking a
  real ~2h agentic pin on the affected account (`docs/LIVE_VERIFICATION_v0.38.1.md`).
- **Crop-selector drift between the mode controller and the cohort detector**: `mode_control`
  probed only 2 of the 6 `crop_*` ratio icons while `drivers/factory` probed all 6 — the
  canonical 6-icon tuple now lives in `mode_control` (the leaf module) and `factory` imports
  it; `test_selector_symmetry.py` locks the identity.

## [0.38.0] — 2026-07-17

### Added

- **Robust agentic↔classic UI mode control** (#332): `--ui-mode` is now driven by a state-aware mode controller that reads Flow's Agent toggle (`aria-pressed`, locale-invariant) instead of icon heuristics — the "Tools" icon present in both modes could previously be misread as a forced-agentic cohort, causing spurious "not recoverable" aborts. `--ui-mode classic` now reliably reaches the classic editor: the controller closes the chat sidebar, toggles off only when actually in agent mode, and verifies the result. Live-verified with a full classic→agent→classic round-trip on real Flow.
- **i2i reference dedup via picker filename search** (#314): repeated local `--ref` images in `gflow image i2i` are attached by selecting the already-uploaded library tile (exact-filename picker search, project-scoped) instead of re-uploading — ending duplicate library pile-ups (the reported 8× `son.jpg`). Falls back to upload when the filename isn't found, and scrolls the virtualised picker grid so off-screen matches are still selected rather than silently re-uploaded (#335). R2V video refs keep upload-only behavior.
- **PR-Triage Autopilot (#238, #333)**: deterministic Stage 0 pre-filter and ephemeral Docker container sandbox executing the `/gflow:pr-council-review` skill against qualified external PRs hourly on the host VPS. Includes Telegram notifications and a persistent audit ledger. Implementation + fixture evals ship in this release; host-side deployment is staged separately.

### Changed

- **Cognitive-complexity refactor of 5 pure-logic functions** (#331): extracted cohesive private helpers to bring them under the Sonar S3776 threshold with zero behavior change (verified by types, scoped tests, and an adversarial behavior-diff review). The remaining S3776/S107 findings in essential-complexity live-automation code were accepted in SonarCloud with justification.

## [0.37.0] — 2026-07-17

### Fixed

- **Agentic image count enforcement** (#313): in the agentic (conversational) Flow UI cohort, the requested image count (`-n`) is now reliably enforced via the Agent settings panel, reworked to reuse classic mode's robust count-tab primitives (a stale sticky default there could previously override the natural-language directive). Covered by a live regression test.

### Changed

- **UI-automation viewport enlarged to 1920×1080** (from 1280×800) to reduce the static browser-fingerprint signal with the most common real desktop resolution. Only the `UiAutomationTransport` context is affected; the `FlowApiClient` REST context (1280×720, selector-irrelevant) is unchanged. Enlarging stays within Flow's desktop layout, so selectors are unaffected. Timing/click humanization from #315 remains out of scope — parked under ADR-13 (the current stealth stack already measures a 0.0% WAF-403 rate). (#315)
- **Auth-login viewports harmonized to 1920×1080** so a profile logs in at the same size it later generates with — `internal_chromium` (Playwright viewport) and `real_chrome` (`--window-size`). Login-window only, not selector-bound. (#315)

### Security

- **FIPS-safe SAPISIDHASH:** the protocol-mandated SHA-1 in Google's SAPISIDHASH computation is now marked `usedforsecurity=False` — it is a protocol hash, not a security primitive, and this lets the call succeed under FIPS-mode Python (which otherwise rejects SHA-1). The digest is byte-identical, so authentication is unchanged. (#329)

## [0.36.0] — 2026-07-16

### Added

- **`GFLOW_CLI_HAR_PATH`:** captures full Playwright network traffic (requests, responses, headers, cookies) to a HAR file for the session — useful for diagnosing wire-format surprises or WAF rejections. Opt-in, env-var only; the file is hardened to `0600` on POSIX after Playwright writes it (#316).
- **`GFLOW_CLI_DEBUG_TRACEBACK`:** prints the real exception message + traceback for unhandled errors — to the console and, under `--json`, into the payload's `error.detail`/`error.traceback` fields — instead of the generic placeholder. The structured telemetry event stays SHA-256-hashed unconditionally either way; this only changes what the operator/caller sees (#316).
- **`llm-council` skill:** `/gflow:llm-council` composes with `pr-council-review`, adding `codex`/`gemini` (opt-in `agy`) as independent external reviewers alongside the internal Claude-subagent council for high-stakes reviews (#320).

### Fixed

- **Reference entity smuggling:** a poisoned character entity (from a `gflow character create` that failed mid-workflow, e.g. the body-triptych step) could leak its `referenceEntities` into unrelated `gflow image i2i` calls in the same project workspace, even when the caller never passed `--reference-entity`. The UI-automation interceptor now strips unrequested `referenceEntities` before submit (#312).

## [0.35.0] — 2026-07-14

### Added

- **Multimodal Reverse-Engineering:** Integrated `gflow reverse-engineer` with `claude-video`'s `watch.py` script. When a video file or URL is passed, gflow now automatically extracts frames and uses the multimodal capabilities of Gemini to reconstruct a detailed, structured prompt.
- **Storyboard Creator:** Added a new built-in tool `storyboard` designed to scaffold a cohesive multi-panel storyboard from a narrative concept or scene description, ensuring visual and stylistic continuity across all panels.
- **GitHub Pages Site:** Created a static site structure for GitHub Pages, including a landing page and material-based documentation layout, with a polished theme aligned with project aesthetics (#308, #309).

### Changed

- **Dynamic Token Budget:** The `maxOutputTokens` parameter for Gemini prompt expansion now dynamically scales as a fraction of `max_output_chars` (approx. 1 token per 4 characters), clamped to a minimum floor of 512 tokens.
- **Agent-Agnostic Skills:** Refactored six command protocols and relocated them to the `skills/` directory to ensure they are accessible by any developer or AI coding agent regardless of the tool being used (#305).
- **Security Updates:** Bumped the `pillow` dependency to `>=12.3.0` to address 5 CVEs (PYSEC-2026-2253..2257) (#306).
- **Ruff Dependency:** Updated the dev-dependency `ruff` from `0.15.20` to `0.15.21` (#304).

### Fixed

- **Type Safety:** Added explicit `dict[str, object]` type annotations to the payload building in `PromptExpander` to satisfy strict Pyright invariance checks on dictionary values.

## [0.34.0] — 2026-07-12

### Added

- **`--ui-mode` / `GFLOW_CLI_UI_MODE` — the CLI can drive the Flow UI arm the command needs, both directions (#299).** Flow serves a **classic** composer or an **agentic** chat cohort, server-assigned and flapping per page load. Before generating, gflow now determines the **required** arm, switches to it as a prerequisite (classic↔agentic DOM toggle), **verifies** via a DOM re-probe, and — if the arm is unreachable — aborts *before* submitting with the new `UiModeUnavailableError` (**exit 28**, retryable), no credits spent. Values: `auto` (default; bind whatever renders), `classic` (require the hard aspect controls), `agentic` (require the chat surface). The required arm is also **inferred**: agent instructions (`-i`) are agentic-only, so they force agentic — closing the #267 gap where `-i` on a classic roll was *silently dropped* (now it either binds agentic or fails fast). Exposed as the **`--ui-mode` flag** on `gflow image t2i` / `i2i` (single-prompt; batch uses the env var) and the **`ui_mode` param** on the `gflow_generate_image` MCP tool; `--ui-mode classic` + `-i` is a fail-fast usage error. Grounded in the #299 spike (`docs/superpowers/spikes/2026-07-12-ui-cohort-backend-config.md`). Applies to every image generation via the shared driver seam. Honest ceiling: a server-side experiment can pin the arm, in which case the switch can't win — the abort still saves the credits.

### Changed

- **`GFLOW_CLI_PREFER_CLASSIC` and `GFLOW_CLI_FORCE_AGENT_UI` are deprecated (#299)** in favor of `GFLOW_CLI_UI_MODE=classic` / `=agentic` (both still work, mapping to the new modes with a `DeprecationWarning`). **Behavior change:** the old `prefer_classic` silent fallback to agentic when the classic toggle was unavailable is gone — a classic-required run now aborts with exit 28 instead. Pipelines that relied on "always yields a file" from `PREFER_CLASSIC=1` must handle exit 28 (retry / switch profile / `GFLOW_CLI_UI_MODE=agentic`).

## [0.33.0] — 2026-07-12

### Added

- **Configurable anti-bot jitter (#241).** The pause between multi-prompt image submissions is now tunable via `--jitter MIN-MAX` on `gflow image t2i` / `gflow image batch` or the `GFLOW_CLI_JITTER_RANGE` setting (env var or `.env`; flag beats env; a single number `N` means uniform 0–N like `video chain --jitter`; `0` disables; bounds must be finite and ≤ 3600 s). The previously **unpaced** paths — `t2i --prompts-file` / `--stdin` / multi-positional and `gflow run` image batches — now pace by default too; field data showed unpaced bursts tripping Flow's WAF (403 `PUBLIC_ERROR_UNUSUAL_ACTIVITY`). WAF cadence behavior and cooldown guidance documented in [DEBUGGING § WAF cadence](docs/DEBUGGING.md#waf-cadence).
- **`--project-name TEXT` on `gflow video i2v` (env: `GFLOW_CLI_PROJECT_NAME`).** The media picker's project menu lists projects by display NAME only — no ids anywhere in its markup, and unnamed projects show nothing but creation timestamps (live round-4 dump: 80 recency-ordered `menuitem`s). When gflow attaches an in-project asset by media UUID it must first select the right project in that menu, so it needs the project's display name. Automatic derivation is best-effort but live-validated (round 5 confirmed the editor tab title carries the name: `Google Flow - <project name>`); `--project-name` is the explicit, highest-precedence override for when derivation fails on other cohorts/locales — a permanent escape hatch, not a workaround. Threads `GenerateVideoRequest.project_name` through to the picker project-menu match; documented in `.env.template`. CLI-only for now: the MCP `gflow_generate_video` tool has no media-UUID frame inputs yet, so the override has no consumer there (command-level parity unaffected; the field rides along when ref-id support lands) (#287).

### Changed

- **Default image-batch jitter lowered from 3–7 s to 0.5–1.5 s (#241).** The default is deliberately minimal — enough to break a perfectly uniform burst signature without wasting wall-clock. Widen (`--jitter 10-30` / `GFLOW_CLI_JITTER_RANGE=10-30`) when runs start hitting WAF 403s, then dial back once the score decays.

### Fixed

- **`gflow video i2v <media-uuid>` now reaches assets deep in a crowded project's virtualised media grid (#287 — primary fix, part 1: scroll on the RIGHT node, progress-bounded).** The live repro: `TransportTimeoutError` ("Start frame asset ... could not be located in the media picker") on a ~100+-asset project for an asset that WAS in the project and in the local catalog, while the same command worked from a small scratch project. `_select_existing_asset`'s scroll fallback had a fixed budget (12 scrolls x 500 px), capping the reachable depth of the react-virtuoso grid regardless of grid size — and the round-6 audit exposed a second layer: the scroll was a blind hover+wheel over the dialog, but react-virtuoso scrolls its OWN container, so a wheel over the wrong node is a silent no-op that looks exactly like "end of grid". The scroll (shared with `_find_picker_entity_tile` via `_scroll_picker_grid_until_rendered`) now drives the dialog's ACTUAL scrollable element via JS (`[data-virtuoso-scroller]` preferred, then the first overflow container; hover+wheel kept as fallback when the probe fails), and is bounded by evidence of progress: it keeps scrolling while the set of rendered tile identifiers still changes between scrolls — depth proportional to grid size — stops after 3 consecutive no-progress scrolls, retains the legacy 12-scroll budget when the DOM probe yields no evidence, and caps at a 200-scroll hard ceiling. Every scroll probe event reports WHICH node moved (tag + class) and its scrollTop before/after, so a wrong-node no-op (frozen scrollTop) is visible in telemetry. The not-found contract is unchanged: same `TransportTimeoutError` naming the slot and UUID (#287).
- **CLI-resolved PROMPT search hints surface UUID frame refs via Flow's media search (#287 — primary fix, part 2).** Live rounds proved Flow's picker search does NOT index media UUIDs (both the full-UUID and UUID-stem tiers found nothing), but each picker tile's `alt` text carries the asset's generation PROMPT — which the search does index. New `get_asset_prompt(db_path, media_id)` catalog query (latest output-operation prompt by `flow_media_id`); `gflow video i2v` resolves each UUID frame ref's recorded prompt and passes its first 6 words as `GenerateVideoRequest.search_hints` (best-effort — no catalog, no hints; layering respected: the CLI resolves because it owns catalog access, the transport only consumes). `_select_existing_asset` types each hint into the picker search box after the UUID tiers and still matches tiles by UUID-in-`src` among the results, so an imprecise hint can only surface extra tiles, never select a wrong one; a hint hit skips the scroll fallback entirely. The pre-existing tiers (display name, media UUID, UUID stem) are kept as cheap first attempts (#287).
- **Hardening: the media picker's library view is verified/aligned to the target project before any UUID asset lookup (#287 — demoted from root cause after round 5).** Round-2's DOM dump appeared to show the picker's library open on a DIFFERENT project ("gflow-cli t2i", 16 tiles, target id nowhere in the dialog), and rounds 2-5 built project-alignment machinery on that reading. The round-5 title telemetry then inverted it: the raw tab title on `/project/f6caf027` was "Google Flow - gflow-cli t2i" — the trigger's "gflow-cli t2i" WAS the target project's actual display name, so the picker had been on the correct project all along and the round-2 evidence was a mirage. The machinery stays as hardening (the picker's library IS per-project with its own selector, so it CAN open on the wrong project — now that state is detected and corrected, and the round-5/6 name derivation gives the match a working source): `_sync_picker_project` runs each time the frame-slot / Add-Media dialog opens: it derives the target project id from the editor URL, resolves the project's display NAME (the menu renders names, not ids; live round-3 run confirmed the previous href/class-only page probes find nothing) — precedence: the `--project-name` / `GFLOW_CLI_PROJECT_NAME` override, then the editor tab title (`document.title` — the editor was navigated to `/project/<id>` before the picker opened; "Flow" AND "Google Flow" branding is stripped tolerantly across separator variants — the live-observed pattern is `Google Flow - <project name>` — branding-only titles are rejected so 'flow' can never contains-match a project like 'gflow-cli i2i', and the raw title is logged on every resolution so the real live pattern stays learnable), then an element whose `href` references the project id, then a project-title-classed element (the local catalog's `projects.title` was considered but the transport layer has no catalog access, and the live page also covers renames and non-gflow projects) — probes the trigger cascade with the live-observed Radix `ProjectDropdownSubTrigger` class first (the generic `aria-haspopup='menu'` fallback tier explicitly excludes the sibling `SortDropdownSubTrigger` — "Recentes" on the observed pt-BR profile — and no tier matches on locale-dependent labels), no-ops when the trigger already shows the target project (by id, or by resolved name — exact OR contains, since the trigger text carries icon-ligature noise; round 5 wasted ~30 menu probes hunting for a project it was already in because the probe demanded exact equality) or when no selector exists (older cohort), and otherwise opens the Radix submenu (click, then hover, then focus+ArrowRight — SubTriggers may ignore plain click — each step verified against the portal-rendered `[role='menu'][data-state='open']`), polls up to ~3s for the portal to POPULATE (live round 3: the open-state flips before the project list renders — matching an empty portal was a guaranteed miss), and clicks the innermost candidate matching the target — anchors whose `href` carries the project id first (round 3 also showed the portal holds ZERO classic menu-item ARIA roles, so the sweep covers generic clickables: `a`, `button`, `li`, `div[role]`, plus the ARIA roles), then the id anywhere in markup, then the normalized name (exact match before contains, so timestamp-labeled scratch entries like "Jul 11, 11:00 PM" can't collide). When the in-view match misses, the open portal itself is scrolled with the same progress-bounded pattern (scroll one step, re-match, stall-terminate when the rendered item set stops changing, hard ceiling) — the recency-ordered menu holds EVERY project and the target's entry can sit below the visible fold. A miss closes the menu (Escape) and proceeds, leaving the asset lookup as the authority.
- **Every picker-lookup decision point now emits structured telemetry, and each failure mode leaves a bounded DOM dump (#287 diagnosis).** The first live verification failed with zero events from the new code paths, making the failing layer indistinguishable (search tiers vs progress probe vs tile matcher vs wrong library project) — this telemetry is what confirmed the root cause in round 2. New `ui_automation_video.*` events: `picker_project_selector_absent` / `picker_project_already_active` / `picker_project_menu_opened` (opened + method: click/hover/keyboard) / `picker_project_menu_populated` (element count after the population poll) / `picker_project_switched` (matched_by: href/id/name) / `picker_project_switch_miss` (menu_opened, menu_elements, candidate count, dump path) / `picker_project_sync_skipped` / `picker_project_name_override` / `picker_project_name_resolved` / `picker_project_name_unresolved` (both name events carry the raw tab title) / `picker_project_menu_scroll_probe` (per menu scroll: rendered item count + new-item delta) / `picker_project_menu_scroll_done` (reason: found / stall / no_menu / ceiling) (project alignment), `picker_search_tier` (term, found, rendered-tile count) and `picker_search_unavailable`, `picker_scroll_probe` (per scroll: rendered-tile count + new-tile delta, plus WHICH node was scrolled — tag + class — and its scrollTop before/after, so a wrong-node no-op scroll with a frozen scrollTop is visible) and `picker_scroll_done` (termination reason: found / stall / legacy_budget / ceiling, total attempts), and `existing_asset_not_found` (media id, project id, screenshot + dump paths). On a final not-found, `_capture_picker_dom_dump` writes `debug_picker_dom_<uuid8>.json` to the out-dir — tile count, the first 3 tiles' outerHTML truncated to 500 chars (enough to see which attribute carries the media identity in a given cohort), the dialog's aria/role/data attributes, the project-selector candidates' outerHTML, and whether the target project id appears in the dialog at all — plus a `debug_picker_miss_<uuid8>.png` screenshot. On a project-switch miss, the OPEN portal's raw innerHTML (bounded to 4000 chars) plus its child-element count and tag histogram are written to `debug_picker_project_menu_<uuid8>.json` and summarized on the event — round 2's closed-trigger dump and round 3's role-filtered item list both left the menu structure invisible; raw markup can't be blinded by role assumptions. All dumps follow the 0.32.1 None-on-capture-failure contract (never report a file that was not written) (#287).

## [0.32.1] — 2026-07-11

### Fixed

- **Browser teardown can no longer hang forever or leak a Chrome tree that locks the profile dir (#293).** A wedged `context.close()` used to be awaited unbounded and its failure swallowed to a warning; stopping the Playwright driver afterwards kills only the Node process, so the detached system-Chrome survived holding the profile — the next run then died at launch with an opaque `TargetClosedError` → "Unexpected error." (exit 1) (observed 3× live, 2026-07-11). Teardown now uses a shared bounded-close helper (generous 30s graceful bound — Playwright hard-kills Chrome mid-profile-flush if a second close arrives during a graceful close, so a slow-but-healthy close must not be escalated; 5s force-close fallback via `context.browser`; 10s driver-stop bound; field resets survive Ctrl-C) across **all three** owned-context teardown paths: `FlowApiClient`, the UI-automation transport's standalone path (its partial-setup guard now also closes a launched context before the driver exits), and the experimental evaluate-fetch transport. A launch-time `TargetClosedError` is now surfaced as `ProfileLockedError` (exit 11) carrying the original error and a kill-the-stale-Chrome remediation, hedged for non-lock startup crashes (#293).

- **Picker grid scroll no longer misses a tile rendered by the final scroll (#283 off-by-one).** `_select_existing_asset` checked the tile count only *before* each scroll, so an asset the last scroll brought into the virtualised grid was never re-checked and the picker gave up with the tile on screen. A post-loop re-check closes it (`_find_picker_entity_tile` shares the loop shape but returns the locator unconditionally, so it was unaffected — now documented).
- **Agentic `await_images` no longer trusts a single exact-count scrape (#283 hardening of the #281 race).** The poll loop breaks only when the new-UUID set is identical across two consecutive scrapes at the expected count (~one extra 0.5s poll); a set that transiently hits the exact count and then grows surfaces as the #281 `MediaAttributionError` instead of being returned as "the" generated media.
- **Debug screenshots that fail to capture are no longer reported as if they existed.** `_capture_debug_screenshot` returned the target path even when `page.screenshot` raised (observed live 2026-07-11: an error message pointed at a file that was never written). It now returns `None` on capture failure and every error message appends its `Screenshot:` clause conditionally (new `screenshot_clause` helper) (#283).

### Changed

- **UUID-shape validation consolidated onto `gflow_cli.api.video.is_media_uuid`.** The four per-module private `_UUID_RE` copies (cli_image, cli_instructions, image_upscale, mcp/tools) now delegate to the public helper introduced in #290; no behavior change. `MediaAttributionError` raises in the recorder now carry `route=` provenance, the agentic ambiguity raise no longer duplicates the class remediation text, `image i2i --ref` help uses the same "media UUID" vocabulary as `video i2v`, and a bad value passed via the deprecated `--end-image` alias names `--end-image` (not `--end-frame`) in its usage error (#283).

## [0.32.0] — 2026-07-11

### Added

- **`gflow video i2v` accepts an in-project asset media UUID for `--initial-frame` / `--end-frame` (and the positional IMAGE).** A UUID-shaped value selects the already-existing Flow asset in place via the same `_select_existing_asset` picker the image `--ref` flow uses (#282 scroll/search fixes included) instead of forcing a duplicate local-file upload — the duplicate-asset pileup and per-run re-upload from the 2026-07-11 chalkboard pilot. Pair with `--project` so the asset's project is the one generated in; a UUID that can't be located in the picker fails with `TransportTimeoutError` (exit 9) naming the slot and UUID (#287).

### Fixed

- **A Flow upload-endpoint rejection is now a typed error instead of "Unexpected error." (exit 1).** An `uploadImage` 4xx during frame/reference attach (observed live: one JPEG rejected with HTTP 400 while byte-identical-format siblings uploaded fine) raised a bare `RuntimeError` that fell through to the generic handler with no hint the *input image* was refused. It now raises the new `MediaUploadRejectedError` (**exit code 27**, RFC 9457 type `media-upload-rejected`) with a re-encode remediation hint (`ffmpeg -q:v 2 -map_metadata -1`) (#287).
- **An explicit `--duration` that cannot be applied now fails fast instead of silently producing a clip of Flow's default length.** When the video settings panel's duration tab probe missed (observed 3/3 on a live 2026-07-11 Frames-submode run: `--duration 4` returned an 8-second clip and the JSON result reported success), `_select_video_duration` demoted the failure to a warning and generation continued on Flow's default. Duration is a contract parameter — downstream timeline math sizes cuts from the requested value — so a probe miss with an explicit `--duration` now raises `UiSelectorDriftError` (**exit code 23**, the #183 selector-drift semantics) with a `debug_no_duration_tab.png` viewport screenshot and an omit-`--duration` remediation hint. Omitting `--duration` is unaffected. Root cause confirmed live 2026-07-11: the duration control is absent from the affected cohort's settings popover (#288, #289).

## [0.31.0] — 2026-07-10

### Fixed

- **Agentic image generation no longer risks attributing a pre-existing project asset to the current request.** `await_images` settled its new-media baseline with a single DOM scrape, which could miss a lazily-rendered pre-existing tile and let `_build_generated_images` slice an arbitrary UUID out of an unordered set as "the" generated image — the 2026-07-10 production incident: an old project logo was silently downloaded and reported as a fresh generation. The baseline is now the **union of two `_scrape_img_srcs` passes** one poll interval apart, absorbing lazy-render stragglers before they're mistaken for new media, and if more new UUIDs still appear than were requested, `await_images` now fails fast with the new `MediaAttributionError` (**exit code 26**, RFC 9457 type `media-attribution`) naming every candidate UUID and the expected count instead of guessing (#281).
- **A pre-download attribution guard now runs ahead of every image download, with `DataIntegrityError` escalated instead of warned.** Even a driver that never hits the agentic ambiguity check above can still hand back a `flow_media_id` already recorded for the profile. `OperationRecorder.verify_media_attribution()` (called from `cli_image.py`, `image_batch.py`'s manifest batch path, and `FlowWorker.process_task`) checks `is_media_recorded()` before `_download_images` and raises `MediaAttributionError` rather than downloading — it also now rejects an intra-batch duplicate `flow_media_id` (the classic transport can return the same media twice in one submission) with no DB lookup needed. Separately, a `DataIntegrityError` from the recorder's `UNIQUE(profile_name, flow_media_id)` constraint — previously caught by the generic `DataStoreError` path and reported as a warn-only "Generated media was saved, but local history was not updated." — now escalates to the same `MediaAttributionError` when (and only when) the failing write is that exact constraint (`route == "data.upsert_asset"`); any other `DataIntegrityError` (e.g. an unrelated `insert_operation`/`link_operation_asset` failure) falls through to the ordinary warn-and-continue path instead of being mislabeled as a media collision. The escalation message now names the full candidate set of `flow_media_id`s / saved paths ("one of ...") rather than always fingering the first image, since the colliding index can't be recovered from sqlite's bare constraint violation (#281).
- **The pre-download attribution guard and the collision-escalation logic are each now a single shared implementation instead of three near-identical copies.** Both originally shipped duplicated across `cli_image.py`, `image_batch.py`, and `worker/daemon.py` (each call site already held a recorder instance), which tripped SonarCloud's duplicated-lines gate. The guard is consolidated onto `OperationRecorder.verify_media_attribution()`; the escalation is consolidated onto the new `gflow_cli.data.recorder.escalate_asset_collision()` — both with no behavior change beyond the route-scoping and intra-batch-duplicate fixes above (#281, #282, #283).
- **`gflow image batch` (the manifest path) now honors `--continue-on-error` for a media-attribution collision.** A collision on one row — from either the pre-download guard or the post-download collision escalation — used to propagate out of the whole batch run regardless of `--continue-on-error`, discarding every other row's already-completed outcome. With `--continue-on-error` the colliding row is now marked as a normal "fail" outcome (nothing was downloaded/recorded for that row) and the batch continues; without it, the collision still aborts the run as before (#281, #282).
- **`--ref <uuid>` picker selection now resolves every ref, not just the first.** `_select_existing_asset` gave up as soon as a tile wasn't in the initial viewport or surfaced by the display-name search, so any `--ref` after the first raised `TransportTimeoutError` once the virtualised (react-virtuoso) media grid hadn't rendered it yet. It now falls back to scrolling and re-checking between scrolls, mirroring the entity picker's `_find_picker_entity_tile` strategy, and `_attach_image_uuid_refs` clears the picker search input at the start of every ref iteration so a search typed while resolving one ref can't shadow the next ref's tile lookup. `_select_existing_asset` itself now also clears a failed display-name search before falling back to scrolling, so it doesn't scroll a grid still filtered by the failed term (#282).

## [0.30.0] — 2026-07-09

### Added

- **MCP `gflow_generate_video` model/duration/count parameters (CLI↔MCP parity):** agents can now select the Veo model (`veo_lite`/`veo_fast`/`veo_quality`/`omni_flash`, aliases accepted), clip duration, and batch count through MCP, matching the CLI `gflow video` flags. An unknown model is rejected up front with a 400 instead of failing deep in the worker; an omitted model still lets the transport apply its i2v veo-lite default (issue #125). Co-authored-by C1ph3r404 (from the closed PR #258). Note: the pre-existing transport-level i2v veo-lite default already protected the MCP path, so this is parity + agent control, not a new credit guard.

### Fixed

- **Character-create recorder no longer crashes on a duplicated `flow_media_id`.** Under the agentic cohort the classic character-editor slot-add control is absent, so the body prompt lands in the still-active face slot and both slots report the same `flow_media_id`; `_record_character_local_files` minted a fresh asset `id` per slot and the second slot violated `UNIQUE(profile_name, flow_media_id)` → `DataIntegrityError`. The recorder now reuses the existing asset id (mirroring `record_completed_video`), making character local-file recording idempotent on the business key. Regression test in `tests/data/test_recorder_character.py`.
- **`prefer_classic` no longer logs a misleading `WARNING` on the agentic cohort.** The server-gated agentic (`tune`) cohort cannot be exited client-side, and `prefer_classic` is best-effort by contract, so `get_ui_driver` now logs that expected fall-through at `INFO` (`ui_driver.prefer_classic.cohort_natively_agentic`) and reserves `WARNING` for genuinely unexpected exit failures.

## [0.29.0] — 2026-07-09

### Added

- **Persistent `gflow instructions` command group:** CRUD over project brief cards (add, list, enable, disable, rm, apply, toggle-mode) with title/ID selection, master toggle, and REST upload support for card reference images. Live-verified credit-free end-to-end (see `docs/LIVE_VERIFICATION_v0.29.0.md`).
- **Declarative full-sync:** `gflow instructions apply FILE` for idempotent sync of instructions from TOML or JSON brief files.
- **Movie manifest instructions integration:** Global `[instructions]` and per-scene `[scenes.instructions]` blocks in `movie.toml` to dynamically sync project brief cards before generating clips (per-scene re-sync memoized via `_BriefSyncCache`; documented as a destructive full-sync).
- **`gflow_instructions_*` MCP tools:** six new tools (`list`, `add`, `set_enabled`, `rm`, `toggle_mode`, `apply`) giving MCP agents the full instructions CRUD surface — thin adapters over the same live-verified brief primitives, credits-free, RFC 9457 error envelopes. `gflow_list_tools` and the new tools are now documented in `docs/MCP.md`. Note: `gflow_generate_video` deliberately has no `instructions` param (the video pipeline has no instructions support — documented asymmetry).
- **MCP↔CLI parity contract enforced in CI:** `tests/mcp/test_cli_parity.py` walks every CLI leaf command and fails when one has neither a mapped MCP tool nor an explicit, reasoned exemption.

### Changed

- **Agentic-indicator selectors consolidated:** the 4 agentic cohort ligature probes are now canonical in `drivers/factory.py` (`AGENTIC_INDICATOR_SELECTORS`, `AGENT_TUNE_INDICATOR_SELECTOR`); both UI transports import them instead of carrying drift-prone copies, locked by a new symmetry test.

### Internal

- **`/gflow:check` and the PR council are now CI-faithful:** the check gate runs the exact CI verify commands (`ruff format --check`), the council pre-flight gained a D0 mechanical CI gate that hard-blocks on a red run, and `check_doc_links.py` joined CI — closing the gap that let a format failure ship past an 8-agent council review.

## [0.28.0] — 2026-07-08

### Added

- **Agent instructions (`-i` / `--instruction`) now actually steer generation.** On an
  agentic-cohort Flow session, `gflow image t2i "…" -i "Every image is a flat 2D crayon
  drawing"` makes the agent adopt that style (live-verified end-to-end). Instruction cards are
  synced to the project's Agent brief via `PATCH /v1/projects/{id}/agentInfo`, and the agent
  folds every **enabled** card into the generation. Cards carry distinct titles and may
  reference image assets. Persistent CRUD (`gflow instructions`) and movie-manifest wiring are
  planned follow-ups.

### Fixed

- **Instructions were silently inert (agentic transport).** Two root causes, both found via a
  live spike: (1) the composer used an imperative `"Generate N images: …"` directive that the
  agent passes to the image tool verbatim, bypassing the brief — now phrased conversationally
  so the agent's reasoning step applies the cards; (2) the brief-level master switch
  `project_brief.enabled` was never set (defaults off on a fresh project → all cards ignored) —
  now enabled whenever cards are synced. Also fixes a wrong PATCH content-type
  (`application/json+protobuf` → HTTP 400, silently) and a hardcoded per-card title that
  collapsed every card to one name.
- **`-i` no longer no-ops without warning on a classic-cohort session:** a clear warning is now
  emitted (instructions only apply on agentic sessions).

## [0.27.1] — 2026-07-07

### Fixed

- **Version Wiring in Movie Handoff:** Properly imported and passed `__version__` to `build_handoff()` generator version instead of using a hardcoded stale default (`0.14.0`).
- **Rich Markup Escaping in CLI Output:** Escaped brackets in `_format_scene_line` plan output (`[t2v]`, `[r2v]`, and `refs=[...]`) so they are not swallowed by Rich console formatting.
- **MCP Server Stale Version:** Wired FastMCP version to `__version__` dynamically on startup, fixing the stale `0.21.0` version trap.
- **MCP Agent Guide:** Added `gflow_list_tools` to the list of available tools in the static MCP agent guide resource.

### Added

- **Document Anchors:** Added HTML anchor (`style-configuration-errors`) to `docs/MOVIE.md` for reserved name `"none"` and unknown style variant `ConfigurationError` paths.
- **Movie Usages Documentation:** Added complete documentation for `gflow movie run` and `gflow movie template` commands to `docs/USAGE.md`.

## [0.27.0] — 2026-07-07

### Added

- **Global `[style]` block with named variants in `movie.toml` (Issue #239):** channel-format
  videos can now express a visual style system (e.g. monochrome → warm color arc) once in the
  `[style]` block and select it per-scene via `style_variant`, without repeating style text in
  every scene's action. Adds `prefix`/`suffix` fields to `[style]`, `[style.variants.*]`
  sub-tables, per-scene `style_variant` and `style_suffix` fields, deterministic composition
  rule, and `style_applied` (variant/prefix/suffix/scene_suffix) per clip in the handoff
  manifest. `none` is a reserved variant name (opt-out keyword) and may not be defined.
- **Prompt-aware resume for `movie run`:** completed scenes now persist a SHA-256 hash of
  their composed prompt (`style_hash`); on resume, a scene whose prompt changed (style edit,
  variant switch, action tweak) is regenerated instead of silently skipped. Unchanged scenes
  still cost nothing. Old state files fall back to comparing the stored prompt text and are
  never re-run on a guess. Dry-run shows the resolved style per scene and marks stale scenes
  as `re-run (style changed)`.

## [0.26.0] — 2026-07-06

### Added

- **Reference a generated image in `image i2i` by its Flow UUID (`gflow_generate_image`,
  `reference_images`)**: pass a generated image's media UUID as a reference and gflow
  attaches it by **selecting the already-existing asset in Flow's reference picker** —
  it does **not** upload a duplicate copy (avoiding duplication is the preferred path).
  The asset's tile is located by its media id in the thumbnail URL (robust to
  display-name collisions), surfaced by a display-name search when it isn't already
  visible, and attached in place. When the asset can't be located (e.g. it's in a
  different project's picker), gflow falls back to uploading its on-disk local file. A
  UUID that isn't in your catalog still attaches by media id (no error). Live-verified:
  a generated image's UUID → i2i output that references it, with no duplicate upload.
- **Generated images now record their Flow display name**: the display name is extracted
  from the `batchGenerateImages` response's `workflows[]` array (previously ignored) and
  persisted in the asset catalog — the searchable label the media picker shows, and what
  the UUID-reference path searches by. (Original find and approach by **@C1ph3r404**,
  #253/#255.)

## [0.25.0] — 2026-07-06

### Fixed

- **Follow-up review fixes for remote image UUIDs (#237/#245)**: a post-merge
  multi-angle review surfaced regressions and defects now corrected:
  - Image `i2i` with a Flow media-id ref that isn't in the local catalog is no
    longer rejected — UUID→display-name resolution is applied only to the video
    paths (image refs attach by media id). Restores the `i2i` pass-through.
  - The generation result envelope's `flow_media_id` again carries the real
    media id (it was returning the asset's `flow_workflow_id`); the workflow id
    is exposed under its own `flow_workflow_id` key.
  - An in-catalog asset with no display name no longer returns its raw UUID as
    the picker search term (which timed out); it fails fast with a clear error.
  - Remote picker tiles match the display name exactly (`get_by_role(exact=True)`),
    so a name that is a substring of another can't silently attach the wrong image.
  - The R2V picker-close timeout matches the I2V budget (was a too-tight 8s that
    aborted slow-but-successful attaches).
  - `_attach_reference_audio` selects its tile by ARIA role+name instead of an
    apostrophe-unsafe `:has-text()` selector.

- **Remote-UUID video attach reworked to use local upload (#237)**: live
  verification found the original mechanism — resolve the UUID to a display name
  and select its tile in Flow's resource picker — could never work for generated
  media: Flow's asset search does not index generation prompts, and generated
  assets carry no display name, so the picker returned "No results found" and the
  attach timed out. The UUID is now resolved to the image's on-disk local file and
  attached through the existing, already-verified file-upload path. The failing
  picker path is no longer used for video UUID refs. (Automatic download-by-media-id
  for the rare case where the local file was pruned is a planned follow-up; for now
  that case fails fast with a clear "Reference Not On Disk" error.)

- **`gflow_generate_image` no longer silently saves a video as an image**: the
  agentic image path has no explicit image-mode toggle — Flow's conversational
  agent infers image-vs-video from the prompt and can produce a *video*, whose
  tile is then scraped as if it were an image. The MP4 bytes were saved with a
  `.png` suffix and catalogued as an image, a silent corruption that only
  surfaced far downstream (Flow 400-rejects the file as an i2v frame → text-only
  #125 fallback). `download_image` now detects video magic bytes (ISO-BMFF / WebM)
  and fails loud with a `WireFormatError` naming the cause, instead of writing the
  corrupt file. (Root cause — the agentic agent producing a video for an image
  request — is tracked separately; this stops the silent corruption.)

- **Rejected i2v/r2v frame uploads now fail loud instead of falling back to T2V**:
  `_upload_via_open_dialog` matched the `uploadImage` response by URL only and
  ignored its status, so a Flow 4xx rejection (e.g. an invalid image file) was
  treated as success — the code committed an empty slot and the generation
  silently produced a text-only video (#125). The upload status is now checked and
  a rejection aborts with a clear error.

### Added

- **Remote image UUIDs in `gflow_generate_video` (#237)**: I2V (`initial_frame` /
  `end_frame`) and R2V (`reference_images`) now accept a generated image's Flow UUID,
  not just a local file path — pipe the output of an image generation straight into a
  video generation. At enqueue time the UUID is resolved to the image's local file
  (already on disk from the image generation) and attached through the same proven
  file-upload path used for a local `--initial-frame`, so no picker name-search is
  involved. A UUID that isn't in your asset catalog fails fast with a clear "Reference
  Not Found" error, and a catalogued asset whose local file is missing fails fast with
  "Reference Not On Disk" (re-generate it or pass a local path) — both instead of a
  long browser timeout. (Contributed by @C1ph3r404; the attach mechanism was
  reworked during maintainer live-verification — see Fixed below.)

### Fixed

- **Removed a shadowed duplicate `Settings.daemon_token` field definition (#243)**: the
  class body defined `daemon_token` twice; Python silently kept only the second
  (aliased) one, leaving the first as dead code that a future edit could touch without
  effect. The surviving definition's contract (both `GFLOW_CLI_DAEMON_TOKEN` and
  `GFLOW_DAEMON_TOKEN` accepted) is now pinned by tests, along with a guard that the
  field is defined exactly once.

- **`$GFLOW_CLI_HOME/.env` now loads as a dotenv fallback (#240)**: `config.py`'s own
  module docstring promised a `.env` fallback "from CWD or `$GFLOW_CLI_HOME/.env`", but
  the implementation only ever read the CWD file — a key placed in the home `.env` was
  silently ignored (easy to miss under the prompt tools' never-fatal contract, and it
  bites any process whose CWD is not a project root: the MCP server launched by a
  desktop client, a worker service, a scheduled task). `Settings` now defaults the
  standard pydantic-settings `_env_file` init kwarg to `($GFLOW_CLI_HOME/.env, ./.env)`
  per construction, so explicit `Settings(_env_file=...)` — including the disable idiom
  `_env_file=None` — keeps working. Precedence: process env vars beat both files, and a
  CWD `.env` beats `$GFLOW_CLI_HOME/.env`.
  - **Home resolution is coherent across every channel**: the home used to locate the
    home `.env` now matches what `Settings.home` reports when `GFLOW_CLI_HOME` comes
    from the process env (case-insensitively, as the env source matches it), from a
    `GFLOW_CLI_HOME` entry in the CWD `.env`, or is set-but-empty (treated as unset
    rather than `Path('.')`). The home `.env` itself cannot relocate home (circular).
  - **Docs reconciled**: `docs/CONFIGURATION.md` § ".env loading", `docs/SECURITY.md`
    (Gemini-key locations) and `.env.template` previously documented CWD-only loading
    and now describe the two-file behavior; the `gflow serve` token hint no longer
    points at `.env.local`, which was never a loaded file.
  - **Worker daemon**: `FlowApiClient` constructions in `process_task` now receive the
    daemon's cached settings instead of re-reading `.env` files live per task, so a
    mid-run edit to the home `.env` can no longer produce a task whose client config
    disagrees with the parameters the task derived from `get_settings()`.


## [0.24.0] — 2026-07-01

### Added

- **`--project` on `video t2v`/`i2v`/`r2v` (#233)**: generate into an existing Flow
  project instead of always creating a scratch project, matching `image t2i`/`i2i`.
  Lets programmatic callers (e.g. a multi-clip storyboard worker) share one project
  across several video generations instead of leaving one throwaway project per clip.
- **`project` parameter on the MCP `gflow_generate_image` / `gflow_generate_video`
  tools**: mirrors the CLI `--project` flag so MCP callers can also target an existing
  Flow project (validated against the same id format). Completes `--project` parity
  across the CLI and MCP surfaces.

## [0.23.0] — 2026-07-01

### Added

- **MCP generation is now functional (#228)**: the `gflow_generate_image` and
  `gflow_generate_video` MCP tools — previously non-functional stubs — now enqueue onto
  the FlowWorker queue and run end-to-end, with the background worker owning download and
  history recording. The `tools` parameter (e.g. `creative-director`) is now applied to
  expand the prompt before generation (it was previously accepted but never applied), and
  reference images are supported across the image (`i2i`) and video (`i2v` / `r2v`) tools.

### Fixed

- **macOS generation 401 — resolved (#222, #230)**: Flow cookies are now read from the
  full cookie jar filtered by domain instead of a path-`/` URL filter that silently
  dropped the `/fx`-scoped `__Secure-next-auth.session-token`; and on macOS — where the
  headed generation context can intermittently fail to decrypt the on-disk cookie store —
  the session is seeded into the context from a snapshot captured pre-launch via the
  working `--password-store=basic` reader. Verified end-to-end on macOS (Apple Silicon) by
  the reporter. Thanks @gunalak.
- **MCP video task safety (#228)**: `i2v` now requires `initial_frame` and `r2v` requires
  `reference_images`, validated at the tool boundary with a clear error; a post-success
  recording failure no longer flips a credit-spent video to `failed`; and any
  non-`completed` task status is reported as a failure rather than a false success.
- **Chrome channel on Chromium-only hosts**: `channel_for_profile()` now gates
  Playwright's `channel="chrome"` on a new `_is_playwright_chrome_channel_available()`
  check that probes only the exact Google-Chrome paths Playwright hardcodes, instead
  of `is_chrome_available()` (which also accepts Chromium). On a host with only
  Chromium installed, the CLI no longer requests `channel="chrome"` and fails with
  `Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome`; it falls
  back to bundled Chromium and logs an actionable warning (#219).
- **Text-to-image extra-image billing**: the generation-settings trigger selector now
  requires `button[aria-haspopup='menu']` (aliased to `MODE_SWITCH_TRIGGER_SELECTORS`),
  preventing a mis-click on an icon-only aspect thumbnail that skipped the count panel
  and left Flow's own default count (typically 2) in effect — billing extra generations
  while the CLI saved only one (#219).
- **Extra-image observability**: `generate_image()` now logs a
  `client.generate_image_extra_returned` warning when the transport returns more images
  than the requested `count=1`, so silent over-generation is surfaced to the user (#219).
- **macOS generation 401 — fail loud (#222)**: a `chrome`-strategy profile that is
  silently downgraded to bundled Chromium at generation launch now raises instead of
  running logged-out, so the macOS auth failure surfaces at its cause rather than as an
  opaque later `401`.

### Diagnostics

- **Persistent-context cookie state (#222)**: generation launch now logs the resolved
  `cookies_db_path` (`client.persistent_context_launch`) and the launched context's own
  cookie state (`client.context_cookie_state`: `flow_session_cookie_present` / count /
  expiry — **never values**, safe to paste publicly). This splits a cookie-**load**
  failure (the macOS persistent context cannot decrypt the profile's cookies) from a
  server-side rejection — the observability that localized the macOS `401` root cause,
  now resolved (see the #222/#230 entry above).

## [0.22.0] — 2026-06-28

### Added

- **Tools framework ("Creative Director")**: a TOML-defined prompt-tool system with
  `creative-director` as the first built-in tool, exposed via two surfaces:
  - **`gflow tools list/show/run`** — discover tools, inspect their styles, and run them
    standalone (e.g. `gflow tools run creative-director "a cat" --style cinema --json`).
  - **`-t` / `--tool` option** on every generation command — `image t2i` / `i2i` / `batch`,
    `video t2v` / `i2v` / `r2v` / `chain` — apply one or more tools before generating
    (e.g. `--tool creative-director:style=cinema`). Repeatable. On multi-prompt batches and
    chains the tool is applied per prompt/link. Replaces the never-released `-e/--expand` flag.
  - The `creative-director` tool rewrites a terse prompt into a vivid one using Google's
    five-component formula (Subject + Action + Context/Location + Composition/Camera + Style)
    via the public Gemini API. Requires `GFLOW_CLI_GEMINI_API_KEY`
    ([get one](https://aistudio.google.com/apikey)); optional `GFLOW_CLI_GEMINI_MODEL`
    (default `gemini-2.5-flash`). Domain-vocabulary modes (`--style cinema`, `portrait`,
    `product`, etc.) inject specialized lens/lighting/colour vocabulary; styles are
    **category-gated**, so image styles apply to image commands and video styles to video.
  - Tool application is **never fatal** — missing key, rate limit, or any API/network fault
    degrades gracefully to the original prompt.
  - **History**: a generation rewritten by a tool records the user's original prompt in the
    `prompt` column, the submitted expansion in `expanded_prompt`, and the applied tool
    (`{name, version, model, params, config_hash}`) in `operations.metadata_json.tool` — all
    honoring `GFLOW_CLI_HISTORY_PROMPTS=redacted` (the redacted form stores only
    `{name, version, params_hash, config_hash}`).
  - MCP parity: `gflow_list_tools` tool and a `tools` array parameter (`[{name, options}]`) on
    `gflow_generate_image` / `gflow_generate_video`, validated and adapted to the CLI
    `--tool` form.
  - **"My Tools"**: drop your own tool TOMLs into `<GFLOW_CLI_HOME>/tools/*.toml` and they are
    registered automatically — listed by `gflow tools list`, usable via `--tool`, and exposed over
    MCP, just like built-ins. A user tool may override a built-in of the same name (logged via
    `tool_user_override`); a malformed user TOML fails loud. See [docs/TOOLS.md](docs/TOOLS.md).
  - The Gemini prompt expander gained an overall **wall-clock budget** (default ~60s per call)
    on top of the lowered 20s per-attempt timeout, so sustained rate limiting can no longer
    block a generation for the full retry schedule before falling back to the original prompt.

### Deprecated

- **`expand_prompt` MCP prompt**: superseded by the `creative-director` tool, which performs the
  rewrite server-side (calls Gemini, strips banned keywords, supports domain styles, records
  provenance). The prompt still works and now carries a `[DEPRECATED]` marker in its
  client-visible description; it is slated for removal in a future major release. Use
  `gflow tools run creative-director` / `--tool` (CLI) or `gflow_list_tools` + the `tools` array
  param (MCP) instead.

## [0.21.0] — 2026-06-26

### Added

- **MCP server** (`gflow mcp run`): a [Model Context Protocol](https://modelcontextprotocol.io) server over **stdio** that exposes gflow's image / video / data operations to MCP-aware agents (Claude Desktop, Cursor, VS Code) as JSON-RPC **tools, resources, and prompts**. Point the client at `gflow mcp run` (example config in `gflow mcp run --help` and [docs/MCP.md](docs/MCP.md)). `gflow mcp setup --target <claude-desktop|cursor|vscode>` is scaffolded for future auto-configuration.
- **MCP over HTTP/SSE** (`gflow serve`): serves the same MCP server over Server-Sent Events — stream at `/sse`, POST messages to `/messages/`. Binds `127.0.0.1:8000` by default; non-loopback binds require `GFLOW_DAEMON_TOKEN`. Foundation for the forthcoming Gflow Studio Web UI and REST `/api/v1` surface.
- **Daemon & generation-queue scaffolding** (internal foundation): a FastAPI lifespan daemon, a `FlowWorker` background processor, and a SQLite-backed generation queue (`QueueRepository` + migration `0007_queue`, swept to `failed` on restart). Lays the groundwork for queued asynchronous generation; not yet wired into a user command (`gflow serve` currently runs the MCP/SSE server only).

### Fixed

- **Security — `cryptography` advisory** ([GHSA-537c-gmf6-5ccf](https://github.com/advisories/GHSA-537c-gmf6-5ccf)): bumped the locked `cryptography` from 48.0.0 to 49.0.0 (transitive dependency).

### Changed

- **CI — dependency CVE gate**: added a `pip-audit` job that audits the locked dependency set on every PR and fails on known advisories.
- **Code health**: SonarCloud cleanup sweep across the CLI, API, transport drivers, and movie/manifest layers (cognitive-complexity, duplicate-literal, and unused-argument refactors) with no behavioral change.

## [0.20.1] — 2026-06-16

### Fixed

- **Aspect ratio overrides in Agentic & Classic cohorts** ([#193](https://github.com/ffroliva/gflow-cli/issues/193)): Fixed a bug where aspect ratio settings were ignored under the pluggable `FlowUiDriver` strategies (both single and batch image generation paths).
- **`GFLOW_CLI_PREFER_CLASSIC`** (or `prefer_classic` setting): Added a configuration setting to force the classic UI driver, allowing users to bypass the Agentic UI cohort if preferred.

## [0.20.0] — 2026-06-15

### Added

- **Forced Agentic UI detection** (exit code 25): dynamically detects if the account's Flow editor page has been placed in Google Flow's new Agentic UI A/B cohort. Raises `FlowAgentUiError` and captures a diagnostic viewport screenshot (`debug_forced_agent_ui.png`) to exit cleanly and prevent CLI hangs.
- **Pluggable Flow UI driver strategy** (`FlowUiDriver`): the UI-automation transport now probes the editor DOM **per generation** (the cohort flaps per page load) and binds a `ClassicFlowUiDriver` or `AgenticFlowUiDriver`, so the two cohorts' selectors never share code. Classic generation is unchanged.
- **Agentic-cohort image generation** (validated live): when the editor is served the Agentic UI, `gflow image` drives it by encoding settings (count / aspect) into the conversational prompt directive and scraping generated assets directly from the DOM — **deduplicated by media UUID** — because the Agentic UI routes generation through a background Web Worker that defeats page-level network capture. Content-policy blocks (detected in alert/dialog regions) fail fast with `ContentPolicyError` (exit 5). Agentic **video** still raises `FlowAgentUiError` (exit 25) pending a validated scraping path.
- **`GFLOW_CLI_FORCE_AGENT_UI`** (testing/diagnostic opt-in): forces the agentic composer by clicking the in-input "Agent" toggle after entering the editor, so the agentic path can be exercised deterministically regardless of the server-assigned A/B cohort (which has no client-readable flag). Unset by default — the cohort is auto-detected per generation. See `docs/AGENT_UI_E2E.md`.

## [0.19.0] — 2026-06-12

### Added

- **Opt-in Patchright browser engine** via `GFLOW_CLI_BROWSER_ENGINE=patchright`
  (default `playwright`, unchanged). Patchright is a drop-in patched Playwright
  (Chromium) that runs page evaluations in an isolated execution context to
  avoid the `Runtime.enable` CDP leak, for stronger reCAPTCHA-Enterprise evasion
  on the **headed** path. It is **not** a headless unlock and must be installed
  separately: `pip install 'gflow-cli[patchright]'`. The default engine is
  byte-identical to before. `gflow auth status` now reports the active engine.
- `BrowserEngineUnavailableError` (exit code 24): selecting `patchright` without
  the package installed now fails with a clear `pip install patchright`
  remediation hint instead of a raw `ImportError` hashed to a generic exit 1.

### Fixed

- An invalid `GFLOW_CLI_*` enum value (e.g. a typo'd `GFLOW_CLI_BROWSER_ENGINE`,
  `GFLOW_CLI_PROVIDER`, or `GFLOW_CLI_LOG_LEVEL`) now fails with a clean
  configuration error and **exit code 11** naming the offending variable, instead
  of leaking a raw pydantic `ValidationError` traceback and exiting 1.

## [0.18.0] — 2026-06-12

### Added

- `UiSelectorDriftError` (exit code 23): UI-automation selector-probe failures — e.g. the
  mode-switch `crop_*` trigger missing from the Flow editor (issue #183) — now raise a typed
  error carrying the probe name, the debug-screenshot path, and a remediation hint, instead
  of an opaque "Unexpected error" (exit 1) whose message was hashed away in logs. Converted
  probes: mode-switch trigger, Image/Video mode tabs, and video sub-mode tabs, in both the
  image and video transports.

### Fixed

- `gflow image` commands (`t2i` / `i2i` / `upscale` / `upload`) now plumb their output
  directory into the API client, so debug screenshots are actually captured on
  UI-automation failures. Previously the transport's screenshot directory was never set on
  the image path and drift errors reported `Screenshot: None` even with `--verbose`.

## [0.17.0] — 2026-06-12

### Added

- Added `verify_flow_profile` in `gflow_cli.auth.verification` using `browser_cookie3` and `httpx` to verify sessions directly from the Chrome cookie store (fast path), with a marker-gated Playwright fallback for encrypted/locked stores. `RealChromeStrategy` now writes the Chrome marker before verification (the fallback reads it) and, on failure, rolls back only a speculative write — a marker that legitimately pre-existed (a previously-verified chrome profile) survives a transient probe failure, and an interrupted verification never leaves an unverified profile claiming the chrome strategy. Cookie extraction is centralised in the new `gflow_cli.auth.cookies` module.

### Fixed

- `gflow_cli.auth.cookies._get_chrome_cookies3` now catches `RuntimeError` (Windows DPAPI failure — `RuntimeError('Failed to decrypt the cipher text with DPAPI')`) in addition to `browser_cookie3.BrowserCookieError`, and re-raises both as `PermissionError` so the Playwright fallback is triggered instead of propagating an unhandled exception.
- `verify_flow_profile` now retries transient HTTP failures (429/503/504) and network errors up to `_MAX_ATTEMPTS` times with exponential backoff, matching the retry behaviour of the existing Playwright-based `verify_flow_session`.

### Changed

- Entity-attach `WireFormatError` failures (exit 7) now carry a remediation hint pointing at Flow's new full-page media-library UI rollout ([#174](https://github.com/ffroliva/gflow-cli/issues/174)) — affected accounts can stage entities via the include action but the submit never carries `referenceEntities`; the error now explains how to tell which UI an account has and where to follow the fix, instead of the generic file-a-bug hint. Both backstops also emit an `entity_attach_context` discovery field (`video`/`image`) for drift telemetry. New KNOWN_ISSUES entry documents the rollout

## [0.16.0] — 2026-06-12

### Fixed

- **`--reference-entity` no longer fails on non-Portuguese Flow accounts** ([#170](https://github.com/ffroliva/gflow-cli/issues/170)) — the resource-picker include selectors hardcoded the pt-BR caption "Incluir no comando"; they are now locale-free tier cascades (context-menu `add`-ligature anchor + pt/ru/en text fallback), fixing `gflow image t2i --reference-entity`, movie R2V entity attach, and Vozes voice attach for every account language. Failures are now typed (`TransportTimeoutError`, exit 9) with a locale-neutral remediation hint, the matched selector tier is logged (`include_selector_tier`) for drift telemetry, and a new image-side submit backstop raises `WireFormatError` (exit 7) if a staged character never reaches the wire instead of silently returning a text-only generation. Thanks @papushin7987 for the live-verified report

### Added

- **`gflow image upscale <mediaId> --scale 2k|4k`** — upscale a platform-generated image to 2K or 4K (Flow's download-menu 1K/2K/4K options) and save it locally; credit-free. The owning project is resolved from the local catalog (or pass `--project` for images gflow didn't record). 4K requires a Flow Ultra subscription — a non-Ultra 4K request fails fast with exit code 22 (`UpscaleUnavailableError`) rather than a generic 403. Reverse-engineered wire documented in [docs/IMAGE_UPSCALE_RECON.md](docs/IMAGE_UPSCALE_RECON.md); live-verified end-to-end (issue #171)

## [0.15.1] — 2026-06-10

### Changed

- Added `--disable-dev-shm-usage` to Chrome launch args in both `FlowApiClient._persistent_context_kwargs()` and `UiAutomationTransport.setup()` — prevents OOM in Docker containers with the default 64 MB `/dev/shm` allocation; no effect on developer machines with adequate shared memory
- README Stats badges hardened against shields.io outages — GitHub-stat badges now pass `cacheSeconds=3600` (mitigates shields.io's shared GitHub token-pool exhaustion, the "Unable to select next GitHub token from pool" error) and the PyPI downloads badge moved from `img.shields.io/pypi/dm` to pepy.tech (pypistats rate-limits shields.io upstream)

### Added

- `scripts/diag/` directory — documented home for investigation scripts that require a live authenticated profile; includes `memory_profile.py` (Chrome process-tree RSS profiler for issue #155), `capture_flow_traffic.py`, and `recaptcha_mint.py` (both moved from `scripts/` root via git mv, history preserved)

### Fixed

- Video status poll now raises `AuthExpiredError` (exit 3) immediately on HTTP 401 from `batchCheckAsyncVideoGenerationStatus` instead of silently timing out after 600 s with a bare `TimeoutError` (exit 1) — session expiry is now detected mid-workflow, not only at login time (issue #156)

## [0.15.0] — 2026-06-09

### Added

- **`gflow image` can reference locked CHARACTER entities** for character-consistent
  stills. New `--reference-entity <id>` (repeatable) + `--reference-entity-name` on
  `image t2i` / `i2i`, plus `--project <id>` to generate in an EXISTING project (where
  the entities live) instead of a throwaway scratch project. Entities attach through
  the editor's **Personagens picker** and ride the submit as `referenceEntities`
  (confirmed against the live API); `_build_batch_generate_images_body` serializes them
  so non-UI/headless transports get parity too. Entities count toward the per-model
  reference cap. `--project` / `--reference-entity` are single-prompt only; for a pure
  character reference use `t2i` (`i2i` still needs a `--ref`). See `docs/USAGE.md` →
  "Character-consistent images (entity references)".

### Fixed

- `--project` no longer overwrites an existing project's stored title/source in the
  local history DB (the recorder preserves the curated title for non-created projects).

### Security

- `--project` / `--reference-entity` ids are validated at the CLI boundary
  (`[A-Za-z0-9-]{1,128}`), closing an unvalidated `page.goto` navigation path
  (`project_editor_url` lacked the allowlist its sibling routes enforce) and a
  CSS-selector injection vector (`data-tile-id='fe_id_<id>'`). The request-body debug
  logger elides large reference-field values so Flow-built image bytes can't leak into
  logs.

### Internal

- Bump dev/CI `ruff` to `0.15.16` (both the `dev` extra and the `dependency-groups`
  pin; supersedes Dependabot PR #165, which updated only the soft `>=` extra and not
  the hard `==` group pin CI actually uses). `ruff check` / `format --check` clean.

## [0.14.0] — 2026-06-07

### Added

- **`gflow movie` — multi-scene, character-consistent video generation.** A TOML
  manifest (`gflow movie template` / `gflow movie run`) drives a sequence of clips
  that reuse a single Flow CHARACTER entity (reference-to-video) so the same face
  and voice carry across every scene. Generate-only by default; `--stitch`
  produces an ffmpeg preview concat; runs are crash-resumable via the sibling
  `<manifest>-state.json`; a versioned handoff manifest is written for downstream
  composition (e.g. Remotion). Deterministic prompt assembly (`composition.py`),
  scene = clip.
- **`docs/MOVIE.md`** — manifest format, the run lifecycle (the headed browser is
  required through generate → poll → download), the character-entity attach
  mechanism, and the best-effort consistency model.
- Dev utilities: `scripts/dev/make_project.py` (create a Flow project) and
  `scripts/dev/patch_character.py` (rename / set voice + personality on an entity).

### Fixed

- **R2V character reuse now actually rides the wire.** The entity is attached via
  the resource picker's **Personagens tab → right-click → "Incluir no comando"**
  (which stages `referenceEntities`; a left-click on the Tudo tile only stages the
  thumbnail as a `referenceImage`). The submit backstop now reads the response's
  real `media[].mediaMetadata.requestData.videoGenerationRequestData.videoGenerationEntityInputs`
  path instead of the request-shape `requests[].referenceEntities` — which had
  false-rejected every successful entity generation. `omni-flash` R2V verified to
  carry the entity.
- Cleared pre-existing type/test debt: `pyright src` is clean again (the missing
  `project_id` parameter was added to the `VideoCapableTransport` protocol and the
  `_enter_editor` type stub); regenerated `uv.lock` (jsonschema dev dependency).

## [0.13.0] — 2026-06-04

### Added

- **`gflow character rm` — delete a Character entity (#150).**
  `gflow character rm --project <id> (--id <entityId> | --name <name>) [-y/--yes] [--json]`
  deletes a Character via `POST flow:batchDeleteAssets` (Bearer; **FREE** — no
  reCAPTCHA, no credit). Resolves by id or exact name (ambiguous name exits
  **11**); prompts for confirmation unless `--yes`/`--json`.

- **In-project governance enforcement (advisory-first).** Made the AI-driven
  development flow followable and partly machine-enforced in-repo, modeled on the
  reference AI-DLC governance orchestrator's advisory-by-default behavior:
  ruff `T20` now bans raw `print()` in `src/`; an advisory branch-naming check and a
  non-blocking materiality + traceability classifier (`scripts/ci/check_materiality.py`
  + `governance-advisory.yml`) recommend `/gflow:predict` + council review when
  sensitive paths (`auth/`, `api/transports/`, `api/client.py`, `_sapisidhash.py`,
  `data/`, `recaptcha`) are touched, without ever blocking a merge. A
  history-replay backtest (`scripts/dev/materiality_backtest.py`) calibrates the
  gate — it measured a 1.1% false-positive rate (vs. a 20-30% estimate) and
  raised fix-coverage from 61% to 74% by surfacing auth-token plumbing that lived
  outside `auth/`. The backtest is a first-class, repeatable artifact (`--json`
  mode, a monthly `governance-benchmark.yml` dashboard job) fully documented in
  [`docs/GOVERNANCE_BENCHMARK.md`](docs/GOVERNANCE_BENCHMARK.md); the gate itself is
  described in
  [`docs/AGENT_GUIDE.md` § Governance & Enforcement](docs/AGENT_GUIDE.md#governance--enforcement).

### Changed

- **`gflow video i2v` frame flags aligned with Flow UI terminology (#122).**
  `--initial-frame FILE` is the new canonical flag for the start image (matches
  Flow's "initial frame" label). `--end-frame FILE` replaces `--end-image` as the
  canonical end-frame flag. `--end-image` is kept as a **deprecated alias** (emits
  `DeprecationWarning`; will be removed in a future minor release). The positional
  `IMAGE` argument remains supported for back-compatibility.

### Fixed

- **Character editor 404 on non-English locales (#153).** `gflow character`'s
  editor URL interpolated the locale verbatim, so the genuine default BCP-47
  `en-US` produced `/fx/en-US/…`, which 404s the Flow character editor (only the
  short primary subtag is a valid `/fx/<seg>/` segment). `character_editor_url`
  now normalizes a BCP-47 tag to its lower-cased short segment (`en-US → en`,
  `pt-BR → pt`), so the tool stays language-agnostic with the real default
  locale.

## [0.12.0] — 2026-06-03

### Fixed

- **Create-project generation failing when Flow opens the Agent _chat panel_.**
  A follow-up to the earlier Agent-pill fix: Flow now also surfaces Agent mode as
  a docked chat side-panel ("Untitled session") on some project opens, and while
  it is up the in-composer Agent pill is absent from the DOM — so the pill-only
  recovery could not find anything to click and generation still failed with
  "mode-switch dropdown trigger not found". `_exit_agent_mode` now handles both
  Agent shapes in one pass: it dismisses the chat panel (locale-stable, aria-free
  structural close anchor) which reveals the pill, then turns the pill off,
  looping until the media panel re-mounts. Keyed on the outcome (`crop_*` is
  back), so it covers pill-only, panel-only, and panel-then-pill without assuming
  which control is present.

### Added

- **`gflow character` command group — reusable Flow Character entities (#145).**
  Mint a project-scoped **Character** (a named subject with reference images, an
  optional voice, and an optional personality) so the same subject appears
  consistently across generations:
  - `gflow character create --project <pid> --name <name> --face-prompt "…"
    [--body-prompt "…"] [--voice <Name>] [--personality "…"]
    [--model nano2|nanopro]` — two-step generation: a **face** reference (slot 0),
    then a self-contained **front/side/back triptych body** (slot 1) seeded by the
    generated face. gflow injects its own triptych instruction, so one body
    generation yields all three angles. Characters have **no aspect-ratio
    control** and exactly two models — `nano2` (Nano Banana 2, default) and
    `nanopro` (Nano Banana Pro). Generated images are **downloaded** to local (or
    cloud) storage; the signed `fifeUrl` is used only at download and never
    persisted.
  - `gflow character list --project <pid>` — list every Character in a project.
  - `gflow character show --project <pid> (--id <entityId> | --name <name>)` —
    show one Character; an ambiguous `--name` exits 11.
  - `gflow character voices` — list the 29-name Gemini voice catalog
    (name / description / sample-url); `--voice` is validated case-insensitively.

  The credited generation rides Flow's own page JS in the character editor
  (Option B, UI passive-capture — a self-assembled direct POST is reCAPTCHA-403
  walled); the structural calls (createEntity, workflow/entity PATCH,
  projectInitialData) are credit-free REST. Creation runs as a
  **persist-before-spend, crash-recoverable saga**: the `entityId` and each
  completed slot are recorded before/as credits are spent, so a crashed run
  resumes without orphaning a paid generation or double-charging. Live-verified
  end-to-end on 2026-06-02 (face + triptych body, both bound, downloaded, read
  back). See [docs/CHARACTER.md](docs/CHARACTER.md).

- **`gflow video chain` — last-frame I2V chaining.** Render a JSONL manifest of
  *links* into one continuous sequence: link 0 is a text-to-video generation,
  and every later link is an image-to-video generation **seeded by the extracted
  last frame of the previous clip**, giving visual continuity with no
  server-side stitching. Each link is a sequential paid Veo generation (**one
  credit per link**); a cost-confirmation gate (`-y`/`--yes` to skip),
  `--dry-run` plan preview, `--max-links` cap (exit 11), and
  `--resume-from <chain-id>` (skips already-paid links, no re-billing) make the
  spend explicit and recoverable. Per-link wire-route checking aborts loudly if
  Flow drops the seed frame and routes an i2v link to the text-only endpoint
  (issue #125), so a misroute can never be reported as a successful chain.
  Only the Veo 3.1 models (`veo-lite`/`veo-fast`/`veo-quality`/`veo-lite-lp`)
  are accepted; `omni-flash` is rejected. Chain links are recorded locally
  (SQLite migration `0005`) to drive `--resume-from`. The frame extractor uses
  PyAV via a new optional **`[chain]`** extra (`pip install 'gflow-cli[chain]'`)
  — no system ffmpeg required. Each link is saved as its own mp4; concatenating
  the clips into one file is a separate step — use `gflow scene` (auto-concat is
  deferred, see [KNOWN_ISSUES.md](KNOWN_ISSUES.md)).

- **`gflow scene` command group (Add Clip / Scenes).** Compose ordered,
  trimmable video clips into a Flow **Scene** over the credit-free aisandbox
  REST surface (no reCAPTCHA, no credits):
  - `gflow scene create --project <pid> <workflowId>[:<start>-<end>] [...]` —
    compose a scene from one or more existing clips (repeat an id to duplicate;
    optional per-clip trim in seconds).
  - `gflow scene show --scene <sid> --project <pid>` — read back a scene's clip
    order and trims.
  - `gflow scene create … --output extended.mp4` — render the composed scene
    into a single **extended video** via Flow's server-side concatenation
    (`runVideoFxConcatenation`) — credit-free, no reCAPTCHA, **no ffmpeg**. The
    combined MP4 is fetched inline and written locally (or to the configured
    cloud `storage_uri`). `--force` overwrites an existing output.

  Scene compositions are recorded locally (SQLite migration `0003`) — including
  each clip's media id + trims, and the rendered extended-video path (migration
  `0004`) — so a compose survives a later render failure and the output is
  discoverable for recovery. The append-to-existing-scene verb (`add-clip`) is
  deferred — see the project backlog.

## [0.11.0] — 2026-05-31

### Changed

- **`gflow video i2v` default model is now `veo-lite`** (was: inherit Flow's
  last-used model, which was typically `omni-flash`). The Veo 3.1 family is
  the only model line that supports i2v interpolation; `omni-flash` is now
  rejected for any i2v invocation (start-only or start+end) and has been
  removed from the i2v `--model` choices. Because `--duration 10` is
  omni-flash-only, the i2v `--duration` choices are now `[4|6|8]`. `omni-flash`
  (and `--duration 10`) remain valid for `gflow video t2v` and `gflow video r2v`.
  See issue #125.

### Fixed

- **`gflow video i2v` silently produced text-to-video output, ignoring the
  start/end frames (issue #125).** When the model was `omni-flash` (Flow's
  last-used default in most sessions), Flow's frontend dropped the bound
  start/end frame references at submit time and routed every call to
  `batchAsyncGenerateVideoText` with `image_inputs: null` — charging a credit
  for a pure text-to-video generation that had no visual relationship to the
  supplied frames. **Every i2v paid run on v0.10.0 before this fix produced
  T2V output regardless of the start/end frames.** Fix: `omni-flash` is
  dropped from the i2v `--model` choices and the i2v default is now `veo-lite`;
  a defense-in-depth transport guard raises `ModelModeIncompatibilityError`
  (exit code 17) for direct `FlowApiClient` callers that bypass the CLI.

- **`gflow video i2v` could still route to T2V even with a valid Veo model,
  because the model-picker option `Veo 3.1 - Lite` was never selected (issue
  #125, second path).** The picker selector was an exact-match
  (`:text-is('volume_upVeo 3.1 - Lite')`) that hardcoded a Material Symbols
  icon-ligature prefix; when it missed, `_select_video_model` warned and
  continued, leaving Flow on `omni-flash` → the frames were dropped to T2V.
  Fixes: (1) the selector is now a robust substring match
  (`:has-text('Veo 3.1 - Lite'):not(:has-text('[Lower Priority]'))`); (2) for
  i2v, a model-select miss is now FATAL — `_select_video_model(required=True)`
  retries the picker then raises `VideoModelSelectionError` (exit code 18)
  *before* any frame attach or submit, spending no credit; (3) a post-submit
  backstop raises `WireFormatError` if an i2v request is still observed routing
  to the T2V endpoint, so a "successful" T2V is never reported as i2v.

- **Create-project generation failing when Flow's "Agent" composer mode is active.**
  Flow's newer editor adds an Agent toggle next to the prompt box; when it is on,
  the media-generation panel (the `crop_*` settings trigger, Image/Video mode
  tablist, and count/model controls) is removed from the DOM, so the UI-automation
  transport raised "mode-switch dropdown trigger not found". `_switch_to_image_mode`
  and `_switch_to_video_mode` now call `_exit_agent_mode()` first, which re-mounts
  the panel by clicking the toggle off. Detection is locale-invariant and uses no
  UI text and no `aria-` attribute (`button:has(span.content)` plus the absence of
  the locale-stable `crop_*` trigger), so it works in every Flow UI language.

- **`gflow image t2i` / `i2i` model selection hardened for non-English Flow UIs
  (issue #94).** `IMAGE_MODEL_OPTION_SELECTORS` is now a selector *cascade*
  (consistent with every other selector group) instead of a single exact-match
  string, so `_select_image_model` no longer silently fails to select the
  requested model when Flow's menu markup shifts. The redundant `--lang=en-US`
  Chromium launch arg was removed — Flow's branded model names ("Nano Banana 2",
  "Nano Banana Pro", "Imagen 4") are not localised and `FLOW_URL`'s `?hl=en`
  already locks the SPA to English, so the override was a no-op.

## [0.10.0] — 2026-05-29

### Fixed

- **Image and video counts leaking across profiles in `gflow data list projects` (issue #113).**
  Fixed an issue where project media counts were combined if two different profiles
  happened to share the same `flow_project_id`. The counts in `_LIST_PROJECTS_SQL`
  are now strictly scoped to the active `profile_name`.

- **List queries fan-out when multiple operations claim an asset (issue #111).**
  Fixed a bug in `gflow data list images` and `gflow data list videos` where 
  assets would be duplicated or have non-deterministic prompts if multiple 
  operations (e.g., retries) claimed the same output asset. The SQL queries 
  now use a deterministic subquery grouping by `asset_id` to ensure exactly 
  one-to-one cardinality.

### Added

- **Google account identity persisted to every profile (`issue #92`).** Both
  auth strategies (`real_chrome`, `internal_chromium`) now write a
  `.gflow_account` file to the profile directory immediately after the session
  is verified, durably associating the signed-in email with the profile on disk.
  `ProfileMeta` gains a `google_account: str | None` field populated by
  `profile_store.list_profiles()` from that file. `gflow auth list` (table and
  `--json`) now includes a **Google account** column so every profile is
  immediately identifiable — no more opaque `default` entries.  The `--json`
  output gains the `google_account` key for programmatic callers.  Closes #92.

- **Auto-rename of the first-run `default` profile to email local-part.** When
  `gflow auth login` creates the first profile and no `--profile` flag was given,
  the profile is named `default` as a placeholder. After the session is verified
  and the email is known, `auth login` automatically renames `profile_default` to
  `profile_<email-local-part>` (e.g. `profile_ffroliva`) and updates
  `config.toml`'s `default_profile` pointer atomically. The local-part is
  sanitized to a filesystem-safe name (characters outside letters, digits, `-`,
  and `_` become `-`), so `flavio.oliva@gmail.com` → `profile_flavio-oliva` and
  `user+flow@gmail.com` → `profile_user-flow`. Existing `default`
  profiles that were created before this change continue to work; they gain the
  email column the next time `gflow auth login` is run against them.  Closes #92.

- **`profile_store.rename_profile(old_name, new_name)`** — reusable primitive that
  renames a profile directory and updates `config.toml` when the renamed profile
  was the default. Raises `FileNotFoundError` / `FileExistsError` on invalid input.

- **Zero-credit smoke test for profile account persistence
  (`tests/smoke/test_profile_account_smoke.py`).** Three smoke tests that verify
  the full observable chain — account file present + readable, `list_profiles()`
  surfaces `google_account`, `gflow auth list --json` includes the field — against
  a real authenticated profile. No image or video generation; zero Flow credits.
  Backfills the `.gflow_account` file for profiles created before the fix so the
  tests work on existing sessions. Opt-in via
  `GFLOW_CLI_E2E_PROFILE=<name> pytest -m smoke tests/smoke/test_profile_account_smoke.py`.

- **Aggregated asset view in `gflow data list images/videos`.** By default,
  listing images or videos now returns one row per asset (Flow media ID),
  collapsing multiple local copies into a single entry with a `COPIES` count
  and the path of the latest copy. This prevents duplicate rows when
  re-downloading the same media to different directories. The `copy_count`
  is also exposed in JSONL output.
- **`--all-copies` flag on `gflow data list images/videos`.** Restores the
  previous behavior of showing every local file as a separate row.
- **`gflow data prune` command.** New maintenance utility to remove stale
  `local_files` database entries for local paths that no longer exist on
  disk. Only targets local files (ignores cloud-stored assets). Supports
  `--dry-run` to preview deletions and `--profile` to limit the scan.
- **External storage documentation for S3, MinIO, and Google Cloud Storage.**
  Adds `docs/EXTERNAL_STORAGE.md`, cross-links it from the README, docs index,
  configuration, data-layer, usage, security, and user-guide docs, and clarifies
  that `GFLOW_CLI_STORAGE_URI` is a cloud-only output mode rather than
  local-plus-cloud dual-write.
- **`--json` flag on `gflow video t2v`, `gflow video i2v`, and `gflow video r2v`.**
  Emits the `VideoResult` (status / command / media_id / generation_status /
  succeeded / local_path / failure_reasons / error_message) plus the request
  echo (model / mode / aspect / duration / count / seed) as a single JSON
  object on stdout. A failed generation still emits its JSON payload and
  then exits 1. The data-layer recorder
  (`record_started_video` / `record_completed_video`) fires regardless of
  `--json` so audit history is independent of the output channel. E2e
  coverage for `--json` shape across image / video / auth / models lives at
  `tests/e2e/test_json_output_e2e.py`.
- **`--json` flag on `gflow image t2i` and `gflow image i2i`.** Emits the
  complete `GeneratedImage` result (every field — `media_name`,
  `workflow_id`, `seed`, `prompt`, `model_name_type`, `aspect_ratio`,
  `dimensions`, `fife_url`, `is_signed_url`) plus the on-disk `local_path`
  as a single JSON object on stdout. A worker keys `images[0].seed` for
  refine-regen seed continuity. Single-prompt only (`--json` rejects
  multi-prompt batch with a Click usage error); progress chatter is
  suppressed so stdout is pure JSON. `ref_count` surfaces only on i2i.
- **`gflow models` catalog command.** New top-level command that enumerates
  the image and video model catalog as a Rich table (default) or as a single
  JSON object (`--json`). Per-model: `name`, CLI aliases (filtered to what
  the generation command's `--model` Choice actually accepts), `ref_cap`,
  `default` (image), `max_duration` (video). Built from the
  `Model` / `VideoModel` enums + their alias maps so the catalog can never
  drift from what the generation commands accept. A UI populating its model
  picker from `gflow models --json` is guaranteed to pass any selected alias
  back to `--model`.
- **`gflow_cli.json_output` module + `--json` error path on
  `run_with_handlers`.** Pure builders for image/video result payloads and
  RFC 9457 problem-details errors (plus a `retryable` flag worker schedulers
  key their retry-vs-absorb decision off — WAF / rate-limit / network /
  timeout). When `as_json=True`, errors emit a parseable JSON payload on
  stdout with the same exit code as the Rich path; the observability event
  still fires.
- **`--json` flag on `gflow auth list`.** Emits the profile inventory as a JSON
  array (`name` / `is_default` / `cookies_present` / `profile_dir` /
  `last_used_at`) on stdout so a programmatic caller (e.g. a worker discovering
  authenticated profiles) can `json.loads(stdout)` instead of scraping the
  Rich table.
- **Per-model r2v reference-image cap rebuilt as a data table.** Replaces the
  prior pair of constants (`OMNI_REFERENCE_CAP=7`, `VEO_REFERENCE_CAP=3`) with
  a `VideoModel -> int` mapping consulted by
  `gflow_cli.api.video.reference_cap_for(model)`. New entries:
  `veo_3_1_lite_lower_priority=3` (was implicitly covered by the veo branch),
  and `veo_3_1_quality=0` — Veo 3.1 Quality does NOT support
  Ingredients/References to Video at all per Google Flow's official support
  page; passing it to r2v raises a clear `does not support R2V
  (reference-to-video)` error rather than letting the request fail at the
  wire. CLI guard added on `gflow video r2v` (`click.UsageError`, exit 2)
  mirroring the i2i pattern so over-cap and quality+r2v fail before any
  profile/network work. E2e tripwire at
  `tests/e2e/test_video_r2v_ref_cap_e2e.py` asserts Flow actually consumes
  all `cap` refs at the at-cap boundary.
- **Per-model i2i reference-image cap.** Flow silently keeps only the first N
  reference images when an i2i request attaches more than the model accepts,
  so a caller could believe every ref was used. `gflow_cli.api.image.reference_cap_for(model)`
  exposes the live-observed per-model cap: NARWHAL (Nano Banana 2) and GEM_PIX_2
  (Nano Pro) accept 10, IMAGEN_3_5 (Imagen 4) accepts 3. Enforced as a domain
  invariant in `GenerateImageRequest.__post_init__` and at the CLI boundary in
  `gflow image i2i` (clean `click.UsageError` / exit 2 before any
  profile/network work). Mirrors the existing video r2v cap pattern. E2e
  tripwire at `tests/e2e/test_image_i2i_ref_cap_e2e.py` asserts Flow actually
  consumes all `cap` refs (one `reference_attached` event per ref) so a future
  silent truncation on the Flow side fails the test.
- **Layered e2e test strategy with cost sub-markers.** The single `e2e` marker is
  now augmented by cost sub-markers (`e2e_auth`, `e2e_image`, `e2e_video`,
  `e2e_batch`, `e2e_data`, `smoke`) so callers can run only the tier they can
  afford (zero-credit auth checks, single-credit image smoke, etc.). See
  `docs/E2E_TESTING.md` for the full reference.
- `tests/e2e/conftest.py` — shared `e2e_profile_dir`, `e2e_nosession_profile`,
  and `e2e_env` fixtures replace duplicated inline helpers in individual test files.
- `tests/api/transports/test_transport_timeout.py` — extracted from e2e as a
  pure-mock integration test; also fixes `Path("/dev/null")` → `Path(os.devnull)`
  for Windows portability.
- `tests/test_marker_registry.py` — invariant checks that every e2e test carries a
  cost sub-marker, and self-tests for the auto-marker conftest hook.
- `docs/E2E_TESTING.md` — comprehensive e2e strategy and layer reference document.

### Fixed

- Structlog logs are now routed to stderr (via
  `PrintLoggerFactory(file=sys.stderr)`) instead of stdout. Previously every
  CLI event leaked onto stdout, which broke the `--json` contract for
  programmatic callers — `json.loads(stdout)` failed because the JSON
  payload was preceded by event-log lines. Logs are diagnostics; stdout is
  data. No-op for human users (terminals still show logs the same way).

### Changed

- `gflow data media` now labels cloud-backed asset records as `cloud_uri_N`
  while keeping local assets under the existing `local_path_N` labels.
- `gflow_cli.api.video` no longer exposes the standalone `OMNI_REFERENCE_CAP` /
  `VEO_REFERENCE_CAP` constants. Callers that need a per-model R2V cap should
  use `reference_cap_for(model)` (which returns `0` for `VEO_3_1_QUALITY` —
  R2V is unsupported there). `MAX_REFERENCE_IMAGES` (= 7) is unchanged and
  still the absolute ceiling used when the model is unknown.
- `GFLOW_CLI_E2E_RUN_VIDEO` default flipped from `"1"` to `"0"`. The Veo step in
  `test_data_layer_e2e.py` is now **opt-in**: set `GFLOW_CLI_E2E_RUN_VIDEO=1` to
  include it. This prevents accidental Veo credit burns on unattended CI runs.
- `pytest` `addopts` now excludes `smoke` in addition to `e2e` and `live`:
  `not e2e and not live and not smoke`. Bare `pytest` never launches a live
  browser session.
- Auto-marker conftest hook uses `item.path.parts` instead of a slash-delimited
  string substring, fixing Windows backslash path compatibility.

## [0.9.1] — 2026-05-27

> **Locale and catalog patch release.** Hardens the headed-browser UI
> automation path for localized Flow profiles, fixes I2V start/end-frame
> attachment on non-English Chrome/Flow sessions, and repairs first-run catalog
> edge cases found after v0.9.0.

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

- Bare `pytest` no longer collects live/e2e tests by default. The project-wide
  pytest `addopts` now excludes `e2e` and `live` unless callers explicitly pass
  a different `-m` expression, and `tests/smoke/test_real_flow.py` is marked
  with both markers so marker-filtered local and CI runs cannot accidentally
  launch a real Flow browser session.
- Browser-manager PID tests no longer call the real `os.kill` while pretending
  to be on POSIX from a Windows runner. The POSIX liveness branches are now
  tested with mocked `os.kill`, avoiding hard interpreter/session exits during
  local test runs.

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
  Flow project.
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

[Unreleased]: https://github.com/ffroliva/gflow-cli/compare/v0.49.0...HEAD
[0.49.0]: https://github.com/ffroliva/gflow-cli/compare/v0.48.0...v0.49.0
[0.48.0]: https://github.com/ffroliva/gflow-cli/compare/v0.47.0...v0.48.0
[0.47.0]: https://github.com/ffroliva/gflow-cli/compare/v0.46.1...v0.47.0
[0.46.1]: https://github.com/ffroliva/gflow-cli/compare/v0.46.0...v0.46.1
[0.46.0]: https://github.com/ffroliva/gflow-cli/compare/v0.45.0...v0.46.0
[0.45.0]: https://github.com/ffroliva/gflow-cli/compare/v0.44.0...v0.45.0
[0.44.0]: https://github.com/ffroliva/gflow-cli/compare/v0.43.0...v0.44.0
[0.43.0]: https://github.com/ffroliva/gflow-cli/compare/v0.42.0...v0.43.0
[0.42.0]: https://github.com/ffroliva/gflow-cli/compare/v0.41.0...v0.42.0
[0.41.0]: https://github.com/ffroliva/gflow-cli/compare/v0.40.0...v0.41.0
[0.40.0]: https://github.com/ffroliva/gflow-cli/compare/v0.39.0...v0.40.0
[0.39.0]: https://github.com/ffroliva/gflow-cli/compare/v0.38.1...v0.39.0
[0.38.1]: https://github.com/ffroliva/gflow-cli/compare/v0.38.0...v0.38.1
[0.38.0]: https://github.com/ffroliva/gflow-cli/compare/v0.37.0...v0.38.0
[0.37.0]: https://github.com/ffroliva/gflow-cli/compare/v0.36.0...v0.37.0
[0.36.0]: https://github.com/ffroliva/gflow-cli/compare/v0.35.0...v0.36.0
[0.35.0]: https://github.com/ffroliva/gflow-cli/compare/v0.34.0...v0.35.0
[0.34.0]: https://github.com/ffroliva/gflow-cli/compare/v0.33.0...v0.34.0
[0.33.0]: https://github.com/ffroliva/gflow-cli/compare/v0.32.1...v0.33.0
[0.32.1]: https://github.com/ffroliva/gflow-cli/compare/v0.32.0...v0.32.1
[0.32.0]: https://github.com/ffroliva/gflow-cli/compare/v0.31.0...v0.32.0
[0.31.0]: https://github.com/ffroliva/gflow-cli/compare/v0.30.0...v0.31.0
[0.30.0]: https://github.com/ffroliva/gflow-cli/compare/v0.29.0...v0.30.0
[0.29.0]: https://github.com/ffroliva/gflow-cli/compare/v0.28.0...v0.29.0
[0.28.0]: https://github.com/ffroliva/gflow-cli/compare/v0.27.1...v0.28.0
[0.27.1]: https://github.com/ffroliva/gflow-cli/compare/v0.27.0...v0.27.1
[0.27.0]: https://github.com/ffroliva/gflow-cli/compare/v0.26.0...v0.27.0
[0.26.0]: https://github.com/ffroliva/gflow-cli/compare/v0.25.0...v0.26.0
[0.25.0]: https://github.com/ffroliva/gflow-cli/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/ffroliva/gflow-cli/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/ffroliva/gflow-cli/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/ffroliva/gflow-cli/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/ffroliva/gflow-cli/compare/v0.20.1...v0.21.0
[0.20.1]: https://github.com/ffroliva/gflow-cli/compare/v0.20.0...v0.20.1
[0.20.0]: https://github.com/ffroliva/gflow-cli/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/ffroliva/gflow-cli/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/ffroliva/gflow-cli/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/ffroliva/gflow-cli/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/ffroliva/gflow-cli/compare/v0.15.1...v0.16.0
[0.15.1]: https://github.com/ffroliva/gflow-cli/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/ffroliva/gflow-cli/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/ffroliva/gflow-cli/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/ffroliva/gflow-cli/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/ffroliva/gflow-cli/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/ffroliva/gflow-cli/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/ffroliva/gflow-cli/compare/v0.9.1...v0.10.0
[0.9.1]: https://github.com/ffroliva/gflow-cli/compare/v0.9.0...v0.9.1
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
