# flow-cli — Implementation Plan

> **Status:** Living document. Updated as phases complete.
> **Owner:** [@ffroliva](https://github.com/ffroliva)
> **Last revised:** 2026-05-09 (Video MVP scope rewrite)

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
| F7 | Generate images (T2I via Imagen) | Phase 3 (needs route capture) |
| F8 | Concurrency across accounts (pool) | Phase 4 |
| F9 | Switch to official Veo 3.1 SDK as `--provider official` | Phase 5 |

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

Current package layout:

```
src/flow_cli/
├── __init__.py
├── __main__.py
├── auth.py             ← login + status (Playwright headed for login)
├── cli.py              ← Click app
├── config.py           ← pydantic-settings
├── models.py           ← legacy domain DTOs (will fold into api/dto.py)
├── paths.py            ← XDG-aware default paths
├── profile_store.py    ← profile inventory + default-profile config.toml
├── providers/          ← legacy stub (will be removed in Phase 2 — superseded by api/)
└── api/                ← NEW
    ├── __init__.py
    ├── client.py       ← FlowApiClient (Playwright persistent context + REST)
    ├── dto.py          ← ProjectInfo, AssetInfo, VideoStatus, ...
    ├── routes.py       ← URL constants
    └── recaptcha.py    ← Phase 2 — token mint via page.evaluate
```

---

## 3. Configuration

Documented in [docs/CONFIGURATION.md](docs/CONFIGURATION.md) and [.env.template](.env.template). Variables:

`FLOW_CLI_HOME`, `FLOW_CLI_OUTPUT_DIR`, `FLOW_CLI_PROFILE`, `FLOW_CLI_PROVIDER`, `FLOW_CLI_GEMINI_API_KEY`, `FLOW_CLI_TIMEOUT_SECONDS`, `FLOW_CLI_LOG_LEVEL`, `FLOW_CLI_LOG_FORMAT`, `FLOW_CLI_CONCURRENCY`.

Default paths via `platformdirs`:

```text
$FLOW_CLI_HOME/                   ← user_data_dir/flow-cli
├── profile_<name>/               ← Chromium persistent contexts
└── config.toml                   ← default_profile = "..."

$FLOW_CLI_OUTPUT_DIR/             ← user_downloads_dir/flow-cli
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

### Phase 2 — Video MVP (T2V + I2V + batch) — **CURRENT FOCUS**

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
| **Headed mode by default for video gen** | Worse UX (window opens) but reliable. Add `FLOW_CLI_HEADLESS=auto\|true\|false`; default `auto` = headless until first failure, then headed. |
| **Headed only for token mint, headless for everything else** | More complex but keeps the rest invisible. |
| **Defer to user reporting** | Ship with headless default + clear error message instructing the user to set `FLOW_CLI_HEADLESS=false`. |

Default plan: **headless first**, instrument the failure with a remediation hint. If users report it failing, switch to headed-by-default.

#### Implementation sequence

Each step is a separate commit. Each one runs the four quality gates locally + verifies CI green before moving on.

**2.1 — reCAPTCHA token mint** (~1-2h)
- New file: `src/flow_cli/api/recaptcha.py`
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
- New file: `src/flow_cli/cli_video.py` (or extend `cli.py`)
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

- [ ] `gflow video t2v "..."` produces a real .mp4 against the user's Google AI Ultra/Pro account
- [ ] `gflow video i2v <png> "..."` produces a .mp4 whose first frame matches the input PNG
- [ ] `gflow video batch <tsv>` processes 3+ clips end-to-end
- [ ] All four quality gates green (ruff / format / pyright / pytest)
- [ ] Test coverage ≥ 80% on `src/flow_cli/api/`
- [ ] `samples/captured/` documents every wire format we depend on
- [ ] `KNOWN_ISSUES.md` updated with anything surprising discovered during 2.4
- [ ] Tagged `v0.2.0a1` on GitHub

---

### Phase 3 — Image generation (T2I via Imagen) — DEFERRED

#### Scope

`gflow image generate -p "<prompt>" [--aspect 1:1|9:16|...] [--count 1..4]` and `gflow image batch <tsv>`.

#### Prerequisite

We have NOT captured the Imagen route. Phase 3 starts with a focused 5-min discovery run against a real Flow Imagen flow, then mirrors the Phase 2 implementation pattern (DTO + client method + CLI + smoke).

#### Why deferred

Video gen is the user's primary need. Image gen has no captured routes yet, so it would block on a discovery run. Cleaner to ship video MVP first, then add images as v0.3.

---

### Phase 4 — Hardening — POST-v0.2.0a1

- Per-account pool + `FLOW_CLI_CONCURRENCY > 1` for parallel batches
- Retry / exponential backoff on 5xx + rate-limited responses
- Domain-error → exit-code mapping (so shell scripts can branch)
- Verbose error messages with remediation hints (e.g. "reCAPTCHA failed → run `gflow auth login` again")
- `structlog` configured with auto/text/json formats
- BDD scenarios in `tests/features/*.feature` via `pytest-bdd`

---

### Phase 5 — Public alpha release on PyPI

- Configure PyPI Trusted Publishing for `flow-cli`
- Verify `uvx --from flow-cli gflow --help` works on a fresh machine
- Tag `v0.2.0` (drop the alpha suffix) when MVP is stable enough for external use
- Announce (LinkedIn / X / dev.to / "Show HN")

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
| 8 | Output dir under `Downloads/flow-cli/` via platformdirs | OS-native, discoverable, easy to clean |
| 9 | No event sourcing, no message queue, no SaaS dependencies | YAGNI for a local CLI |
| 10 | Both `gflow` and `flow` binary names installed | `flow` is friendlier; `gflow` avoids conflicts with Facebook Flow / MS Power Automate |
| 11 | LF-only line endings via `.gitattributes` | Single repo source of truth; cross-platform contributors don't think about it |
| 12 | `Provider` indirection (legacy `providers/`) removed | Superseded by `api.FlowApiClient`. Re-introduce when we add `OfficialVeoProvider` in Phase 5 |

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
| Q1 | If reCAPTCHA fails headless, default to headed (visible window) or fail loud and tell user? | **Fail loud** with `FLOW_CLI_HEADLESS=false` remediation. Headed pop-ups in batch mode would be unbearable. |
| Q2 | Default model tier — `fast` (Veo 3.1 Fast) or `quality` (Veo 3.1)? | **`fast`** — burns less credit per clip; users can opt into quality. |
| Q3 | Default seed behaviour — random or deterministic-from-prompt? | **Random** — matches Flow UI behaviour. `--seed N` for reproducibility. |
| Q4 | Default audio handling — `BLOCK_SILENCED_VIDEOS` (captured shape) or new `audio` flag? | **Block silenced** for v0.2.0a1; revisit if users want audio control. |
| Q5 | Manifest concurrency — sequential by default or parallel-by-account? | **Sequential** for v0.2.0a1. Concurrency lands in Phase 4. |
| Q6 | Should `gflow video i2v` auto-archive the uploaded start frame after generation, or keep it in the library? | **Keep**, with `--no-archive` flag. Auto-archive can leak quota in batch runs but lets users reuse uploads. |

---

_End of plan. Updates ship as part of the phase that motivated them._
