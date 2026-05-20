# gflow-cli

> **Unofficial, reverse-engineered Python CLI for Google Flow.**
> Drive [Google Flow](https://labs.google/fx/tools/flow) Veo image-to-video generations from your terminal — **without the browser**.

[![CI](https://github.com/ffroliva/gflow-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/ffroliva/gflow-cli/actions/workflows/ci.yml)
[![Release](https://github.com/ffroliva/gflow-cli/actions/workflows/release.yml/badge.svg)](https://github.com/ffroliva/gflow-cli/actions/workflows/release.yml)
[![PyPI version](https://img.shields.io/pypi/v/gflow-cli.svg)](https://pypi.org/project/gflow-cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/gflow-cli.svg)](https://pypi.org/project/gflow-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#project-status)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checked: pyright](https://img.shields.io/badge/type%20checked-pyright-blue.svg)](https://github.com/microsoft/pyright)
[![Tests: TDD](https://img.shields.io/badge/tests-TDD-brightgreen.svg)](#development--tdd-workflow)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=ffroliva_gflow-cli&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ffroliva_gflow-cli)

> ⚠️ **Not affiliated with Google.** Reverse-engineered from public Flow web traffic. Endpoints can change at any time. See full [DISCLAIMER](DISCLAIMER.md) before use.

📚 **Docs:** [INDEX](docs/INDEX.md) · [User Guide](docs/USER_GUIDE.md) · [Architecture](docs/ARCHITECTURE.md) · [Authentication](docs/AUTHENTICATION.md) · [Configuration](docs/CONFIGURATION.md) · [Usage](docs/USAGE.md) · [Security](docs/SECURITY.md) · [Known issues](KNOWN_ISSUES.md) · [Release protocol](RELEASE.md) · [Plan](PLAN.md) · [Changelog](CHANGELOG.md)
🤖 **For AI agents:** [CLAUDE.md](CLAUDE.md) · [`.claude/`](.claude/README.md)

---

## Objective

**For Google AI Ultra and Pro subscribers who have Flow credits and want to use them efficiently.**

Your subscription includes a generous Veo credit allowance, but the Flow web UI was designed for hand-crafted, one-at-a-time video creation — not for the batch workflows that actually justify burning through hundreds of credits a month. The UI is slow (waiting for the React app, dragging assets, clicking through dialogs), the DOM is fragile to automate, and there's no way to script "generate these 50 clips while I'm at lunch."

`gflow-cli` reverse-engineers Flow's internal REST API on `aisandbox-pa.googleapis.com` and exposes it as a clean command-line tool. **Same Veo model, same quality, same Ultra/Pro billing — without ever opening a browser** (after a one-time auth capture).

Now you can:

- **Burn credits efficiently** — `for img in ./inputs/*.png; do gflow video i2v "$img" "$prompt" -o "out/$(basename "$img" .png).mp4"; done`
- **Build pipelines** — wire Veo into your AI video production stack, content automation, or batch experiments
- **Stay in the terminal** — no Chromium, no waiting for the UI to load, no clicking through 4 dialogs per clip
- **Parallelise** — `GFLOW_CLI_CONCURRENCY=4 gflow video batch manifest.tsv` fans out across 4 Playwright Pages on one profile (v0.4.0a2); `--profile` swaps accounts

This project is the same pattern as [`edge-tts`](https://github.com/rany2/edge-tts) — an unofficial Python client over Microsoft's Azure TTS service used by the Edge browser.

---

## Disclaimer

`gflow-cli` is **not affiliated with, endorsed by, or sponsored by Google**. It calls a private API surface (`aisandbox-pa.googleapis.com`) that Google can change or restrict at any time. By using this tool you accept that:

- You must already have a valid Google AI Ultra or Pro subscription with Flow access.
- All generations bill against **your own Google account**, subject to Google's terms.
- Endpoints, request shapes, and auth flows may break without notice.
- The maintainer will respond promptly to any takedown request from Google.

Read the full [DISCLAIMER](DISCLAIMER.md) before deploying this in any production setting.

---

## Project status

**v0.7.0 — first stable.** Image (T2I/I2I/upload), the **`gflow run` JSON-batch command**, and the **`ui_automation` default transport** are functional end-to-end against a live Google AI Pro/Ultra Flow account — every CLI aspect ratio (`9:16`, `16:9`, `1:1`, `4:3`, `3:4`) live-verified for v0.7.0 (see [`docs/LIVE_VERIFICATION_v0.7.0.md`](docs/LIVE_VERIFICATION_v0.7.0.md)). Video T2V works at the library level via `UiAutomationTransport.generate_video()`; CLI wiring for `gflow video t2v/i2v/batch` is queued for Phase B. Three earlier HTTP transport strategies (`evaluate_fetch` / `bearer` / `sapisidhash`) live in an `experimental/` subpackage; the production path is `ui_automation`.

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
| Video CLI on `ui_automation` (`gflow video t2v/i2v/batch`) | ⏳ Phase B |
| Provider abstraction for official Veo 3.1 API | ⏳ planned |

### What's new in v0.7.0

- **Downstream-worker ergonomics** — `FlowApiClient(out_dir=...)` plumbing for debug screenshots, `health_check()` for liveness probes, optional `project_id` on `generate_image*`, and `BrowserSessionClosedError` (exit code 15) translating `playwright._impl._errors.TargetClosedError` into a stable library-owned class. Closes issues [#16](https://github.com/ffroliva/gflow-cli/issues/16) and [#18](https://github.com/ffroliva/gflow-cli/issues/18).
- **`gflow_cli.exceptions` module** — standard alias for `gflow_cli.errors`; either import path works.
- **Auth hardening** — `gflow auth login` now verifies a real Flow app session before reporting success ([#15](https://github.com/ffroliva/gflow-cli/issues/15)) and fails fast with `AuthBrowserRejectedError` (exit 14) when Google rejects Playwright's bundled Chromium ([#17](https://github.com/ffroliva/gflow-cli/issues/17)).
- **1:1 aspect-tab cascade** — exact-match (`:text-is`) cascade against `1:1`, `Square`, `1×1`, `1x1` replaces the brittle substring selector; all 5 aspect ratios live-verified end-to-end.
- **Overlay-dismiss helper** — first-run profiles no longer fail on the next click after the Flow "What's new" iframe appears ([#26](https://github.com/ffroliva/gflow-cli/issues/26)).
- **Signed-tag CI gate** — `release.yml` now rejects unsigned tags ([#30](https://github.com/ffroliva/gflow-cli/issues/30)).
- **Listener instrumentation** — `ui_automation.batch_response_seen` and `…_dropped_project_id_mismatch` log keys eliminate the silent listener-miss black-hole.
- **New docs** — [`docs/DEBUGGING.md`](docs/DEBUGGING.md) (evergreen reference) and [`docs/LIVE_VERIFICATION_v0.7.0.md`](docs/LIVE_VERIFICATION_v0.7.0.md) (per-release evidence).
- See the full [CHANGELOG](CHANGELOG.md) for details.

---

## Demo

![gflow image t2i — single 9:16 prompt, streaming structlog output, PNG on disk](docs/assets/example-run.gif)

*A single `gflow image t2i "..." --aspect 9:16 --model nano2 --out ...` call against a logged-in Pro/Ultra profile. The terminal shows the streaming `structlog` JSON for the run, the final `ls` of the written PNG, and nothing else — Chromium drives the Flow editor silently in the background because the persistent Playwright session is already warm, so this take is intentionally terminal-only.*

**Try it yourself** — this is the exact command the GIF runs:

```bash
gflow image t2i "a quiet mountain lake at dawn, cinematic photography" --aspect 9:16 --model nano2 --out ./gflow-output/example-single
```

One prompt, default model (`nano2` = Nano Banana 2), 9:16 portrait, written as `./gflow-output/example-single/<media_name>_0.png`. Wall time is typically 30–90s on a warm profile; the first call after a fresh `gflow auth login` is slower because Playwright spins up Chromium and navigates to the Flow editor. See [`docs/USAGE.md`](docs/USAGE.md) for the full `gflow image t2i` flag reference (`--model`, `--aspect`, `-n`, `--seed`, `--profile`, `--transport`).

To reproduce the recording yourself, see [`scripts/record_demo.ps1`](scripts/record_demo.ps1) (Windows, requires OBS + ffmpeg + gifski).

---

## Prerequisites

| Requirement | Why |
|---|---|
| **Python 3.11+** | Modern type hints, asyncio improvements |
| **[uv](https://docs.astral.sh/uv/)** ≥ 0.4 | Dependency + virtualenv management; also enables `uvx` runs |
| **Playwright Chromium** | Used **once** for `auth login` and as the HTTP transport (cookie jar). No UI automation. |
| **Google AI Ultra or Pro** account with Flow access | Otherwise the API returns 403. Try in [labs.google/fx/tools/flow](https://labs.google/fx/tools/flow) first. |
| ~500 MB disk | Chromium browser + Python deps |

Tested on Windows 11 + macOS 14 + Ubuntu 24.04. Linux + WSL work but `auth login` needs a display server (X / Wayland) for the one-time browser capture; a saved profile transfers freely between machines.

---

## Install

### Try it without installing (zero-config, recommended for first run)

```bash
uvx --from gflow-cli gflow --help
```

`uvx` (from [uv](https://docs.astral.sh/uv/)) downloads and runs the package in a throwaway environment. **No global install, no virtualenv to manage.** Perfect for occasional batch runs or trying it before committing.

### Install as a user tool

```bash
uv tool install gflow-cli
gflow --help
```

This installs `gflow` (and `flow` if no conflict) on your `PATH` system-wide, isolated from your project venvs. Update with `uv tool upgrade gflow-cli`.

### From source (current — pre-release)

```bash
git clone git@github.com:ffroliva/gflow-cli.git
cd gflow-cli
uv sync                          # creates .venv, installs runtime + dev deps
uv run playwright install chromium   # one-time browser download (~150 MB)
uv run gflow --help
```

### Install Playwright Chromium (one-time, any install method)

```bash
uvx --from gflow-cli playwright install chromium
# or after `uv tool install`:
uv tool run --from gflow-cli playwright install chromium
```

---

## Quick start

```bash
# 1. Sign in once — opens a Chromium window, persists session locally
gflow auth login

# 2. Verify
gflow auth status

# 3a. Generate an image from a text prompt (lands at $GFLOW_CLI_OUTPUT_DIR/images/<date>/)
gflow image t2i "a hot air balloon over Tokyo at sunrise"

# 3b. Generate a clip end-to-end
gflow video i2v ./input.png "Slow cinematic push-in, soft golden light" -o out.mp4
```

The image lands at `$GFLOW_CLI_OUTPUT_DIR/images/<YYYY-MM-DD>/<media_name>_1.png` (defaults to `./out/` when the env var is unset). See [docs/USAGE.md § `gflow image t2i`](docs/USAGE.md#gflow-image-t2i) for `--model`, `--aspect`, `-n/--count`, `--seed`, and `--out` flags.

> **Video generation is being rebuilt.** The HTTP video path returned HTTP 401
> and was retired. Video generation now runs on the UI-automation transport:
> Phase A ships the text-to-video transport; the `gflow video` CLI commands
> return in a later release (Phase B). See
> `docs/superpowers/specs/2026-05-18-ui-automation-video-generation-design.md`.

---

## Commands

```text
gflow auth login                                         # one-time browser sign-in
gflow auth status                                        # show current session

gflow image upload <path>                                # upload PNG/JPEG → asset UUID
gflow image t2i "<prompt>" [...] [--model] [--aspect]    # text-to-image; repeat prompts for a warm batch
gflow image t2i --prompts-file prompts.txt               # text-file multi-prompt batch
gflow image t2i --stdin                                  # stdin multi-prompt batch
gflow image i2i "<prompt>" --ref PATH_OR_UUID [...]      # image-to-image (1–4 per call)

gflow video t2v "<prompt>" -o out.mp4                    # text-to-video — returns in Phase B
gflow video i2v <image> "<prompt>" -o out.mp4            # image-to-video — returns in Phase B
gflow video batch <manifest.tsv>                         # TSV-driven batch — returns in Phase B
```

Each command supports `--profile <name>` for managing multiple Google accounts side-by-side.

---

## Stack

| Layer | Tech | Why |
|---|---|---|
| Package + deps | [`uv`](https://docs.astral.sh/uv/) + [`hatchling`](https://hatch.pypa.io/) | Fast install, lockfile, builds wheels |
| CLI framework | [`click`](https://click.palletsprojects.com/) | Mature, declarative, composable subcommands |
| Console UI | [`rich`](https://rich.readthedocs.io/) | Pretty progress bars, colour, tables |
| HTTP transport | [`playwright`](https://playwright.dev/python/) (`page.request`) | Auto-attaches Google session cookies — no OAuth scraping |
| Async | stdlib `asyncio` | Concurrency primitive for parallel generations |
| Retry / backoff | [`tenacity`](https://github.com/jd/tenacity) | Exponential jittered backoff on transient 5xx / 429 / network errors |
| Structured logs | [`structlog`](https://www.structlog.org/) | Privacy-safe JSON-on-pipe, `error_raised` / `error_unhandled` events |
| Type checking | [`pyright`](https://github.com/microsoft/pyright) (strict on `src/gflow_cli`) | Catches errors before runtime |
| Linting / format | [`ruff`](https://github.com/astral-sh/ruff) | Single tool, fast |
| Testing | [`pytest`](https://docs.pytest.org/) + [`pytest-asyncio`](https://pytest-asyncio.readthedocs.io/) | Standard, async-aware |
| CI/CD | GitHub Actions | Free, matrix builds, OIDC trusted publishing |

No FastAPI, no Django, no SQLAlchemy. This is a CLI + library — keeping the runtime surface tight and `uvx`-friendly.

---

## Architecture

```text
┌─────────────────┐
│  gflow CLI      │ ← Click + Rich
└────────┬────────┘
         │
┌────────▼────────┐
│  Provider       │ ← protocol (Provider in gflow_cli/providers/base.py)
│  abstraction    │
└────────┬────────┘
         │
   ┌─────┴─────┬───────────────┐
   │           │               │
┌──▼──┐    ┌────────┐       ┌───────┐
│Flow │    │Official│       │ Mock  │
│(now)│    │ Veo    │       │(tests)│
│     │    │(planned│       │       │
│     │    │ later) │       │       │
│     │    │        │       │       │
└──┬──┘    └────────┘       └───────┘
   │
   │   POST /v1/flow/uploadImage
   │   POST /v1/video:batchAsyncGenerateVideoText
   │   POST /v1/video:batchCheckAsyncVideoGenerationStatus
   │   PATCH /v1/flowWorkflows/{id}
   ▼
aisandbox-pa.googleapis.com  (Google's private Flow API)
```

The `Provider` interface keeps backends interchangeable. v0.1 ships `FlowProvider`. A future release may add `OfficialVeoProvider` (uses [`googleapis/python-genai`](https://github.com/googleapis/python-genai) against `generativelanguage.googleapis.com`) — same code path, swap with `GFLOW_CLI_PROVIDER=official`.

### Auth strategy

`gflow-cli` doesn't reverse-engineer Google's OAuth flow. Instead it **piggybacks on Playwright's persistent context**: `gflow auth login` opens a Chromium window, you sign in normally, and the resulting cookie jar is saved to a per-OS user-data dir via [`platformdirs`](https://github.com/platformdirs/platformdirs):

- Windows: `%LOCALAPPDATA%\gflow-cli\profile_default\`
- macOS: `~/Library/Application Support/gflow-cli/profile_default/`
- Linux: `~/.local/share/gflow-cli/profile_default/`

Subsequent commands launch a **headless** Playwright context using that profile and call REST endpoints via Playwright's HTTP client — which auto-attaches the cookies. No tokens to refresh manually, no SSO scraping. Auth is the only browser interaction, and it's a one-time event.

---

## Use as a Claude Code (or other agent) skill

`gflow-cli` ships an installable [Claude Code Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) at [`skills/gflow-cli/SKILL.md`](skills/gflow-cli/SKILL.md).

**Install for Claude Code:**

```bash
# Clone the repo, then symlink the skill into your Claude skills dir:
ln -s "$(pwd)/skills/gflow-cli" ~/.claude/skills/gflow-cli
```

**Use in any other agent (Cursor, Codex, Gemini CLI, Aider, ...):** the SKILL.md is plain Markdown — point your agent's context at it as a reference doc. The CLI is the same regardless of caller.

When the skill is loaded, an agent sees:
- When to invoke gflow-cli (the user wants to generate a Veo video, has Flow access, etc.)
- The full command surface
- How to handle auth (kick off `gflow auth login` once, then headless)
- Common error modes and fixes

---

## Development & TDD workflow

`gflow-cli` is **test-driven**. Every public function in `Provider` implementations starts as a **red test** that locks the contract before any production code is written. CI rejects any PR that lowers test coverage.

```bash
# Setup
uv sync --extra dev
uv run playwright install chromium

# Quality checks (CI runs all three)
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src

# Tests
uv run pytest -q                    # all tests
uv run pytest -q --cov=gflow_cli     # with coverage
uv run pytest tests/test_providers.py -q   # one file
uv run pytest -k "i2v" -q                  # by keyword
```

### TDD discipline

1. **Red** — write a failing test that captures the new behaviour.
2. **Green** — write the minimum production code to make it pass.
3. **Refactor** — clean up, keep tests green.
4. **Commit** — small, atomic, with a descriptive message.

Each `Provider` method has a corresponding test file under `tests/`. New routes start as `pytest.raises(NotImplementedError)` markers, then move to behavioural tests with mocked HTTP, then to live integration tests behind a `@pytest.mark.live` opt-in. See [CONTRIBUTING.md](CONTRIBUTING.md) for full workflow.

---

## Releases

`gflow-cli` follows **[Semantic Versioning 2.0.0](https://semver.org/)** — breaking changes bump MAJOR, new features bump MINOR, fixes bump PATCH.

### Cadence

- **Alpha (`0.x.y`)**: rapid iteration. APIs may change between minor versions.
- **`1.0.0`**: stable surface. Breaking changes require MAJOR bump and migration notes.
- **Patch releases** ship as needed for bug fixes.

### How releases work

1. Update [`CHANGELOG.md`](CHANGELOG.md) with the version's changes (Keep-a-Changelog format).
2. Bump `version` in `pyproject.toml`.
3. Bump `__version__` in `src/gflow_cli/__init__.py`.
4. Tag the commit with a **signed annotated tag** (`-s`; CI rejects unsigned/lightweight tags):
   ```bash
   git tag -s v<version> -m "v<version>"  # for example, v0.7.0 or v0.7.1a1
   git push origin v<version>
   ```
5. The [`release.yml`](.github/workflows/release.yml) GitHub Action runs:
   - Builds the wheel + sdist with `uv build`
   - Publishes to PyPI via [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) — no API tokens stored
   - Creates a GitHub Release with the changelog excerpt + built artifacts attached

PEP 440 prerelease tags (`vX.Y.ZaN`, `vX.Y.ZbN`, `vX.Y.ZrcN`) and hyphenated prerelease tags (`vX.Y.Z-alphaN`, `vX.Y.Z-betaN`, `vX.Y.Z-rcN`) auto-flag as prereleases on GitHub. Stable tags such as `v0.6.0` become full GitHub Releases. See [RELEASE.md](RELEASE.md) for the checklist and the prerelease/full-release policy.

Stable releases (e.g. `v0.7.0`) install via `pip install gflow-cli` or `uvx --from "gflow-cli==0.7.0" gflow`. Prereleases (`vX.Y.ZaN`/`bN`/`rcN`) need `pip install --pre gflow-cli` or an explicit pin like `uvx --from "gflow-cli==0.7.1a1" gflow`.

---

## License

[MIT License](LICENSE) © 2026 Flavio Oliva (`ffroliva`).

The full text is in [LICENSE](LICENSE). In short:

- ✅ Commercial use, modification, distribution, private use — all allowed.
- ❗ No warranty — provided as-is.
- ❗ Must include the original license + copyright in any copy/derivative.

Note that the **Google service** this tool talks to has its own terms (Google Labs Additional Terms, Google AI Ultra/Pro subscription terms, etc.). The MIT license here covers `gflow-cli`'s code only — it does not grant any rights to Flow itself or to Veo model output. See [DISCLAIMER](DISCLAIMER.md).

---

## Acknowledgements

- [`edge-tts`](https://github.com/rany2/edge-tts) — design inspiration for community SDKs over private cloud APIs.
- [`googleapis/python-genai`](https://github.com/googleapis/python-genai) — the official Veo SDK that a future release may alias.
- [Keysight Technologies — *Google Labs – Flow AI with Veo3: A Network Traffic Analysis*](https://www.keysight.com/blogs/en/tech/nwvs/2025/08/04/google-flow-ai-har-analysis) — independent traffic capture that helped validate the captured route patterns.

---

## Stats

[![GitHub stars](https://img.shields.io/github/stars/ffroliva/gflow-cli?style=social)](https://github.com/ffroliva/gflow-cli/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/ffroliva/gflow-cli?style=social)](https://github.com/ffroliva/gflow-cli/network/members)
[![GitHub watchers](https://img.shields.io/github/watchers/ffroliva/gflow-cli?style=social)](https://github.com/ffroliva/gflow-cli/watchers)
[![GitHub issues](https://img.shields.io/github/issues/ffroliva/gflow-cli)](https://github.com/ffroliva/gflow-cli/issues)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/ffroliva/gflow-cli)](https://github.com/ffroliva/gflow-cli/pulls)
[![GitHub last commit](https://img.shields.io/github/last-commit/ffroliva/gflow-cli)](https://github.com/ffroliva/gflow-cli/commits/main)
[![GitHub repo size](https://img.shields.io/github/repo-size/ffroliva/gflow-cli)](https://github.com/ffroliva/gflow-cli)
[![PyPI downloads](https://img.shields.io/pypi/dm/gflow-cli.svg)](https://pypi.org/project/gflow-cli/)

If `gflow-cli` saves you time, please ⭐ the repo — it's the cheapest way to support the project.
