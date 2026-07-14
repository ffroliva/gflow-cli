# Project Status

> Where gflow-cli is in its lifecycle, by release. Updated on every signed tag.

## Current release

**v0.35.0 — alpha.** **Multimodal reverse-engineering + Storyboard tool + Dynamic Token Budgeting (#305-follow-up).** Adds `gflow tools run storyboard` to generate sequential visual prompts from single ideas, and integrates `gflow tools run reverse-engineer` with `claude-video`'s `watch.py` script for frame extraction and multimodal deconstruction of video/URL references using Gemini. Token budgets now scale dynamically with character limits. Verification: [LIVE_VERIFICATION_v0.35.0](LIVE_VERIFICATION_v0.35.0.md) (proven live storyboard expansion + frame extraction).

**Develop (unreleased, post-v0.35.0):** *(empty — develop is the staging branch for the next release).*

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
