# flow-cli

> **Unofficial, reverse-engineered Python CLI for Google Flow.**
> Drive [Google Flow](https://labs.google/fx/tools/flow) Veo image-to-video generations from your terminal — **without the browser**.

[![CI](https://github.com/ffroliva/flow-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/ffroliva/flow-cli/actions/workflows/ci.yml)
[![Release](https://github.com/ffroliva/flow-cli/actions/workflows/release.yml/badge.svg)](https://github.com/ffroliva/flow-cli/actions/workflows/release.yml)
[![PyPI version](https://img.shields.io/pypi/v/flow-cli.svg)](https://pypi.org/project/flow-cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/flow-cli.svg)](https://pypi.org/project/flow-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#project-status)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checked: pyright](https://img.shields.io/badge/type%20checked-pyright-blue.svg)](https://github.com/microsoft/pyright)
[![Tests: TDD](https://img.shields.io/badge/tests-TDD-brightgreen.svg)](#development--tdd-workflow)

> ⚠️ **Not affiliated with Google.** Reverse-engineered from public Flow web traffic. Endpoints can change at any time. See full [DISCLAIMER](DISCLAIMER.md) before use.

📚 **Docs:** [INDEX](docs/INDEX.md) · [Architecture](docs/ARCHITECTURE.md) · [Authentication](docs/AUTHENTICATION.md) · [Configuration](docs/CONFIGURATION.md) · [Usage](docs/USAGE.md) · [Security](docs/SECURITY.md) · [Known issues](KNOWN_ISSUES.md) · [Plan](PLAN.md) · [Changelog](CHANGELOG.md)

---

## Objective

**For Google AI Ultra and Pro subscribers who have Flow credits and want to use them efficiently.**

Your subscription includes a generous Veo credit allowance, but the Flow web UI was designed for hand-crafted, one-at-a-time video creation — not for the batch workflows that actually justify burning through hundreds of credits a month. The UI is slow (waiting for the React app, dragging assets, clicking through dialogs), the DOM is fragile to automate, and there's no way to script "generate these 50 clips while I'm at lunch."

`flow-cli` reverse-engineers Flow's internal REST API on `aisandbox-pa.googleapis.com` and exposes it as a clean command-line tool. **Same Veo model, same quality, same Ultra/Pro billing — without ever opening a browser** (after a one-time auth capture).

Now you can:

- **Burn credits efficiently** — `for img in ./inputs/*.png; do gflow i2v "$img" "$prompt" -o "out/$(basename "$img" .png).mp4"; done`
- **Build pipelines** — wire Veo into your AI video production stack, content automation, or batch experiments
- **Stay in the terminal** — no Chromium, no waiting for the UI to load, no clicking through 4 dialogs per clip
- **Parallelise** — drive multiple accounts side-by-side with `--profile` (planned v0.4)

This project is the same pattern as [`edge-tts`](https://github.com/rany2/edge-tts) — an unofficial Python client over Microsoft's Azure TTS service used by the Edge browser.

---

## Disclaimer

`flow-cli` is **not affiliated with, endorsed by, or sponsored by Google**. It calls a private API surface (`aisandbox-pa.googleapis.com`) that Google can change or restrict at any time. By using this tool you accept that:

- You must already have a valid Google AI Ultra or Pro subscription with Flow access.
- All generations bill against **your own Google account**, subject to Google's terms.
- Endpoints, request shapes, and auth flows may break without notice.
- The maintainer will respond promptly to any takedown request from Google.

Read the full [DISCLAIMER](DISCLAIMER.md) before deploying this in any production setting.

---

## Project status

**v0.1.0 — pre-release alpha.** Routes captured, scaffold in place, implementations being filled in. **Not yet usable end-to-end.**

| Milestone | Status |
|---|---|
| Repo scaffold, CI, license, README, disclaimer | ✅ done |
| Auth login flow (one-time browser capture) | 🔧 in progress |
| `upload_image` route wired | 🔧 in progress |
| `start_generation` route wired | 🔧 in progress |
| `get_job` polling | 🔧 in progress |
| `download` signed URL fetch | 🔧 in progress |
| End-to-end smoke test against live Flow | 🔧 in progress |
| First public alpha release on PyPI | ⏳ planned (v0.2) |
| Provider abstraction for official Veo 3.1 API | ⏳ planned (v0.3) |
| Concurrency / per-account pool | ⏳ planned (v0.4) |

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
uvx --from flow-cli gflow --help
```

`uvx` (from [uv](https://docs.astral.sh/uv/)) downloads and runs the package in a throwaway environment. **No global install, no virtualenv to manage.** Perfect for occasional batch runs or trying it before committing.

### Install as a user tool

```bash
uv tool install flow-cli
gflow --help
```

This installs `gflow` (and `flow` if no conflict) on your `PATH` system-wide, isolated from your project venvs. Update with `uv tool upgrade flow-cli`.

### From source (current — pre-release)

```bash
git clone git@github.com:ffroliva/flow-cli.git
cd flow-cli
uv sync                          # creates .venv, installs runtime + dev deps
uv run playwright install chromium   # one-time browser download (~150 MB)
uv run gflow --help
```

### Install Playwright Chromium (one-time, any install method)

```bash
uvx --from flow-cli playwright install chromium
# or after `uv tool install`:
uv tool run --from flow-cli playwright install chromium
```

---

## Quick start

```bash
# 1. Sign in once — opens a Chromium window, persists session locally
gflow auth login

# 2. Verify
gflow auth status

# 3. Generate a clip end-to-end
gflow i2v ./input.png "Slow cinematic push-in, soft golden light" -o out.mp4
```

Same call from Python:

```python
import asyncio
from pathlib import Path
from flow_cli.providers.flow import FlowProvider
from flow_cli.auth import profile_dir
from flow_cli.models import GenerationRequest

async def make_clip():
    async with FlowProvider(profile_dir=profile_dir()) as p:
        asset = await p.upload_image(Path("input.png"))
        job = await p.start_generation(GenerationRequest(
            start_image=Path("input.png"),
            motion_prompt="Slow cinematic push-in, soft golden light",
        ))
        # Poll until job.status == JobStatus.SUCCEEDED, then:
        # await p.download(job.output_url, Path("out.mp4"))

asyncio.run(make_clip())
```

---

## Commands (v0.1)

```text
gflow auth login                          # one-time browser sign-in
gflow auth status                         # show current session

gflow upload <image>                      # → asset UUID
gflow generate -s <uuid> -p "<prompt>"    # kick off Veo gen, returns job_id
gflow status <job_id>                     # poll job status
gflow download <job_id> -o out.mp4        # fetch result

gflow i2v <image> "<prompt>" -o out.mp4   # convenience: upload + generate + poll + download
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
| Type checking | [`pyright`](https://github.com/microsoft/pyright) (strict on `src/flow_cli`) | Catches errors before runtime |
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
│  Provider       │ ← protocol (Provider in flow_cli/providers/base.py)
│  abstraction    │
└────────┬────────┘
         │
   ┌─────┴─────┬───────────────┐
   │           │               │
┌──▼──┐    ┌───▼───┐       ┌───▼───┐
│Flow │    │Official│       │ Mock  │
│(v0.1)│   │ Veo    │       │(tests)│
│     │    │(v0.3+) │       │       │
└──┬──┘    └────────┘       └───────┘
   │
   │   POST /v1/flow/uploadImage
   │   POST /v1/video:batchAsyncGenerateVideoText
   │   POST /v1/video:batchCheckAsyncVideoGenerationStatus
   │   PATCH /v1/flowWorkflows/{id}
   ▼
aisandbox-pa.googleapis.com  (Google's private Flow API)
```

The `Provider` interface keeps backends interchangeable. v0.1 ships `FlowProvider`. v0.3+ will add `OfficialVeoProvider` (uses [`googleapis/python-genai`](https://github.com/googleapis/python-genai) against `generativelanguage.googleapis.com`) — same code path, swap with `--provider official`.

### Auth strategy

`flow-cli` doesn't reverse-engineer Google's OAuth flow. Instead it **piggybacks on Playwright's persistent context**: `gflow auth login` opens a Chromium window, you sign in normally, and the resulting cookie jar is saved to `~/.flow-cli/profile_default/`. Subsequent commands launch a **headless** Playwright context using that profile and call REST endpoints via Playwright's HTTP client — which auto-attaches the cookies. No tokens to refresh manually, no SSO scraping. Auth is the only browser interaction, and it's a one-time event.

---

## Use as a Claude Code (or other agent) skill

`flow-cli` ships an installable [Claude Code Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) at [`skills/flow-cli/SKILL.md`](skills/flow-cli/SKILL.md).

**Install for Claude Code:**

```bash
# Clone the repo, then symlink the skill into your Claude skills dir:
ln -s "$(pwd)/skills/flow-cli" ~/.claude/skills/flow-cli
```

**Use in any other agent (Cursor, Codex, Gemini CLI, Aider, ...):** the SKILL.md is plain Markdown — point your agent's context at it as a reference doc. The CLI is the same regardless of caller.

When the skill is loaded, an agent sees:
- When to invoke flow-cli (the user wants to generate a Veo video, has Flow access, etc.)
- The full command surface
- How to handle auth (kick off `gflow auth login` once, then headless)
- Common error modes and fixes

---

## Development & TDD workflow

`flow-cli` is **test-driven**. Every public function in `Provider` implementations starts as a **red test** that locks the contract before any production code is written. CI rejects any PR that lowers test coverage.

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
uv run pytest -q --cov=flow_cli     # with coverage
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

`flow-cli` follows **[Semantic Versioning 2.0.0](https://semver.org/)** — breaking changes bump MAJOR, new features bump MINOR, fixes bump PATCH.

### Cadence

- **Alpha (`0.x.y`)**: rapid iteration. APIs may change between minor versions.
- **`1.0.0`**: stable surface. Breaking changes require MAJOR bump and migration notes.
- **Patch releases** ship as needed for bug fixes.

### How releases work

1. Update [`CHANGELOG.md`](CHANGELOG.md) with the version's changes (Keep-a-Changelog format).
2. Bump `version` in `pyproject.toml`.
3. Tag the commit:
   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```
4. The [`release.yml`](.github/workflows/release.yml) GitHub Action runs:
   - Builds the wheel + sdist with `uv build`
   - Publishes to PyPI via [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) — no API tokens stored
   - Creates a GitHub Release with the changelog excerpt + built artifacts attached

Pre-release tags (`v*.*.*-rc*`, `v*.*.*-alpha*`, `v*.*.*-beta*`) auto-flag as pre-releases on GitHub. Install with `pip install --pre flow-cli` or `uvx --from "flow-cli==0.2.0a1" gflow`.

---

## License

[MIT License](LICENSE) © 2026 Flavio Oliva (`ffroliva`).

The full text is in [LICENSE](LICENSE). In short:

- ✅ Commercial use, modification, distribution, private use — all allowed.
- ❗ No warranty — provided as-is.
- ❗ Must include the original license + copyright in any copy/derivative.

Note that the **Google service** this tool talks to has its own terms (Google Labs Additional Terms, Google AI Ultra/Pro subscription terms, etc.). The MIT license here covers `flow-cli`'s code only — it does not grant any rights to Flow itself or to Veo model output. See [DISCLAIMER](DISCLAIMER.md).

---

## Acknowledgements

- [`edge-tts`](https://github.com/rany2/edge-tts) — design inspiration for community SDKs over private cloud APIs.
- [`googleapis/python-genai`](https://github.com/googleapis/python-genai) — the official Veo SDK that v0.3+ will alias.
- [Keysight Technologies — *Google Labs – Flow AI with Veo3: A Network Traffic Analysis*](https://www.keysight.com/blogs/en/tech/nwvs/2025/08/04/google-flow-ai-har-analysis) — independent traffic capture that helped validate the captured route patterns.

---

## Stats

[![GitHub stars](https://img.shields.io/github/stars/ffroliva/flow-cli?style=social)](https://github.com/ffroliva/flow-cli/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/ffroliva/flow-cli?style=social)](https://github.com/ffroliva/flow-cli/network/members)
[![GitHub watchers](https://img.shields.io/github/watchers/ffroliva/flow-cli?style=social)](https://github.com/ffroliva/flow-cli/watchers)
[![GitHub issues](https://img.shields.io/github/issues/ffroliva/flow-cli)](https://github.com/ffroliva/flow-cli/issues)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/ffroliva/flow-cli)](https://github.com/ffroliva/flow-cli/pulls)
[![GitHub last commit](https://img.shields.io/github/last-commit/ffroliva/flow-cli)](https://github.com/ffroliva/flow-cli/commits/main)
[![GitHub repo size](https://img.shields.io/github/repo-size/ffroliva/flow-cli)](https://github.com/ffroliva/flow-cli)
[![PyPI downloads](https://img.shields.io/pypi/dm/flow-cli.svg)](https://pypi.org/project/flow-cli/)
[![PyPI total downloads](https://static.pepy.tech/badge/flow-cli)](https://pepy.tech/project/flow-cli)

If `flow-cli` saves you time, please ⭐ the repo — it's the cheapest way to support the project.
