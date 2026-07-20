# gflow-cli — Implementation Plan

> **Status:** Living document. Updated as phases complete.
> **Owner:** [@ffroliva](https://github.com/ffroliva)
> **Last revised:** 2026-06-03 (v0.12.0 release — `gflow character` reusable Flow Character entities (#145), `gflow scene` Add Clip + credit-free server-side extended video, `gflow video chain` last-frame I2V chaining, create-project fix under Flow's Agent chat panel)

This plan turns the v0.1 scaffold into a production-grade CLI for Google AI Ultra/Pro subscribers who want to spend their Flow credits via batch automation. The plan is opinionated, treating this repo as a portfolio-grade benchmark.

---

## 1. Goals

### Functional (MVP — v0.2.0a1)

| # | Goal | Phase |
|---|---|---|
| F1 | Authenticate once via browser, persist session | ✅ Phase 1 (shipped) |
| F2 | Generate **a single video from text** (T2V) | **Phase 2** |
| F3 | Generate **a single video from image + text** (I2V) | **Phase 2** |
| F4 | Generate **a batch of videos** from a TSV manifest | **Phase 2** |
| F5 | Download all outputs to a configurable directory | **Phase 2** |
| F6 | Per-account profiles (`--profile`) for multi-account use | ✅ Phase 1 (shipped) |

### Functional (post-MVP)

| # | Goal | Phase |
|---|---|---|
| F7 | Generate images (T2I/I2I via Imagen + Nano Banana) | ✅ done (v0.3.0a1) |
| F8 | Batch concurrency within one profile (per-worker Page pool) | ✅ done (v0.4.0a2) |
| F9 | Cross-account scheduling (round-robin across profiles) | Backlog (post-v0.5) |
| F10 | Switch to official Veo SDK as `GFLOW_CLI_PROVIDER=official` | Phase 5+ |

### Non-functional (every phase)

| # | Goal |
|---|---|
| N1 | Maintainable — clear boundaries, small files, no god modules |
| N2 | Testable — every behaviour has an automated check (unit + integration) |
| N3 | Observable — what failed, where, why, in one structured log line |
| N4 | Configurable — env vars > flags > sane defaults |
| N5 | Vitrine-grade — code a senior engineer would put on their CV |
| N6 | Cross-platform — Windows, macOS, Linux working uniformly |

### Explicit non-goals

- ❌ A GUI. CLI only.
- ❌ Hosting / multi-tenancy. Single user, local CLI.
- ❌ Re-implementing Google's auth. Playwright cookie jar is good enough.
- ❌ Re-selling Flow. See [DISCLAIMER](DISCLAIMER.md).

---

## 2. Architecture (steady state)

Documented in detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Summary:

```
┌──────────────────────────────────────────────────────────────┐
│  interfaces/cli/   ← Click commands (gflow auth/video/...)    │
└──────────────────────┬───────────────────────────────────────┘
                       │ instantiates + calls
┌──────────────────────▼───────────────────────────────────────┐
│  api/              ← FlowApiClient (Playwright + REST)        │
│                      + DTOs + URL constants + reCAPTCHA mint  │
└──────────────────────┬───────────────────────────────────────┘
                       │ HTTP via page.request
                       ▼
        aisandbox-pa.googleapis.com  +  labs.google/fx/api/trpc
```

The original PLAN included a full DDD/CQRS/Clean refactor. **Deferred** — for a single-user CLI, the layered shape becomes theatre. We keep the package boundaries (`api/`, `cli/`, `config.py`, `paths.py`) but skip the bus/handler/port indirection until there's a second `Provider` (v0.5+).

Current package layout (post-Phase 4):

```
src/gflow_cli/
├── __init__.py
├── __main__.py
├── auth/               ← v0.6.0a2: strategy pattern (real Chrome + internal Chromium)
│   ├── __init__.py     ← login(), AuthStatus, get_status(), list_profiles()
│   ├── base.py         ← AuthStrategy Protocol
│   ├── factory.py      ← AuthStrategyFactory (auto/chrome/internal routing)
│   ├── internal_chromium.py  ← bundled Chromium (legacy fallback)
│   └── real_chrome.py  ← system Chrome with G12 stealth flags
├── cli.py              ← Click app, top-level + auth subgroup
├── cli_image.py        ← gflow image upload/t2i/i2i (Click subgroup)
├── cli_video.py        ← gflow video t2v/i2v/batch (Click subgroup)
├── _cli_helpers.py     ← Phase 4 — shared profile/handler helpers
├── config.py           ← pydantic-settings + legacy-env-var shim
├── errors.py           ← Phase 4 — RFC 9457 hierarchy + EXIT_CODE_MAP
├── observability.py    ← Phase 4 — structlog bootstrap + event emitters
├── paths.py            ← XDG-aware default paths
├── profile_store.py    ← profile inventory + default-profile config.toml
├── data/               ← Phase 6 — local SQLite provenance layer
│   ├── __init__.py
│   ├── store.py        ← DataStore (connection + migration runner)
│   ├── repository.py   ← read queries (get_by_media_id, list_by_profile, …)
│   ├── recorder.py     ← OperationRecorder (write path + redact_metadata)
│   └── migrations/
│       └── 0001_initial.sql
└── api/
    ├── __init__.py
    ├── _retry.py       ← Phase 4 — tenacity AsyncRetrying + Retry-After cap
    ├── client.py       ← FlowApiClient (Playwright persistent context + REST)
    ├── dto.py          ← ProjectInfo, AssetInfo, VideoStatus, VideoOperation, ...
    ├── image.py        ← image request/response builders
    ├── recaptcha.py    ← reCAPTCHA Enterprise token mint via page.evaluate
    ├── routes.py       ← URL constants
    └── video.py        ← GenerateVideoRequest + Aspect/Mode/Tier enums
```

---

## 3. Configuration

Documented in [docs/CONFIGURATION.md](docs/CONFIGURATION.md) and [.env.template](.env.template). Variables:

`GFLOW_CLI_HOME`, `GFLOW_CLI_OUTPUT_DIR`, `GFLOW_CLI_PROFILE`, `GFLOW_CLI_PROVIDER`, `GFLOW_CLI_GEMINI_API_KEY`, `GFLOW_CLI_TIMEOUT_SECONDS`, `GFLOW_CLI_LOG_LEVEL`, `GFLOW_CLI_LOG_FORMAT`, `GFLOW_CLI_CONCURRENCY`, `GFLOW_CLI_DB_PATH`, `GFLOW_CLI_HISTORY_PROMPTS`.

Default paths via `platformdirs`:

```text
$GFLOW_CLI_HOME/                   ← user_data_dir/gflow-cli
├── profile_<name>/               ← Chromium persistent contexts
└── config.toml                   ← default_profile = "..."

$GFLOW_CLI_OUTPUT_DIR/             ← user_downloads_dir/gflow-cli
├── videos/<YYYY-MM-DD>/<job_id>.mp4
└── images/<YYYY-MM-DD>/<job_id>_<i>.png  (Phase 3)
```

---

## 4. Phase status

### Phase 1 — Foundation ✅ MOSTLY DONE

Shipped:
- `pydantic-settings` config layer with full env-var resolution
- XDG-aware paths via `platformdirs`
- `auth login/status/list/use/logout` + bare `gflow auth` UX
- Profile inventory (`profile_store`) with `config.toml` default persistence
- 79 tests passing, CI green
- Documentation tree (`docs/INDEX/AUTHENTICATION/CONFIGURATION/USAGE/SECURITY/ARCHITECTURE`)
- `CLAUDE.md` + `.claude/` for AI agents
- `KNOWN_ISSUES.md` + `DISCLAIMER.md` + `CONTRIBUTING.md`

Deferred (NOT blocking MVP):
- Full DDD/CQRS layered refactor — overkill for single-user CLI
- `structlog` wiring (deferred until logs are needed in anger)
- Per-folder `CLAUDE.md` files (only valuable when domains grow)

---

### Phase 2 — Video MVP (T2V + I2V + batch) — ✅ DONE (v0.2.0a1)

#### Scope

`gflow video t2v "<prompt>"` and `gflow video i2v <image> "<prompt>"` produce real Veo videos against a live Google AI Ultra/Pro account — end-to-end, no UI automation.

#### Captured routes (from samples/captured/, sanitised reference traffic)

| Route | Status | reCAPTCHA? |
|---|---|---|
| `POST .../trpc/project.createProject` | ✅ wired | No |
| `POST /v1/flow/uploadImage` | ✅ wired (I2V only) | No |
| `POST /v1/video:batchAsyncGenerateVideoText` | 🔧 to wire | **Yes** |
| `POST /v1/video:batchCheckAsyncVideoGenerationStatus` | ✅ wired | No |
| `getMediaUrlRedirect?name=...` (download) | ✅ wired | No |
| `PATCH /v1/flowWorkflows/{id}` (archive cleanup) | ✅ wired | No |

The `generate_video` route is the only one with a hard prerequisite that's not just "have cookies" — it needs a fresh **reCAPTCHA Enterprise token** per call.

#### Architecture decision: reCAPTCHA token minting

The reCAPTCHA token (~3000 chars, starts `0cAFcWe…`) is minted by the browser executing reCAPTCHA's JS challenge. Single-use, ~2 min expiry. Cannot be generated server-side.

**Approach:** the existing Playwright persistent context is already navigated to a Flow editor page (`EDITOR_BOOTSTRAP_URL`) on `__aenter__`. Before each `generate_video` call, we run `page.evaluate("grecaptcha.execute(siteKey, {action})")` to mint a fresh token, then include it in the request body.

**Site key + action discovery:** read from page source on first use, cache for the session:

```python
async def discover_recaptcha_site_key(page: Page) -> str:
    return await page.evaluate("""() => {
        const scripts = document.querySelectorAll('script[src*="recaptcha/enterprise.js"]');
        for (const s of scripts) {
            const m = s.src.match(/[?&]render=([^&]+)/);
            if (m) return m[1];
        }
        throw new Error("reCAPTCHA Enterprise script not found");
    }""")
```

**Action name:** the captured token has metadata that reveals the action — Flow uses something like `videoGeneration` or similar. We discover this from the bound JS handler or from network capture in the discovery script.

**Risk: headless detection.** Google's reCAPTCHA Enterprise can detect headless Chromium and refuse to mint tokens (returns a "challenge required" response that we can't solve programmatically). If this triggers:

| Fallback | What changes |
|---|---|
| **Headed mode by default for video gen** | Worse UX (window opens) but reliable. Add `GFLOW_CLI_HEADLESS=auto\|true\|false`; default `auto` = headless until first failure, then headed. |
| **Headed only for token mint, headless for everything else** | More complex but keeps the rest invisible. |
| **Defer to user reporting** | Ship with headless default + clear error message instructing the user to set `GFLOW_CLI_HEADLESS=false`. |

Default plan: **headless first**, instrument the failure with a remediation hint. If users report it failing, switch to headed-by-default.

#### Implementation sequence

Each step is a separate commit. Each one runs the four quality gates locally + verifies CI green before moving on.

**2.1 — reCAPTCHA token mint** (~1-2h)
- New file: `src/gflow_cli/api/recaptcha.py`
  - `discover_site_key(page) -> str`
  - `mint_token(page, site_key, action) -> str`
  - Cache site_key on first discovery
- Tests: `tests/api/test_recaptcha.py`
  - Mock `page.evaluate` calls
  - Verify error path when reCAPTCHA script not found
  - Verify cache behaviour
- Defer real "does the live API accept it" verification to step 2.4 (smoke).

**2.2 — `FlowApiClient.generate_video()` method** (~1h)
- Add method on FlowApiClient: `generate_video(project_id, prompt, *, start_asset=None, aspect="9:16", model_tier="fast", seed=None) -> VideoOperation`
- Encode model key: `veo_3_1_t2v_fast_portrait` (T2V) or `veo_3_1_i2v_fast_portrait` (I2V), parameterised by aspect + tier
- Body assembly using captured shape from `samples/captured/02_batchAsyncGenerateVideoText.json`
- Tests: body shape verification + reCAPTCHA token plumbing (mocked)

**2.3 — CLI commands** (~1h)
- New file: `src/gflow_cli/cli_video.py` (or extend `cli.py`)
  - `gflow video t2v "<prompt>" [--aspect 9:16|16:9|1:1] [--output PATH] [--profile NAME] [--async]`
  - `gflow video i2v <image> "<prompt>" [...same options + auto-uploads start frame]`
  - `gflow video batch <manifest.tsv> [--out-dir DIR] [--concurrency N]`
  - `gflow video status <job_id>` (poll a previously-async'd job)
- Manifest TSV format: `start_image\tprompt\tend_image?\taspect?\toutput_path?` (start_image empty → T2V)
- Default polling loop with progress output via Rich
- Tests: Click runner-based + handler logic with mocked FlowApiClient

**2.4 — Live smoke test** (~30m)
- New file: `scripts/smoke_e2e.py` — tiny script user runs once
- Sequence: create project → t2v "test cinematic motion" → poll → download → assert mp4 size
- Document in README: "Run after first `gflow auth login`"

**2.5 — Remove legacy `providers/` package** (~30m)
- Already superseded by `api/`. Delete the stub. Update tests + cli.py imports.

**2.6 — Update docs + CHANGELOG** (~30m)
- `docs/USAGE.md`: rewrite the Video commands section with real examples
- `KNOWN_ISSUES.md`: add a new entry about reCAPTCHA headless detection (only if 2.4 reveals it)
- `CHANGELOG.md`: collect all `[Unreleased]` entries into the v0.2.0a1 section

**2.7 — Tag `v0.2.0a1`** (~15m, automatable via `/release`)
- Bump version in `pyproject.toml`
- Final CHANGELOG migration
- `git tag v0.2.0a1 && git push origin v0.2.0a1`
- GitHub Release auto-created by workflow
- (PyPI Trusted Publishing not yet configured — that ships in v0.2.0)

#### Total effort estimate

~5-6 hours focused. Can be split across two sessions if reCAPTCHA discovery proves nasty.

#### Definition of done (Phase 2)

- [x] `gflow video t2v "..."` produces a real .mp4 against the user's Google AI Ultra/Pro account
- [x] `gflow video i2v <png> "..."` produces a .mp4 whose first frame matches the input PNG
- [x] `gflow video batch <tsv>` processes 3+ clips end-to-end
- [x] All four quality gates green (ruff / format / pyright / pytest)
- [x] Test coverage ≥ 80% on `src/gflow_cli/api/`
- [x] `samples/captured/` documents every wire format we depend on
- [x] `KNOWN_ISSUES.md` updated with anything surprising discovered during 2.4
- [x] Tagged `v0.2.0a1` on GitHub

---

### Phase 3 — Image MVP (T2I + I2I + upload) — ✅ DONE (v0.3.0a1)

#### Scope (shipped)

- `gflow image upload <path>` — upload PNG/JPEG/WebP/GIF, print asset UUID + dimensions.
- `gflow image t2i "<prompt>" [--model {nano2|nano-pro|image4}] [--aspect ...] [-n 1..4] [--seed N] [--out DIR]`
- `gflow image i2i "<prompt>" --ref PATH_OR_UUID [--ref ...] [...same as t2i]`

Three models behind aliases (`nano2` → `NARWHAL`, `nano-pro` → `GEM_PIX_2`, `image4` → `IMAGEN_3_5`); five aspect ratios (`9:16` / `16:9` / `1:1` / `4:3` / `3:4`); 1–4 images per call via N parallel POSTs sharing one `batchId`.

#### Captured routes

| Route | Status |
|---|---|
| `POST /v1/projects/{projectId}/flowMedia:batchGenerateImages` | ✅ wired |
| Direct download from signed `fifeUrl` (`*.googleusercontent.com` / `flow-content.google` allowlist) | ✅ wired |

Body envelope mirrors video (clientContext + mediaGenerationContext.batchId + useNewMedia + requests[]). `text/plain` content-type, fresh reCAPTCHA Enterprise token per call.

#### Definition of done (Phase 3) — all checked

- [x] `gflow image t2i "..."` produces a real .png against the user's Google AI Ultra/Pro account
- [x] `gflow image i2i ... --ref hero.png` produces a .png that visibly references the input
- [x] `gflow image upload hero.png` prints a reusable asset UUID
- [x] All four quality gates green (208 tests, 82% coverage, image.py at 100%)
- [x] `samples/captured/06_batchGenerateImages.json` + `07_batchGenerateImages_seeded.json` document the wire format
- [x] DEBUG body logs redact reCAPTCHA tokens; project_id allowlist closes a percent-encoded-slash bypass; download path enforces SSRF host allowlist
- [x] Tagged `v0.3.0a1` on GitHub at `ccce4d5`

---

### Phase 4 — Hardening — ✅ COMPLETE (v0.4.0a1 → v0.4.0a2, 2026-05-11)

All 9 tasks (T0 spike + T1–T8) shipped. See `CHANGELOG.md` `[0.4.0a1]` for
user-facing notes and `docs/superpowers/plans/2026-05-10-phase-4-hardening.md`
for the task-by-task ledger.

- [x] Per-worker Page pool inside shared `BrowserContext`; `GFLOW_CLI_CONCURRENCY` (1–16) drives in-flight degree.
- [x] `gflow video batch` fans out via `asyncio.gather` (was sequential pre-v0.4.0a1).
- [x] `tenacity.AsyncRetrying` retry — 3 attempts, exp-jittered backoff, `Retry-After` capped at 60 s, `reraise=True`, retry on 5xx / 429 / `playwright.Error` / `TimeoutError`, reCAPTCHA token re-minted **inside** the retry loop on the worker's own Page.
- [x] RFC 9457 Problem Details exception hierarchy (`GFlowError → FlowApiError → 5 typed leaves`) with per-class exit codes 3–7 (130 for SIGINT).
- [x] `WireFormatError` discovery payload (route_name, http_status, content_type, top_level_keys, body_prefix_redacted) for log-grep-driven error-taxonomy evolution.
- [x] `structlog` bootstrap — TTY auto-detect (text/JSON), `show_locals=False` mandatory on exception renderer, `correlation_id` + `cli_version` bound via `contextvars` at process boundary, `error_raised` + `error_unhandled` events.
- [x] 12 BDD scenarios (`pytest-bdd`) across auth/video/image with a `_forbid_live_playwright` autouse tripwire — mocked-only contract enforced.

#### Phase 4 — T0 Page-pool spike note (2026-05-10)

Page creation averaged **44.7–48.1 ms per page** across N=2/4/8/16 inside one persistent `BrowserContext` on Windows 11 / Playwright Chromium (headless); cookies confirmed shared at Context level ✓. The per-Page cost is well under the 200 ms/page hard-cap threshold, so the design choice (per-worker Page within a shared persistent Context, mirroring `browser-use`'s pattern) is industry-supported and safe.

| N  | avg_create_ms | total_ms | pages_open_after |
|----|---------------|----------|------------------|
|  2 |          44.7 |     44.7 |                2 |
|  4 |          45.4 |    136.3 |                4 |
|  8 |          46.2 |    323.6 |                8 |
| 16 |          48.1 |    721.2 |               16 |

`cookies_shared_between_pages = True` — confirmed by navigating Page 0 to `https://example.com`, opening a second Page, and observing identical `ctx.cookies()` output before/after. This validates that the per-account auth state (`storage_state.json`) persists once at the Context level and every pooled worker Page inherits it for free.

**Verdict.** ✅ Page pool feasible up to N=16 — `Settings.concurrency` cap unchanged (`le=16`). No T2 test adjustments needed.

---

### Video generation rework (UiAutomationTransport) — Phase 0 spike ✅ DONE (2026-05-19)

The video-generation feature has its own sub-phase plan (spike → Phase A → Phase B), tracked in `docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md`. Targets v0.6.0a5 (see KNOWN_ISSUES "REST API 401").

**Phase 0 — submit-mechanism spike — ✅ DONE.** `scripts/smoke_video_editor.py` drove live Flow and proved video generation can be UI-driven like `generate_images`: a T2V `batchAsyncGenerateVideoText` fired and returned HTTP 200. Full results in the spec §10.5; open-question resolutions in §10.2.

- ✅ Core finding confirmed — UI-drive video works.
- ✅ Q5 — video offers 9:16 / 16:9 only (no SQUARE).
- ✅ Mode switch is a 2-step dropdown; selectors locked to language-agnostic structural anchors (Radix `aria-controls` tokens, icon ligatures).
- ✅ Output count defaults to x2 — the transport must set count explicitly (verified count=1 → 1 video / 20 credits).
- ⚠️ Q7 — status poll `page.request.post` → 401; the spec §5.5 polling design must be reworked in Phase A (capture Flow's own status responses).
- ⏭️ Q1 / Q3 / Q6 — image attachment is an in-page catalog dialog; driving it, the start-only-I2V check, and the R2V slot cap are deferred to Phase B.

**Next:** Phase A (T2V transport), once §5.5 is revised for the Q7 401. Issue #24 (locale-agnostic selectors): Phase 1 (env override via `GFLOW_CLI_LOCALE`) landed via PR #51 on develop 2026-05-24 (post-v0.8.1, unreleased); live-verified end-to-end in pt-BR (1 credit, mp4 downloaded). Full removal of the `--lang=en-US` Chromium arg is gated on a selector-invariant capture across the remaining onboarding/new-project text selectors.

---

### Phase 7 — Protocol Extensions (v0.8.1, 2026-05-23)

- [x] **Issue #24: Locale-Agnostic Selectors — Phase 1 + Phase 2 + Phase 3 shipped.**
  - **Phase 1 (PR #51, 2026-05-24, post-v0.8.1):** added the `GFLOW_CLI_LOCALE` env override on Playwright's launch `locale=` parameter; live-verified `gflow video t2v` end-to-end under `pt-BR`.
  - **Phase 2 (PR #70, 2026-05-25, develop `c6e32aa`):** restructured `ONBOARDING_SELECTORS` into a two-tier cascade (3 strict ARIA/ID anchors + ~37 text entries spanning 14 locales). `_attach_frame` (I2V/R2V) was intended to flip to structural-first, but the shipped `FRAME_SLOTS_STRUCT` (`swap_horiz` icon container) matched **zero** elements on real Flow DOMs (wrong icon class + wrong slot tag). Discovered + corrected by PR #90 below. **T2V live-proof on de-DE: 70.9 s, 3.1 MB 1280×720 H.264 mp4.**
  - **Phase 3 (PR #90 / [#63](https://github.com/ffroliva/gflow-cli/issues/63), 2026-05-26, develop `9a0896a8`):** replaced `FRAME_SLOTS_STRUCT` with the locale-free pattern `div[type='button'][aria-haspopup='dialog']` (verified via DOM probe — matches exactly 2). Also fixed the End-frame OOB index after Start is attached. **I2V live-proof on de-DE: 124 s, mp4 with `ftyp` magic bytes, both `frame_attached` events fired (Start + End).**
  - **Phase 4 (Task #6, 2026-05-26, branch `claude/next-quick-win-gBPqS`, PR #93):** `NEW_PROJECT_SELECTORS` expanded to all 14 locales (icon-first tier + 14-locale text tier); English-only ARIA fallbacks (`[aria-label*='New project']`, `[aria-label*='Project']`) removed. `SUBMIT_BUTTON_SELECTORS` drops its English-only `button[aria-label*="Create"]` entry — `arrow_forward` icon selectors cover it in all locales. Both tuples are now fully locale-invariant. Note: issue #24 was closed 2026-05-24 after Phases 1–3; Phase 4 is cosmetic tail work. `--lang=en-US` retained only to stabilise `IMAGE_MODEL_OPTION_SELECTORS` (product names like "Nano Banana 2" may be localised). Removing it requires converting the image model picker to a structural anchor — tracked as issue #94 (Phase 5 below). **R2V live e2e on non-EN not yet exercised** — still open.
  - **Phase 5 (2026-05-30, branch `claude/issue-94-staleness-check-MnsEY`, PR #127):** `IMAGE_MODEL_OPTION_SELECTORS` and `VIDEO_MODEL_OPTION_SELECTORS` converted from `dict[Model, str]` to `dict[Model, tuple[str, ...]]` (cascade discipline; Tier 1 structural slots reserved for future DOM-probe-confirmed anchors). `--lang=en-US` removed from Chromium launch args — locale controlled by `locale=locale_env` Playwright kwarg (persists across all navigations including `/project/<uuid>` deep-links) + `FLOW_URL`'s `?hl=en` on initial load. Branded model names confirmed locale-stable. Four new `TestSelectorLocaleInvariance` tests + four `TestSelectImageModel` runtime tests. **R2V live e2e on non-EN still pending (owner gate — requires authenticated non-EN Chrome profile).**
- [ ] **Model Context Protocol (MCP) Server.** (Backlog) Expose core gflow-cli tools via MCP for language-agnostic agentic access. Planned in [PREDICT.md](file:///C:/development/github/gflow-cli/docs/superpowers/plans/2026-06-22-mcp-server/PREDICT.md) / [SCENARIO.md](file:///C:/development/github/gflow-cli/docs/superpowers/plans/2026-06-22-mcp-server/SCENARIO.md) / [PLAN.md](file:///C:/development/github/gflow-cli/docs/superpowers/plans/2026-06-22-mcp-server/PLAN.md).
- [x] **Tools framework + Prompt Expansion ("Creative Director") — shipped → develop (PRs #210 + #211, 2026-06-28).** Prompt expansion evolved from the never-released `-e`/`--expand` flag into a TOML-defined **Tools framework**: `creative-director` is the first built-in tool, invoked uniformly via `--tool/-t <name>[:k=v]` on every generation command (`image t2i`/`i2i`/`batch`, `video t2v`/`i2v`/`r2v`/`chain`) or standalone via `gflow tools list/show/run`. The tool rewrites a terse prompt via Gemini's 5-component formula (`src/gflow_cli/tools/`, stdlib-urllib expander, never-fatal: missing key / 429-backoff / network / empty-clean all fall back to the original), with 15 domain styles and deterministic banned-keyword stripping. `operations.expanded_prompt` records original + submitted expansion (withheld under `history_prompts=redacted`); `metadata_json.tool` records `{name, version, model, params, config_hash}` provenance (minimized in redacted mode). Reuses `GFLOW_CLI_GEMINI_API_KEY`; `GFLOW_CLI_GEMINI_MODEL` overrides. MCP parity: `gflow_list_tools` + a `tools` array param per AGENTS.md §61. Docs: [TOOLS.md](file:///C:/development/github/gflow-cli/docs/TOOLS.md) + [PROMPT_EXPANSION.md](file:///C:/development/github/gflow-cli/docs/PROMPT_EXPANSION.md). Memory `[[prompt-expansion-feature]]`.
  - **PR #210 (framework):** `tools/` package (spec/loader/registry/runtime/expander/banned/invocation), `creative-director.toml`, `gflow tools list/show/run`, `--tool` on t2i/t2v (single-prompt guard kept), MCP `gflow_list_tools` + `tools` param.
  - **PR #211 (broaden surface):** `original_prompt`/`tool` carried on the request DTOs (`GenerateImageRequest`/`GenerateVideoRequest`/`BatchPromptItem`/`ChainLinkSpec`); recorder reads `request.original_prompt` (kwarg retired); `metadata_json.tool` recording; `--tool` wired into i2i/batch/i2v/r2v/chain; single-prompt guard removed. Silent-misrecord hazard resolved by carrying `original_prompt` on the DTO rather than re-threading a kwarg through each recorder method.
  - **Expander robustness — DONE 2026-06-28 (PR #214).** Per-attempt timeout lowered 30s→20s and an overall `max_total_seconds` wall-clock budget (default 60s) added to `tools/expander.py` — retries stop once the budget is spent and each attempt's timeout is clamped to the remaining budget, so sustained 429s can no longer block ~120s before fallback. `tenacity` (a dep) was evaluated and **rejected**: it is built around re-raising after retries whereas the expander must never raise (fall back to the original), and the per-attempt structlog + total-budget logic is clearer as a hand-rolled loop with injectable `transport`/`sleep`/`clock` seams.
  - **`expand_prompt` reconcile — DONE 2026-06-28 (PR #215).** The legacy `mcp/prompts.py` `expand_prompt` MCP prompt is **deprecated** in favor of the `creative-director` tool (client-visible `[DEPRECATED]` marker; kept functional; removal slated for a future major). See [PROMPT_EXPANSION § Relationship to expand_prompt](file:///C:/development/github/gflow-cli/docs/PROMPT_EXPANSION.md).
  - **My Tools — DONE 2026-06-28 (PR #216).** User-authored TOMLs in `<GFLOW_CLI_HOME>/tools/*.toml` are loaded into the registry (override-a-builtin-with-warning; malformed fails loud).
  - **Backlog — deferred by design:** the S3 `Tool.apply(ctx)→outcome` dispatch framework. The research doc gates it on a *second, non-prompt* tool (e.g. Mockup) — building it now with a single prompt-only implementer is the documented "premature uniformity (guessed contract)" anti-pattern.
- [ ] **Integrated Filmmaking Studio, Flow Worker & MCP SSE Service.** (Backlog) The MCP→FlowWorker wiring shipped in v0.23.0 (PR #228). Remaining: a local FastAPI/React filmmaking studio dashboard + HTTP/SSE MCP. Planned in [gflow-studio-scaffold/PLAN.md](file:///C:/development/github/gflow-cli/docs/superpowers/plans/2026-06-24-gflow-studio-scaffold/PLAN.md) and [rest-api-layer/PLAN.md](file:///C:/development/github/gflow-cli/docs/superpowers/plans/2026-06-24-rest-api-layer/PLAN.md).
- [x] **Issue #92: Google account identity persistence shipped 2026-05-28 (PR #110).** After every `gflow auth login`, both auth strategies write the verified email to `$GFLOW_CLI_HOME/profile_<name>/.gflow_account`. `ProfileMeta.google_account` surfaces it; `gflow auth list` and `gflow auth list --json` display it. First-login auto-rename: when no `--profile` flag was given and the only profile is named `default`, it is renamed to the email local-part. `profile_store.rename_profile()` primitive added. Zero-credit smoke test (`tests/smoke/test_profile_account_smoke.py`) ships with backfill path for pre-existing profiles. LLM council (9-dim) run; all must-fix items applied before merge. See `KNOWN_ISSUES.md` § Resolved for full detail.
- [x] **`/gflow:pr-council-review` slash command shipped 2026-05-26 (PR #97).** Multi-dimensional LLM council for open PRs (4 baseline + 8 adaptive dimensions, mandatory memory-slug binding, draft-PR guard, YELLOW escape valve, live-verify gate). Validated on PR #93; self-audited by 3-agent meta-council surfacing 13 must-fix items applied before merge. See memory `[[llm-council-code-review-pr93]]`.
  - **Phase A (Backlog — Portability):** extract command body into `skills/pr-council-review/SKILL.md` so Antigravity / Codex / Cursor / Aider can consume it. Keep `.claude/commands/gflow/pr-council-review.md` as a thin wrapper. Memory: `[[pr-council-review-portability-backlog]]`.
  - **Phase B (Backlog — Token optimization):** add `scripts/dev/pr_council_prefetch.py` (single gh-call producing structured JSON for agents) and `scripts/dev/memory_filter.py` (relevant slug bodies only). Targets ~30-50% per-agent context cost reduction.
  - **Phase C (Backlog — Reusable meta-council):** codify the 3-dim audit (completeness / robustness / prompt-clarity) we used on this command as `/gflow:meta-council-audit <path>` for re-use on future skills/commands.
  - Sequencing: **A → C → B**.

---

### Video generation rework — Phase A (T2V transport) ✅ DONE (2026-05-19)

Retired the 401-dead HTTP video path (`FlowApiClient.generate_video` / `get_video_status`, `build_generate_body` / `model_key`, `VideoOperation` / `VideoStatus` DTOs in `api/dto.py`). Added pure `api/video.py` value objects + parsers and a `ui_automation_video.py` mixin (`VideoGenerationMixin`) delivering `generate_video` for T2V via the Flow editor UI, with status polling that captures Flow's own traffic. `gflow video` CLI commands are stubbed pending Phase B (I2V + R2V + CLI rewire).

Tasks shipped:
- T1–T5: groundwork — constants, routes, error mapping, smoke-test and test-suite cleanup of dead HTTP video path
- T6: `GenerateVideoRequest` value object (frozen dataclass with validation); `Mode.R2V` added
- T7: `VideoStatus` value object (pure domain, replaces DTO)
- T8: pure response parsers — `parse_video_status`, `media_name_from_generate_response`
- T9: `_attach_generate_response_listener` — captures Flow's own generate response via network interception
- T10: `_attach_status_response_listener` + `_poll_video_status` — status polling via captured traffic
- T11: video editor selectors + mode switching — `_probe_selector_cascade`, `_switch_to_video_mode`, `_wait_video_editor_ready`
- T12: output-count and aspect-ratio controls — `_set_output_count_one`, `_select_video_aspect`
- T13: `generate_video` orchestration + `VideoGenerationMixin` wired into `UiAutomationTransport`
- T14: docs update (this entry)

I2V and R2V explicitly deferred — the orchestration raises `NotImplementedError("Phase A supports T2V only…")` for non-T2V modes.

**Next:** Phase B — I2V + R2V + CLI rewire (`gflow video t2v/i2v` commands re-enabled).

---

### Phase 5 — Public alpha soak + first non-alpha release ✅ DONE (v0.7.0, 2026-05-20)

`v0.2.0a1` through `v0.6.0a6` shipped as alpha on PyPI under Trusted Publishing
during the soak window. **v0.7.0 is the first stable (non-`aN`) release**,
tagged 2026-05-20 with a signed annotated tag (CI gate from #30) and verified
end-to-end against the live Flow UI across four aspect ratios — see
[`docs/LIVE_VERIFICATION_v0.7.0.md`](docs/LIVE_VERIFICATION_v0.7.0.md).

Headline scope for v0.7.0 (downstream-worker ergonomics release):

- `FlowApiClient(out_dir=...)` debug-screenshot plumbing (#18)
- `health_check()` + `BrowserSessionClosedError` (#16, #18)
- Optional `project_id` on `generate_image*` (#16)
- `gflow_cli.exceptions` alias module (#16)
- `gflow auth login` Flow-session verification (#15) + Chromium-rejection
  guidance via `AuthBrowserRejectedError` exit 14 (#17)
- Overlay-dismiss helper for first-run profiles (#26)
- 1:1 aspect-ratio selector cascade (live-verification fallout)
- Signed-tag CI gate (#30)
- Listener instrumentation (`batch_response_seen`, `…_dropped_project_id_mismatch`)
- New evergreen docs: [`docs/DEBUGGING.md`](docs/DEBUGGING.md) and
  [`docs/LIVE_VERIFICATION_v0.7.0.md`](docs/LIVE_VERIFICATION_v0.7.0.md)

Optional follow-up still open: scaffold `OfficialVeoProvider` against
[`googleapis/python-genai`](https://github.com/googleapis/python-genai) behind
`GFLOW_CLI_PROVIDER=official`.

**External install verified:**
```bash
uvx --from "gflow-cli==0.7.0" gflow --version    # → gflow, version 0.7.0
```

---

### Phase B — Video CLI restoration on `UiAutomationTransport` — ✅ MOSTLY DONE

Phase A (T2V library transport) shipped with v0.7.0 via PR #23. PR #36 (2026-05-21) closed the T2V CLI gap; PR #48 (merged 2026-05-24) shipped I2V + R2V + model picker:

- [x] Restore `gflow video t2v` CLI — shipped via PR #36 (`gflow video t2v PROMPT [--aspect 9:16|16:9] [--profile] [--out-dir]`)
- [x] First-class video download mirroring the image side ([#29](https://github.com/ffroliva/gflow-cli/issues/29)) — shipped via PR #36 (`VideoResult`, `_download_video`, `FlowApiClient.download_video`)
- [x] Live-verify T2V portrait/landscape — 2026-05-21 on profile `ffroliva` (both `9:16` and `16:9`); evidence in [`docs/LIVE_VERIFICATION_video_download.md`](docs/LIVE_VERIFICATION_video_download.md)
- [x] **I2V (image-to-video)** on `UiAutomationTransport` — shipped via PR #48 (`gflow video i2v IMAGE PROMPT`)
- [x] **R2V (reference-to-video)** on `UiAutomationTransport` — shipped via PR #48 (`gflow video r2v PROMPT --ref IMAGE`)
- [x] **Model picker** (fast/quality tier selection) — shipped via PR #48
- [x] Parameterized live e2e under `tests/e2e/test_video_t2v_e2e.py` — shipped; assertion stale-fix in `8dce1a9` (issue #54 closed)
- [ ] Restore `gflow video batch` CLI (TSV-manifest fan-out — currently stubbed; awaiting manifest-driven runner design)
- [ ] Live e2e for I2V + R2V (only T2V exercised on non-EN locale to date — gate for dropping `--lang=en-US`)
- [ ] Address the first-attempt listener-miss flake observed during v0.7.0 live verification

---

### PR-Triage Autopilot — COMPLETED

Autonomous hourly code review helper running in a Docker sandbox. Design spec in [2026-07-04-pr-triage-autopilot-design.md](file:///C:/development/github/gflow-cli-pr-triage/docs/superpowers/specs/2026-07-04-pr-triage-autopilot-design.md).

- [x] Task 1: Stage 0 deterministic pre-filter (`pr_triage_gate.py`)
- [x] Task 2: §9 Autonomous mode in `pr-council-review` skill
- [x] Task 3: Ephemeral Docker sandbox image (`Dockerfile.triage`)
- [x] Task 4: Main host orchestration script (`pr_triage_autopilot.py`)
- [x] Task 5: Operations runbook (`PR_TRIAGE_AUTOPILOT-OPS.md`)

---

### Technical Debt & Refactoring — BACKLOG

Identified during v0.6.0a1 Council Review. To be addressed before or during Phase 6.

- **Unify Output Resolution**: `cli_run.py` uses `out/<UTC>` while `cli_image.py` uses `images/<YYYY-MM-DD>`. Align both to the date-partitioned daily strategy or a single shared helper.
- **Deduplicate JSON Validation**: `cli_run.py` duplicates model/aspect validation logic. Consolidate into `image_batch.py` helpers.
- **Generic Batch Orchestrator**: Refactor `run_image_batch` to accept a worker callback. This will allow the same sequential/fail-fast orchestration to support `video` batches in the future.
- **Playwright Thread Safety**: Investigate why `unittest.mock.patch` plus multi-threaded `asyncio` hangs in `tests/test_browser_manager.py`. The test is currently skipped.

---

### Issue #5 — Auth: detect Google "browser may not be secure" guard — BACKLOG

External report ([issue #5](https://github.com/ffroliva/gflow-cli/issues/5)): user on
Ubuntu 24.04 installed via `uv tool install gflow-cli`, ran `gflow auth login`,
hit Google's anti-automation guard ("This browser or app may not be secure")
because the bundled internal Chromium is flagged. The workaround
(`--browser chrome` or installing system Chrome so `--browser auto` picks it)
exists but is undiscoverable from the failure mode — the user just sees
Google's error page.

**Fix:**

- In `auth/internal_chromium.py`, after navigating to the Google sign-in URL,
  detect the rejection page (URL match `accounts.google.com/v3/signin/rejected`
  or page text matching `browser .* may not be secure`).
- On detection, raise a new `AuthBrowserBlockedError` (new Problem Details
  class `https://gflow-cli.dev/errors/auth-browser-blocked`, distinct from the
  existing `AuthExpiredError`) with remediation hint:
  *"Install Google Chrome and re-run with `--browser chrome` (or set
  `GFLOW_CLI_AUTH_BROWSER=chrome`). See docs/AUTHENTICATION.md."*
- Register the new class in `errors.py` exit-code map (reuse the auth
  exit-code class).

**Files:** `src/gflow_cli/auth/internal_chromium.py`, `src/gflow_cli/errors.py`,
`tests/auth/test_internal_chromium.py`.

**Tests (TDD):**

- Unit: mock a `Page` whose `goto` resolves to the rejection URL → assert
  `AuthBrowserBlockedError` raised, hint mentions `--browser chrome`.
- Unit: existing success path still passes (no false positives).

**Acceptance:** Ubuntu user repro from issue #5 surfaces the workaround in the
CLI error itself; docs lookup no longer required.

**Scope guardrails:** Do not change the default strategy auto-detection.
Do not add fingerprint-evasion code — Google may block it anyway and the
real-Chrome path already works.

---

### Issue #14 — Batch redesign: native count selector + --same-project — BACKLOG

[Issue #14](https://github.com/ffroliva/gflow-cli/issues/14) — two parts.

**Part 1 (bug fix):** `gflow image t2i -n N` should use Flow's native `x{N}`
UI count selector (visible in the t2i menu, max `x4`), submitting once and
returning N images from one project. Today (post-`1621748d`) the CLI fans out
N×(count=1) submissions across N projects, serialised by `_generate_lock` —
correct but wasteful.

- Refactor `generate_images_batch` in `api/client.py` to delegate to
  `generate_images(count=N)` when given a single prompt with `N>1`.
- Extend `UiAutomationTransport._configure_generation_settings` to click
  the `x{N}` count tab (`1x` / `x2` / `x3` / `x4`).
- CLI validation in `cli_image.py`: reject `-n` outside `[1, 4]` with a clear
  error citing Flow's UI cap.
- Investigate whether `_generate_lock` is still needed for the cross-profile
  concurrency case (`GFLOW_CLI_CONCURRENCY`); keep if so, remove if not.

**Part 2 (feature, deferred):** `--same-project` flag for multi-prompt
batches — stay in one project, submit prompts sequentially with 3–7s
random jitter, log-and-continue on per-item failure. **Blocked** until a
multi-prompt CLI command exists to attach the flag to (no `gflow image
batch <manifest>` today).

**Files:** `src/gflow_cli/api/client.py`,
`src/gflow_cli/api/transports/ui_automation.py`, `src/gflow_cli/cli_image.py`,
`tests/api/transports/test_ui_automation.py`,
`tests/api/test_client.py`.

**Tests (TDD):**

- Unit: `-n 4` single prompt → transport called once with `count=4`, opens
  editor once, downloads 4 images.
- Unit: `-n 5` → CLI exits with validation error pointing to the `1-4` range.
- Unit: count-tab selector mock asserts the correct `x{N}` element clicked.
- E2E (`@pytest.mark.live`, manual): 4 images appear in one project from one
  submission.

**Acceptance:** Part 1 ships in `v0.6.0a6` (or later) reducing 4-image batch
time by ~3-4× and consolidating into one project. Part 2 stays open until
the multi-prompt CLI lands.

**Risk:** This touches the file we just fixed in `1621748d` — must keep both
Bug A (lock for cross-profile) and Bug B (gallery navigation when restored)
working. Re-run E2E #1 and #2 from `tmp/` after refactor.

---

### Issue #15 — i2v upload 401: hybrid-transport auth mismatch — INVESTIGATION REQUIRED

[Issue #15](https://github.com/ffroliva/gflow-cli/issues/15) — `POST /v1/flow/uploadImage`
returns HTTP 401 even with a freshly verified session.
t2i (UI-driven) works on the same session; i2v's REST upload path does not.
Strongly suspect missing `SAPISIDHASH Authorization` header — the project
already has `src/gflow_cli/api/transports/experimental/sapisidhash.py`.

**Investigation gates (must complete before coding):**

1. Read `transports/experimental/sapisidhash.py` end-to-end. Does it
   implement `SAPISIDHASH = SHA-1(timestamp + " " + SAPISID + " " + origin)`
   per Google's convention? Is it wired up anywhere?
2. Read `FlowApiClient._post_json` in `api/client.py`. What headers does
   it attach today? Is `Authorization` set at all?
3. Capture a working browser session's request to `/v1/flow/uploadImage`
   via DevTools → Network → copy as cURL. Diff the production CLI's outgoing
   headers against the working browser's.

Only after these three gates produce a clear picture, write the fix plan.
Speculative coding here will burn a session.

**Likely fix:**

- Compute SAPISIDHASH from the persistent profile's `SAPISID` cookie + a
  Unix timestamp + the Flow origin.
- Attach `Authorization: SAPISIDHASH {ts}_{hash}` header in `_post_json`
  for routes that need it (likely all `aisandbox-pa.googleapis.com` POSTs,
  not just uploadImage — verify).
- Refresh the timestamp/hash per request (the timestamp is a freshness
  signal; SAPISID is long-lived).

**Secondary fix (independent of root cause):**

- Classify 401 from `/v1/flow/uploadImage` as `UploadAuthError` (or similar),
  distinct from `AuthExpiredError`, with a remediation hint that doesn't
  send the user into a `gflow auth login` loop — *the login is fine; the
  upload auth header is the problem*.

**Files:** `src/gflow_cli/api/client.py`, possibly promote
`api/transports/experimental/sapisidhash.py` into the production tree,
`src/gflow_cli/errors.py`, `tests/api/test_client.py`,
`tests/api/test_sapisidhash.py`.

**Tests (TDD):**

- Unit: `SAPISIDHASH` calculation produces the exact value for known inputs
  (test vector lifted from a captured working request).
- Unit: `_post_json` attaches `Authorization` header on routes that require it.
- E2E (`@pytest.mark.live`): `gflow video i2v` runs end-to-end on a fresh
  session without 401.

**Acceptance:** i2v completes to a downloaded `.mp4`; correlation IDs in
the structured log trace through `upload_image` → video generation → poll →
download with no error events.

**Out of scope:** rewriting i2v upload to use UI drag-and-drop. The
SAPISIDHASH fix, if proven, also benefits any future REST endpoint that
gates on the same header.

---

### Phase 6 — Local SQLite data layer — ✅ SHIPPED (PR #58 + #78 + #81, v0.9.0)

Records image, batch, T2V, I2V, and R2V provenance in a local SQLite catalog. Read-only `gflow data` subcommands exposed.

- `gflow_cli/data/` — `DataStore` + repository + `OperationRecorder` + `redact_metadata`
- Default DB path: `<GFLOW_CLI_HOME>/gflow.db`; override via `GFLOW_CLI_DB_PATH`
- Schema versioned via SHA-256-checksummed migrations (`0001_initial.sql`); newer-schema detection raises `DataStoreError` (exit 16) with a clear upgrade hint
- Privacy: `GFLOW_CLI_HISTORY_PROMPTS=redacted` stores only SHA-256 prompt hash; signed CDN URLs, reCAPTCHA tokens, and auth headers are stripped by `redact_metadata` before any DB write
- **Shipped CLI surface:**
  - `gflow data media <id> [--profile]` — profile, media ID, project ID, kind, and local file paths
  - `gflow data list projects|images|videos|profiles [--profile] [--limit N] [--offset N] [--json]`
- T2V/I2V/R2V/image flows through `FlowApiClient`, sharing the client boundary
- **PR #78 fixes:** DB path drift (#79) + videos NULL-duration crash (#80) on Windows
- **PR #81 fix:** keep test + example outputs out of the repo root

**Backlog follow-ons:**

- `gflow data import` / `gflow data repair` — back-fill older operations not recorded by this version
- `gflow data search` — full-text / metadata search across catalog
- `gflow history` alias for the data subcommand
- Cost/credit estimation per profile or per project

### CDP Attach Transport — BACKLOG (deferred)

**Background:** During Phase 5 E2E testing, `batchGenerateImages` returned 403 because `navigator.webdriver=true` in the Playwright-launched context allowed reCAPTCHA Enterprise to detect automation. An alternative approach was proposed: connect to an already-running, user-visible Chrome instance via CDP instead of launching a new context. Since the user's real Chrome is already logged in and looks like a human browser, reCAPTCHA would see a genuine session.

**What to investigate first:**
- Whether Chrome launched with `--remote-debugging-port=9222` is treated differently by Google's reCAPTCHA Enterprise (`navigator.webdriver` is `undefined` in real Chrome without Playwright injection, so the score may be higher than with the current stealth flags).
- Use `playwright.chromium.connect_over_cdp("http://localhost:9222")` to attach.
- Confirm that cookies are shared between the CDP-attached session and the existing browser tabs (they should be — same Chrome profile).

**Design constraints:**
- Must be a new, opt-in transport: `--transport cdp_attach` (or `GFLOW_CLI_TRANSPORT=cdp_attach`).
- Must NOT interfere with the default `ui_automation` or HTTP transports.
- The user is responsible for launching Chrome with `--remote-debugging-port`; the CLI only attaches.
- If the CDP port is unreachable, fail with a clear `TransportError` pointing to the setup instructions.

**Open question:** Does attaching via CDP set `navigator.webdriver=true` on the attached page? If yes, the same reCAPTCHA detection still applies and the approach has no advantage over the current stealth fix. **This must be verified before implementing anything.**

**Status:** NOT implemented. Parked until the stealth-flag fix (`--disable-blink-features=AutomationControlled` + init script) is confirmed insufficient, or until a contributor picks it up.

**2026-07-09 verification — the current stealth fix is CONFIRMED SUFFICIENT (so this stays parked).** A 20-generation baseline on a live authed profile through the default stealth stack produced a **0.0% WAF 403 rate** (19/20 success; the one miss was a UI-scrape timeout, not a WAF block). This met ADR-13's "must verify before implementing" gate and closed the [Camoufox adoption roadmap](superpowers/plans/2026-07-09-camoufox-adoption/PLAN.md) at Phase 2 — the Camoufox engine was NOT built. Evidence: [superpowers/spikes/2026-07-09-camoufox-waf-403.md](superpowers/spikes/2026-07-09-camoufox-waf-403.md). Re-run `scripts/spike_waf_camoufox.py` if a repeatable WAF-403 is later observed; a materially non-zero rate would reopen CDP-Attach / Camoufox.

**2026-07-19 — packaged CDP lifecycle removed.** The unused CDP attach/spawn lockfile lifecycle that previously shipped in `browser_manager.py` (`get_or_launch_browser` / `close_browser` and its port/lockfile/singleton-lock helpers) was deleted: zero production consumers, an unsafe no-lock attach-to-any-Chrome path, and no positive evidence outweighing the 2026-05-12 WAF rejection above. Chrome discovery/channel helpers (`is_chrome_available`, `channel_for_profile`, etc.) — used by the real auth/UI-automation path — were kept. This ADR's "parked" status is unchanged: CDP-attach as a distinct opt-in transport remains a backlog idea for a future contributor with a safe ownership model; see `.superpowers/sdd/cdp-decision.md` for the evidence.

---

### Phase 8 — Pluggable storage backend — BACKLOG

Today the CLI writes media to `$GFLOW_CLI_OUTPUT_DIR` on the local filesystem. Phase 8 makes the storage backend pluggable so generated assets can stream directly to S3 / GCS / Azure Blob without an intermediate local copy.

- New `gflow_cli.storage` module with a `StorageBackend` Protocol (write_bytes, exists, stat, list)
- Implementations: `LocalStorage` (today's behaviour, default), `S3Storage`, `GCSStorage`, `AzureBlobStorage`
- Configure via `GFLOW_CLI_STORAGE_BACKEND` env (`local|s3|gcs|azure`) + backend-specific creds
- New flag: `--storage-backend s3://bucket/prefix/` for per-call override
- Object naming convention: `{profile}/{command}/{YYYY-MM-DD}/{media_uuid}.png|mp4`
- Metadata sidecar: each object has a corresponding `.problem.json` (RFC 9457 Problem Details for any error during retrieval) and `.manifest.json` (prompt, model, aspect, seed) for full provenance
- Hooks into Phase 6: `operations` table records the storage URL alongside the local path
- Out of scope until Phase 8 ships: lifecycle policies, deduplication, presigned URL generation

---

### Unified Multi-Account Management — BACKLOG

**Background:** A key strength of the gflow architecture is its capacity to drive and coordinate across multiple Google Accounts in a unified way. This enables horizontal scale, credit pooling, and parallel batch generation bypassing individual account quotas.

**Requirements & Implications:**
- **Cross-Account Session Inventory:** A unified credential manager tracking persistent browser context directories (`profile_<email_local>`) and session lifespans.
- **Concurrent Warm Context Pool:** A background daemon scheduling across a warm browser context pool representing multiple active Google Accounts (e.g., executing parallel worker queues on different profiles).
- **Round-Robin Queue Dispatcher:** A dispatcher that distributes task batches dynamically across profiles based on quota availability or idle status.
- **Aggregated Provenance Mapping:** The local `gflow.db` data layer indexes projects, operations, and downloaded files back to the generating Google identity.
- **Studio UI Integration:** Dropdowns displaying connected profiles, quota/credit tracking, active account tags on workflow nodes, and single-click profile swapping.

---

## 5. Decision log (ADRs in miniature)

| # | Decision | Rationale |
|---|---|---|
| 1 | Hybrid Playwright + REST, not pure HTTP client | reCAPTCHA token mint requires a real browser context; same context piggybacks for cookies |
| 2 | DDD/CQRS layered refactor deferred indefinitely | YAGNI for a single-user CLI; revisit if `gflow serve` HTTP front-end ever lands |
| 3 | `pydantic-settings` over raw `python-dotenv` | Validated config, single source, fails fast |
| 4 | `platformdirs` for default paths | Same convention as `pip`, `uv`, `httpx` — no surprises |
| 5 | TSV manifests over JSON/YAML | Editable in any tool, scriptable, vim/awk-friendly |
| 6 | `text/plain` content-type for aisandbox-pa POSTs | Verified in samples — server 400s on `application/json` despite the body being JSON |
| 7 | Default video aspect 9:16 | Flow's primary use case is short-form vertical reels |
| 8 | Output dir under `Downloads/gflow-cli/` via platformdirs | OS-native, discoverable, easy to clean |
| 9 | No event sourcing, no message queue, no SaaS dependencies | YAGNI for a local CLI |
| 10 | Both `gflow` and `flow` binary names installed | `flow` is friendlier; `gflow` avoids conflicts with Facebook Flow / MS Power Automate |
| 11 | LF-only line endings via `.gitattributes` | Single repo source of truth; cross-platform contributors don't think about it |
| 12 | `Provider` indirection (legacy `providers/`) removed | Superseded by `api.FlowApiClient`. Re-introduce when we add `OfficialVeoProvider` (planned v0.5+) |
| 13 | CDP attach / stealth-engine alternatives deferred; **current stealth fix confirmed sufficient 2026-07-09** | Gate met: a 20-gen live baseline through the default stealth stack showed a **0.0% WAF 403 rate**, so the "confirm insufficient before implementing" bar is unmet — CDP-Attach and the Camoufox engine stay parked (Camoufox roadmap closed at Phase 2). Evidence: [superpowers/spikes/2026-07-09-camoufox-waf-403.md](superpowers/spikes/2026-07-09-camoufox-waf-403.md). |
| 14 | HTTP status path (`get_video_status`) retired alongside `generate_video` | It is the same 401-dead path and would collide with the new `api/video.py:VideoStatus` value object — retiring both together keeps the domain clean. |

---

## 6. Definition of done (per phase)

A phase ships when:

- [ ] All exit criteria for that phase are met
- [ ] CI is green (lint + format + type + test + coverage ≥ 80%)
- [ ] `CHANGELOG.md` `[Unreleased]` block lists every user-visible change
- [ ] README is updated if user-facing surface changed
- [ ] One BDD feature file exists for any new user-visible command (Phase 4+)
- [ ] No `# TODO` left in the diff without a tracked issue link

---

## 7. Open questions for confirmation before Phase 2 starts

| # | Question | Suggested default |
|---|---|---|
| Q1 | If reCAPTCHA fails headless, default to headed (visible window) or fail loud and tell user? | **Fail loud** with `GFLOW_CLI_HEADLESS=false` remediation. Headed pop-ups in batch mode would be unbearable. |
| Q2 | Default model tier — `fast` (Veo 3.1 Fast) or `quality` (Veo 3.1)? | **`fast`** — burns less credit per clip; users can opt into quality. |
| Q3 | Default seed behaviour — random or deterministic-from-prompt? | **Random** — matches Flow UI behaviour. `--seed N` for reproducibility. |
| Q4 | Default audio handling — `BLOCK_SILENCED_VIDEOS` (captured shape) or new `audio` flag? | **Block silenced** for v0.2.0a1; revisit if users want audio control. |
| Q5 | Manifest concurrency — sequential by default or parallel-by-account? | **Sequential** for v0.2.0a1. Concurrency lands in Phase 4. |
| Q6 | Should `gflow video i2v` auto-archive the uploaded start frame after generation, or keep it in the library? | **Keep**, with `--no-archive` flag. Auto-archive can leak quota in batch runs but lets users reuse uploads. |

---

_End of plan. Updates ship as part of the phase that motivated them._
