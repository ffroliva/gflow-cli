# Project Status

> Where gflow-cli is in its lifecycle, by release. Updated on every signed tag.

## Current release

**v0.47.0 — alpha.** **MCP SDK 2.0.0 migration + dual-era protocol + entity provenance (#402, #407, #408).** Bounds `mcp` dependency to `>=2.0.0,<3`, migrates server to `MCPServer`, supports both 2026-07-28 and legacy protocol eras via SDK, defaults `gflow serve` to Streamable HTTP at `/mcp`, adds `resolve-drift` CI job, and records `entity_ids` / `entity_names` in `operations.metadata_json` on generation operations. See [LIVE_VERIFICATION_v0.47.0.md](LIVE_VERIFICATION_v0.47.0.md).

**Develop (unreleased, post-v0.47.0):** *(empty — develop is the staging branch for the next release).*

<details><summary>v0.46.0 — prompt tools on any OpenAI-compatible endpoint (#387)</summary>

**v0.46.0 — alpha.** **BREAKING — the prompt tools drive any OpenAI-compatible endpoint (#387).** `--tool creative-director` / `reverse-engineer` / `storyboard` now speak OpenAI Chat Completions instead of being hardwired to Google's native Gemini API, so OpenAI, gateways/proxies (OpenRouter, LiteLLM), local runtimes (Ollama, LM Studio), and Google's own compatibility endpoint all work. New config: `GFLOW_CLI_LLM_BASE_URL`, `GFLOW_CLI_LLM_API_KEY` (optional — omitted when unset so keyless local gateways work), `GFLOW_CLI_LLM_MODEL`. The removed `GFLOW_CLI_GEMINI_API_KEY` / `GFLOW_CLI_GEMINI_MODEL` trigger a loud one-time notice, never a silent no-op. The user-supplied endpoint is treated as a trust boundary: redirects declined, `https` (or loopback `http`) enforced, error bodies redacted. `reverse-engineer` degrades to the original input instead of expanding a file path as a prompt. See [LIVE_VERIFICATION_v0.46.0.md](LIVE_VERIFICATION_v0.46.0.md).

</details>

<details><summary>v0.45.0 — reference + character binding fixes (#393/#395)</summary>

**v0.45.0 — alpha.** **Reference + character binding fixes (#393/#395).** `gflow image i2i --ref <UUID>` now attaches the catalog's recorded file when Flow's per-project media picker cannot reach the tile, instead of failing the run — the fail-loud contract (never generate without a requested reference) is unchanged and pinned by a live e2e. `gflow character create` binds portraits to the character again: overlay dismissal was pressing Escape on Flow's own composer (`[role='dialog']`/`[role='alert']` matched the app itself), and the character route could bounce to the project page, sending the prompt to the **project** composer — both produced generations with no `entityContext`, which Flow filed as plain project images. Also: a Flow web-app crash on the character route is now the typed retryable `FlowAppError` (exit 31), character binding failures say what actually happened, and `--format-prompt` (#383) is live-verified. 2810 tests pass. See [LIVE_VERIFICATION_v0.45.0.md](LIVE_VERIFICATION_v0.45.0.md).

</details>

<details><summary>v0.44.0 — dual-side project naming &amp; management (#381)</summary>

**v0.44.0 — alpha.** **Dual-side project naming & management (#381).** Feature set includes `gflow project` subcommand family (`list`, `show`, `rename`, `create`), `--project-name` / `--project-title` options across `gflow image` (`t2i`, `i2i`) and `gflow video` (`t2v`, `i2v`, `r2v`) generation commands, prompt slugging for scratch projects, dual-side title sync updating Google Flow's tRPC server/UI and local SQLite catalog in lockstep, HTTP 429 adaptive backoff / `RateLimitError` recovery (#384), character body prompt composer fixes (#378), and a repo-local Codex plugin (`$gflow:*` skills). Verified via full test suite and live Playwright Chromium E2E transport test. See [LIVE_VERIFICATION_v0.44.0.md](LIVE_VERIFICATION_v0.44.0.md).

</details>


<details><summary>v0.42.0 — content-safety classification + Antigravity coding agent (#359/#360/#361)</summary>

**v0.42.0 — alpha.** **Content-safety classification + Antigravity migration (#359/#360/#361).** Content-safety `400` responses from Flow are now classified as `ContentPolicyError` (exit 5) instead of the misleading `WireFormatError`, so callers can branch deterministically on a policy rejection. Corrected a false `flow_operation_id` invariant (`veo-lite` can emit a `remote_started` checkpoint with `operation_id` unset; `media_id` is the canonical handle used by polling, download, and lookup). Replaced the retired Gemini CLI with Antigravity (`agy`) as the supported Google coding agent across skills and docs — Antigravity auto-discovers `AGENTS.md`, so the dedicated `GEMINI.md` hub is removed and `agy` is the pinned `high`-tier `llm-council` external reviewer. See [LIVE_VERIFICATION_v0.42.0.md](LIVE_VERIFICATION_v0.42.0.md).

</details>

<details><summary>v0.41.0 — production-readiness hardening (#357)</summary>

**v0.41.0 — alpha.** **Production-readiness hardening** ([#357]): queue safety (versioned payloads, atomic claims, checkpointed execution phases), cross-process profile lease (`ProfileLockedError` exit 11), cancellation-safe browser teardown, driver honesty (typed `SupportsSendPrompt` injection, frozen `TransportSetup`), mention-index fail-closed (`MentionIndexUnavailableError` exit 29), and external-CDP lifecycle removal. Removed nonfunctional manifest-driven video batch command (never worked end-to-end; loop `gflow video t2v`/`i2v` from the shell instead). Also: `/gflow:live-verify` skill for per-feature live-verification enforcement. 2513 tests pass; live-verified against real Flow (stale-session fail-fast, free image gen, paid veo-lite T2V). See [LIVE_VERIFICATION_v0.40.0-production-readiness.md](LIVE_VERIFICATION_v0.40.0-production-readiness.md).

</details>

<details><summary>v0.40.0 — prompt @-mention resolution for asset tagging (#344)</summary>

**v0.40.0 — alpha.** **Prompt `@`-mention resolution for asset tagging (#344).** `@Name` in a t2i/i2i/video prompt resolves to a staged, taggable character entity via `services/mentions.py`'s `resolve_and_apply`, shared by the `image`/`video` CLI paths, the async worker, and MCP tools. Media-asset (non-character) `@`-mentions also work, but on the **image path only** — video-path media mentions are Phase 3. A bare character with no reference images is rejected early with a clear error instead of failing deep in the UI attach. De-tagged prompts are persisted to the catalog. See [REFERENCE_STRATEGIES](REFERENCE_STRATEGIES.md) for `@`-mention vs `--reference-entity` vs `--ref`. Verification: [LIVE_VERIFICATION_v0.40.0](LIVE_VERIFICATION_v0.40.0.md) (`gflow character create` + `@Zoro` t2i passed live end-to-end against unmodified `develop`, ~2 Imagen credits; also records a same-cycle investigation dead end where a stale local WIP branch was mistaken for `develop`'s real state).

</details>

<details><summary>v0.39.0 — failed-generation persistence + gflow data list errors</summary>

**v0.39.0 — alpha.** **Failed generations are now persisted to the local catalog (#341).** Every paid-generation path (`video t2v/i2v/r2v`, `video chain`, `image t2i/i2i`, multi-prompt t2i, `gflow run`, `movie run`, and the async worker) now records a terminal `status="failed"` operation row — with a stable `error_type` derived from the exception's RFC 9457 `problem_type` and a redacted `error_detail` — before the error propagates, so WAF-403 block onset, duration, and recovery windows are measurable instead of reconstructed from memory. The new `gflow data list errors` subcommand browses failed operations newest-first, and videos whose poll returns `succeeded=false` are now recorded as `failed` (previously mis-recorded as `succeeded`). Bounded retention + export deferred to #345. Verification: [LIVE_VERIFICATION_v0.39.0](LIVE_VERIFICATION_v0.39.0.md) (a real `image t2i` HTTP-400 wire failure produced the first `failed` row in the production catalog, $0).

</details>

<details><summary>v0.38.1 — agentic-pin recovery (opt-in reload after a real toggle-off)</summary>

**v0.38.1 — alpha.** **Agentic-pin recovery (#338).** When a real, unforced Agent-toggle click lands but the classic media panel never mounts in place (the 2026-07-17 both-accounts pin), `ensure_media_mode` can now reload the page once — opt-in and sanctioned only from the pre-bind classic path — to re-roll the server's per-load cohort and mount the persisted `isAgentModeToggled=false` preference; a composer-render race that made the toggle unreachable was also fixed. Verification: [LIVE_VERIFICATION_v0.38.1](LIVE_VERIFICATION_v0.38.1.md) (classic recovered on denon82 after ~2h of active server pin).

</details>

<details><summary>v0.38.0 — robust agentic↔classic mode control + i2i ref dedup</summary>

**v0.38.0 — alpha.** **Robust agentic↔classic mode control + i2i reference dedup (#332, #314).** `--ui-mode` is now driven by a state-aware mode controller reading Flow's Agent toggle `aria-pressed` state (locale-invariant), ending spurious "forced agentic — not recoverable" aborts caused by an icon heuristic that matched both modes; `--ui-mode classic` reliably reaches the classic editor. Repeated local `--ref` images in `gflow image i2i` are now attached by selecting the already-uploaded library tile via exact-filename picker search (with a virtualised-grid scroll fallback, #335) instead of re-uploading duplicates. Also ships the PR-triage autopilot implementation (#238/#333, host deployment staged separately) and a behavior-preserving cognitive-complexity refactor (#331). Verification: [LIVE_VERIFICATION_v0.38.0](LIVE_VERIFICATION_v0.38.0.md) (agentic→classic recovery observed in a real run; dedup contract proven with an upload-then-dedup run pair; veo-fast t2v successful).

</details>

<details><summary>v0.37.0 — viewport 1920×1080 + agentic count enforcement</summary>

**v0.37.0 — alpha.** **Viewport 1920×1080 + agentic count enforcement + FIPS-safe SAPISIDHASH (#313, #315, #329).** UI-automation and auth-login viewports harmonized to 1920×1080 to blend with the most common desktop resolution; the agentic cohort's requested image count (`-n`) is reliably enforced via the Agent settings panel; the protocol-mandated SHA-1 in SAPISIDHASH is marked `usedforsecurity=False` so it works under FIPS-mode Python. Also bumps `mcp` to 1.28.1 (CVE-2026-59950). Verification: [LIVE_VERIFICATION_v0.37.0](LIVE_VERIFICATION_v0.37.0.md).

</details>

<details><summary>v0.36.0 — diagnostic tooling + reference-entity-smuggling fix</summary>

**v0.36.0 — alpha.** **Diagnostic tooling (`GFLOW_CLI_HAR_PATH` + `GFLOW_CLI_DEBUG_TRACEBACK`) + reference-entity-smuggling fix (#312/#316).** Two opt-in, env-var-only debug knobs: `GFLOW_CLI_HAR_PATH` captures full Playwright network traffic to a HAR file (0600-hardened on POSIX); `GFLOW_CLI_DEBUG_TRACEBACK` prints the real exception + traceback for unhandled errors (console and `--json`) instead of the generic placeholder, while structured telemetry stays hashed unconditionally either way. Also fixes a poisoned character entity leaking `referenceEntities` into unrelated `image i2i` calls in the same project workspace. Also ships the `llm-council` skill (`/gflow:llm-council`), adding external CLI reviewers (`codex`/`gemini`, opt-in `agy`) alongside the internal council for high-stakes reviews. Verification: [LIVE_VERIFICATION_v0.36.0](LIVE_VERIFICATION_v0.36.0.md) (live HAR capture against real Flow traffic; typed-error scoping confirmed live; unhandled path covered by 6 new tests, 30 total across both files).

</details>

<details><summary>v0.35.0 — multimodal reverse-engineering + storyboard tool</summary>

**v0.35.0 — alpha.** **Multimodal reverse-engineering + Storyboard tool + Dynamic Token Budgeting (#305-follow-up).** Adds `gflow tools run storyboard` to generate sequential visual prompts from single ideas, and integrates `gflow tools run reverse-engineer` with `claude-video`'s `watch.py` script for frame extraction and multimodal deconstruction of video/URL references using Gemini. Token budgets now scale dynamically with character limits. Verification: [LIVE_VERIFICATION_v0.35.0](LIVE_VERIFICATION_v0.35.0.md) (proven live storyboard expansion + frame extraction).

</details>

<details><summary>v0.34.0 — bidirectional UI cohort switching</summary>

**v0.34.0 — alpha.** **Bidirectional UI cohort switching (#299).** Introduces `--ui-mode` / `GFLOW_CLI_UI_MODE` to force classic or agentic Flow UI cohort layouts on the fly, with verification and an exit-28 fail-fast when a required layout cannot be reached. Verification: [LIVE_VERIFICATION_v0.34.0](LIVE_VERIFICATION_v0.34.0.md).

</details>

<details><summary>v0.33.0 — anti-bot jitter and video i2v project name overrides</summary>

**v0.33.0 — alpha.** **Anti-bot jitter and video i2v project name overrides (#241, #287).** Configurable anti-bot jitter range via `--jitter` / `GFLOW_CLI_JITTER_RANGE`, lower default batch jitter (0.5–1.5 s), and `--project-name` overrides for resolving in-project assets on localized or virtualized project dropdowns. Verification: [LIVE_VERIFICATION_v0.33.0](LIVE_VERIFICATION_v0.33.0.md).

</details>

<details><summary>v0.32.1 — browser teardown hardening</summary>

**v0.32.1 — alpha.** **Browser teardown hardening and profile lock translation (#293, #283).** Fixed Chrome process leaks on aborted context teardowns, translated launch failures to `ProfileLockedError` (exit 11), and fixed picker grid off-by-one scroll bounds. Verification: [LIVE_VERIFICATION_v0.32.1](LIVE_VERIFICATION_v0.32.1.md).

</details>

<details><summary>v0.32.0 — in-project asset i2v frame selection</summary>

**v0.32.0 — alpha.** **In-project asset i2v frame selection by UUID (#287, #288).** Select existing assets for video initial/end frames by UUID in place without re-uploading, and add fail-fast for duration settings control presence. Verification: [LIVE_VERIFICATION_v0.32.0](LIVE_VERIFICATION_v0.32.0.md).

</details>

<details><summary>v0.31.0 — wrong-media attribution defenses</summary>

**v0.31.0 — alpha.** **Wrong-media attribution defenses and multi-ref picker scrolling (#281, #282).** Added pre-download verification guards against ambiguous agentic-cohort image downloads (`MediaAttributionError` exit 26), and added viewport-scrolling fallback to resolve multiple sequential `--ref` selections in the virtualized picker grid. Verification: [LIVE_VERIFICATION_v0.31.0](LIVE_VERIFICATION_v0.31.0.md).

</details>

<details><summary>v0.30.0 — agentic-cohort image path support</summary>

**v0.30.0 — alpha.** **Agentic-cohort image path support and MCP video parameters (#258).** Supported native 768x1376 still generations in the agentic cohort, added character-creation integrity guards, and mapped model/duration/count video parameters on the MCP server. Verification: [LIVE_VERIFICATION_v0.30.0](LIVE_VERIFICATION_v0.30.0.md).

</details>

<details><summary>v0.29.0 — persistent gflow instructions CRUD</summary>

<details><summary>v0.28.0 — agent instructions (-i) steer agentic generation</summary>

**v0.28.0 — alpha.** **Agent instructions (`-i` / `--instruction`) now actually steer agentic
image generation (PR #263).** Instruction cards sync to the project's Agent brief via
`PATCH …/agentInfo` and the agent folds every enabled card into generation. Root causes fixed:
conversational (not imperative) composer directive + the `project_brief.enabled` master switch.
Verification: [LIVE_VERIFICATION_v0.28.0](LIVE_VERIFICATION_v0.28.0.md) (crayon e2e GREEN).

</details>

<details><summary>v0.27.1 — v0.27.0 follow-up fixes + documentation sync</summary>

**v0.27.1 — alpha.** **v0.27.0 release follow-up fixes and documentation sync (#239).** Patch release wiring package version dynamically to `build_handoff()` and `FastMCP` server, escaping brackets in Rich console planning output, updating MCP agent guide, and adding `gflow movie` usage documentation. Verification: [LIVE_VERIFICATION_v0.27.1](LIVE_VERIFICATION_v0.27.1.md) (credit-free baseline verification).

</details>

<details><summary>v0.27.0 — Global [style] block with named variants + prompt-aware resume for gflow movie</summary>

**v0.27.0 — alpha.** **Global `[style]` block with named variants + prompt-aware resume
for `gflow movie` (#239).** A `movie.toml` can now express a visual style system once —
`prefix`/`suffix` on `[style]` plus `[style.variants.*]` sub-tables — and select it
per-scene via `style_variant` / `style_suffix` (deterministic composition, `none`
reserved as the opt-out keyword). The handoff manifest records `style_applied`
(variant/prefix/suffix/scene_suffix) per clip. Resume is now prompt-aware: completed
scenes persist a `style_hash`; a scene whose composed prompt changed is regenerated
instead of silently skipped, and dry-run marks it `re-run (style changed)`. Carries
forward v0.26.0 (i2i select-in-place by UUID). Verification:
[LIVE_VERIFICATION_v0.27.0](LIVE_VERIFICATION_v0.27.0.md) (credit-free CLI ledger).

</details>

<details><summary>v0.26.0 — image i2i references a generated image by UUID (select in place)</summary>

**v0.26.0 — alpha.** **Reference a generated image in `image i2i` by its Flow UUID.**
A `reference_images` entry that is a media UUID is attached by **selecting the
already-existing asset in Flow's reference picker** (located by UUID in the thumbnail
URL, surfaced by display-name search when hidden) — no duplicate upload; local upload
remains the fallback. Generated images also record their Flow `display_name` (credited
@C1ph3r404). Verification: [LIVE_VERIFICATION_v0.26.0](LIVE_VERIFICATION_v0.26.0.md)
(live e2e GREEN).

</details>

<details><summary>v0.25.0 — remote-UUID i2v + silent-failure guards</summary>

**v0.25.0 — alpha.** **`video i2v` from a generated image's UUID proven live (#237)** —
the picker-search attach was reworked to a local-upload path, producing a real 8s
interpolation from a catalogued UUID. Home-`.env` config matrix (#240) verified live.
Two silent failures made loud: video-as-image download rejection and rejected-upload
fail-fast. Verification: [LIVE_VERIFICATION_v0.25.0](LIVE_VERIFICATION_v0.25.0.md).

</details>

<details><summary>v0.24.0 — `--project` parity across CLI + MCP</summary>

**v0.24.0 — alpha.** **`--project` parity across CLI + MCP.** The video commands
(`video t2v`/`i2v`/`r2v`) gain `--project <id>` to generate into an existing Flow project
instead of a scratch one (#233/#234), matching `image t2i`/`i2i`; and the MCP
`gflow_generate_image` / `gflow_generate_video` tools gain a matching `project` parameter
(#235), so agent callers get the same capability. Both surface an already-wired worker
capability (`payload["project_id"]`) and validate the id identically. Carries forward
v0.23.0 (MCP generation live + macOS 401 fix). Verification:
[LIVE_VERIFICATION_v0.24.0](LIVE_VERIFICATION_v0.24.0.md).

</details>

<details><summary>v0.23.0 — MCP generation live + macOS 401 fixed</summary>

**v0.23.0 — alpha.** **MCP generation goes live + macOS 401 fixed.** The MCP server's
`gflow_generate_image` / `gflow_generate_video` tools — previously non-functional stubs —
are now wired end-to-end to the FlowWorker queue (background worker owns download +
history recording), the `tools` prompt-expansion parameter is actually applied, and i2v/r2v
require their frame/reference inputs at the tool boundary. The long-standing macOS
generation `401` (#222) is resolved (#230, @gunalak): Flow cookies are read from the full
jar by domain instead of a path-`/` filter that dropped the `/fx`-scoped session token, and
the headed context is seeded from a pre-launch snapshot when macOS can't decrypt the store.
Carries forward v0.22.0 (Tools framework) + v0.21.0 (MCP server). Verification:
[LIVE_VERIFICATION_v0.23.0](LIVE_VERIFICATION_v0.23.0.md) (MCP wiring proven live; #222
reporter-verified e2e on macOS).

</details>

<details><summary>v0.22.0 — Tools framework ("Creative Director")</summary>

**v0.22.0 — alpha.** **Tools framework ("Creative Director").** A TOML-defined prompt-tool system: `creative-director` rewrites a terse prompt into a vivid one via Google's five-component formula (public Gemini API, never-fatal), with 15 category-gated domain styles and deterministic banned-keyword stripping. Invoke it via the new `gflow tools list/show/run` group or the uniform `-t`/`--tool` option on every generation command (`image t2i`/`i2i`/`batch`, `video t2v`/`i2v`/`r2v`/`chain`), replacing the never-released `-e/--expand`. History records the original prompt, the submitted `expanded_prompt`, and `metadata_json.tool` provenance (redaction-honoring). **"My Tools"**: user-authored TOMLs in `<GFLOW_CLI_HOME>/tools/*.toml` load automatically. MCP parity via `gflow_list_tools` + a `tools` array param; the legacy `expand_prompt` MCP prompt is deprecated. The Gemini expander gained an overall wall-clock budget. Carries forward v0.21.0 (MCP server over stdio + HTTP/SSE). Verification: [LIVE_VERIFICATION_v0.22.0](LIVE_VERIFICATION_v0.22.0.md) (CI/automated complete; live owner-run pending).

</details>

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
| `gflow character rm` — free character deletion (#150) | ✅ done (v0.13.0) |
| Align I2V CLI flags with Flow UI Labels (`--initial-frame`) (#122) | ✅ done (v0.13.0) |
| In-project governance (ruff T20, materiality Classifier) | ✅ done (v0.13.0) |
| `gflow movie` — multi-scene, character-consistent video from a TOML manifest (entity reuse, resumable, handoff manifest) | ✅ done (v0.14.0) |
| `gflow image t2i/i2i` — reference locked CHARACTER entities (`--reference-entity`) + `--project` for character-consistent stills | ✅ done (v0.15.0) |
| `gflow character` — reusable Flow Character entities (`create`/`list`/`show`/`voices`), persist-before-spend saga (#145) | ✅ done (v0.12.0) |
| `gflow scene` — Add Clip / Scenes compose + credit-free server-side extended video (`runVideoFxConcatenation`) | ✅ done (v0.12.0) |
| `gflow video chain` — last-frame I2V chaining from a JSONL manifest (`--dry-run`/`--max-links`/`--resume-from`) | ✅ done (v0.12.0) |
| Create-project generation works under Flow's Agent docked chat panel | ✅ done (v0.12.0) |
| Video status poll raises `AuthExpiredError` (exit 3) on mid-workflow 401 (#156) + Docker `/dev/shm` hardening | ✅ done (v0.15.1) |
| Locale-free resource-picker include selectors — entity attach works on every account language (#170) | ✅ done (v0.16.0) |
| `gflow image upscale <mediaId> --scale 2k\|4k` — credit-free download-menu upscale, 4K Ultra-gated (#171) | ✅ done (v0.16.0) |
| Cookie-store session verification fast path (`verify_flow_profile`, PR #168) + Playwright fallback | ✅ done (v0.17.0) |
| Entity-attach exit-7 remediation hint + `entity_attach_context` drift telemetry (#174 interim) | ✅ done (v0.17.0) |
| Agentic-UI exit-23 `UiSelectorDriftError` + `out_dir` wiring (#183) | ✅ done (v0.18.0) |
| Patchright opt-in browser engine (`GFLOW_CLI_BROWSER_ENGINE=patchright`) | ✅ done (v0.19.0) |
| Aspect-ratio overrides under Agentic & Classic cohorts + `GFLOW_CLI_PREFER_CLASSIC` (#193) | ✅ done (v0.20.0 / v0.20.1) |
| MCP server (`gflow mcp run` stdio + `gflow serve` HTTP/SSE) + daemon/queue scaffolding | ✅ done (v0.21.0) |
| Tools framework: `gflow tools` group + `--tool` + `creative-director` + "My Tools" + MCP parity | ✅ done (v0.22.0) |
| MCP generation wired to FlowWorker (tool→queue→download→record) + `tools` applied + i2v/r2v boundary validation | ✅ done (v0.23.0) |
| macOS generation 401 fixed — `/fx` cookie-path read + headed-context seed (#222/#230) | ✅ done (v0.23.0) |
| `--project <id>` on `video t2v/i2v/r2v` + MCP `project` parameter (#233/#234/#235) | ✅ done (v0.24.0) |
| `video i2v` from a generated image's UUID (#237) + home-`.env` matrix (#240) + silent-failure guards | ✅ done (v0.25.0) |
| `image i2i` references a generated image by UUID — select in place, no duplicate upload + `display_name` capture | ✅ done (v0.26.0) |
| `movie.toml` `[style]` block with named variants + prompt-aware resume (`style_hash`) (#239) | ✅ done (v0.27.0) |
| Agent instructions (`-i`/`--instruction`) steer agentic generation — conversational directive + brief master switch (PR #263) | ✅ done (v0.28.0) |
| Persistent `gflow instructions` CRUD + `movie.toml` instructions brief-sync + `gflow_instructions_*` MCP tools + CI-enforced MCP↔CLI parity (#192) | ✅ done (v0.29.0) |
| Diagnostic tooling (`GFLOW_CLI_HAR_PATH` + `GFLOW_CLI_DEBUG_TRACEBACK`) + reference-entity-smuggling fix (#312/#316) | ✅ done (v0.36.0) |
| Viewports 1920×1080 + agentic count enforcement + FIPS-safe SAPISIDHASH (#313/#315/#329) | ✅ done (v0.37.0) |
| Robust `aria-pressed` agentic↔classic mode control (#332) + i2i ref dedup via picker filename search (#314) | ✅ done (v0.38.0) |
| Agentic-pin recovery: opt-in reload after a real Agent-toggle click (#338) | ✅ done (v0.38.1) |
| Failed generations persisted to the local catalog + `gflow data list errors` (#341) | ✅ done (v0.39.0) |
| Prompt `@`-mention resolution for asset tagging (#344) | ✅ done (v0.40.0) |
| Production-readiness hardening: queue safety, profile lease, cancellation-safe teardown (#357) | ✅ done (v0.41.0) |
| Content-safety `ContentPolicyError` classification + Antigravity coding agent (#359/#360/#361) | ✅ done (v0.42.0) |
| Private incident diagnostics (`GFLOW_CLI_INCIDENT_CAPTURE`) | ✅ done (v0.43.0) |
| Dual-side project naming & management (`gflow project` family, `--project-name`/`--project-title`) (#381) | ✅ done (v0.44.0) |
| `--ref` catalog-backed upload fallback + character-entity binding fixes (#393/#395) | ✅ done (v0.45.0) |
| Prompt tools on any OpenAI-compatible endpoint (`GFLOW_CLI_LLM_*`) (#387) | ✅ done (v0.46.0) |
| Classic count-setter: digit-keyed tab selection + typed drift error (#404) | ✅ done (v0.46.1) |
| Manifest-driven video batch runner on `ui_automation` | ❌ removed — never worked end-to-end, shipped as a nonfunctional stub; see v0.41.0 changelog. For multi-clip video, loop `gflow video t2v`/`i2v` from the shell; `gflow image batch` remains supported for images. |
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
