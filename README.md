# gflow-cli

> Unofficial Python CLI for Google Flow. Drive [Veo](https://labs.google/fx/tools/flow) (image-to-video, text-to-video) and Imagen (text-to-image) from your terminal: scripted, batched, pipeline-ready.

[![PyPI version](https://img.shields.io/pypi/v/gflow-cli.svg)](https://pypi.org/project/gflow-cli/)
[![CI](https://github.com/ffroliva/gflow-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/ffroliva/gflow-cli/actions/workflows/ci.yml)
[![Release](https://github.com/ffroliva/gflow-cli/actions/workflows/release.yml/badge.svg)](https://github.com/ffroliva/gflow-cli/actions/workflows/release.yml)
[![Python versions](https://img.shields.io/pypi/pyversions/gflow-cli.svg)](https://pypi.org/project/gflow-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](docs/PROJECT_STATUS.md)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checked: pyright](https://img.shields.io/badge/type%20checked-pyright-blue.svg)](https://github.com/microsoft/pyright)
[![Tests: TDD](https://img.shields.io/badge/tests-TDD-brightgreen.svg)](CONTRIBUTING.md)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=ffroliva_gflow-cli&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ffroliva_gflow-cli)

> ⚠️ **Read this before you install.** gflow-cli is **unofficial, alpha, and reverse-engineered — not affiliated with Google**. It drives a headed browser on *your own* Google Flow session, so treat it as your own account risk: automation is subject to Google's ToS, and endpoints or UI can change without notice. It requires a Google AI **Ultra or Pro** subscription with Flow access, and every generation bills your account. Read the full [DISCLAIMER](DISCLAIMER.md).
>
> 💳 **What failure costs you.** Credits are only spent on Veo *video* generation — images and composition ops are free, so most breakage costs nothing. When Flow's UI drifts mid-run, the CLI fails fast and loudly with distinct exit codes (e.g. selector drift = exit 23) instead of resubmitting, and batch items are recorded locally *before* submission so a broken run never silently burns credits on a stale state. See [KNOWN_ISSUES](KNOWN_ISSUES.md) for the current risk list.
>
> 🌐 **Headed browser today.** gflow drives Flow through a persistent Playwright Chromium profile, because Google's auth and reCAPTCHA gates require it. The [Architecture](#architecture--current-limitations) section shows where you can help.

## Why gflow-cli?

You pay for Google AI Ultra or Pro, you have Veo credits, and you run real batch work. gflow-cli gives you:

- **Batch generation.** Loop prompts straight from the shell: `for p in $(cat prompts.txt); do gflow image t2i "$p"; done`. Image batching plus `gflow video t2v` / `i2v` / `r2v` all ship today.
- **Consistent subjects.** `gflow character create` mints a Flow Character (face and body reference) so the same person appears from one generation to the next.
- **Prompt tools.** `--tool creative-director` rewrites a terse prompt into a vivid one (Google's 5-component formula) before generating — on any command. Bring your own with [My Tools](docs/TOOLS.md).
- **Pipelines.** Wire Veo into your content automation, AI-video stack, or batch experiments.
- **Terminal-native.** After one `gflow auth login`, you stay in the shell. No clicking through dialogs.

Same Veo and Imagen models, same quality, same Ultra/Pro billing, now programmatic.

## 60-second quick start

```bash
# 1 · Install (uv recommended; also: pip install gflow-cli)
uv tool install gflow-cli
uv tool run --from gflow-cli playwright install chromium     # one-time, ~150 MB

# 2 · Authenticate (one-time, opens a real Chrome window)
gflow auth login --browser chrome

# 3 · Generate
gflow image t2i "a hot air balloon over Tokyo at sunrise"
# or:
gflow video t2v "Slow cinematic push-in on a sunlit forest clearing" --aspect 16:9
# or mint a reusable Character (face + body reference):
gflow character create --project <id> --name "Aria" --face-prompt "..." --body-prompt "..."
```

Outputs land under `$GFLOW_CLI_OUTPUT_DIR`, or you can route them to S3, MinIO, or Google Cloud Storage with [`GFLOW_CLI_STORAGE_URI`](docs/EXTERNAL_STORAGE.md). The first call takes 30 to 90 seconds while Chromium warms up; later calls reuse the warm session.

> **Why `--browser chrome`?** Google rejects Playwright's bundled Chromium. The CLI fails fast with a friendly error (`AuthBrowserRejectedError`, exit code 14) if you pick anything else.

For the full 10-minute walkthrough with troubleshooting and multi-account setup, see **[USER_GUIDE: Journey 1](docs/USER_GUIDE.md#journey-1--first-time-setup-10-minutes)**.

## Examples

One command in, real Flow output back. Left: `gflow image t2i` generating a photorealistic scene in your library. Right: a frame-to-frame transform.

![gflow-cli examples: text-to-image generation, and a before/after frame transform](https://raw.githubusercontent.com/ffroliva/gflow-cli/main/docs/assets/examples.webp)

## Demo

![gflow image t2i runs a single 9:16 prompt, streams structlog output, and writes a PNG to disk](https://raw.githubusercontent.com/ffroliva/gflow-cli/main/docs/assets/example-run.gif)

A single `gflow image t2i "..." --aspect 9:16 --model nano2` call against a logged-in Pro/Ultra profile. The terminal streams the run's `structlog` JSON, then lists the written PNG. Chromium drives the Flow editor silently in the background.

Reproduce the recording with [`scripts/record_demo.ps1`](scripts/record_demo.ps1) (Windows, OBS, ffmpeg, gifski). More formats, including the side-by-side split-screen: **[docs/DEMOS.md](docs/DEMOS.md)**.

## Documentation

[**docs/INDEX.md**](docs/INDEX.md) is the master routing layer. Quick links:

| Topic | Read |
|---|---|
| 🎯 **Getting started** | [User Guide](docs/USER_GUIDE.md) · [Usage](docs/USAGE.md) · [Configuration](docs/CONFIGURATION.md) |
| **Storage & catalog** | [External Storage](docs/EXTERNAL_STORAGE.md) · [Data Layer](docs/DATA_LAYER.md) |
| 🎭 **Characters** | [Characters](docs/CHARACTER.md), reusable subjects (`gflow character`) |
| 🤖 **Agentic & automation** | [Instructions](docs/INSTRUCTIONS.md) (`gflow instructions`, persistent brief cards) · [Movie](docs/MOVIE.md) (`gflow movie`, multi-scene manifests) · [Tools](docs/TOOLS.md) (`--tool`, prompt rewriting) · [MCP server](docs/MCP.md) (`gflow mcp run` / `gflow serve`) |
| 🔐 **Auth & sessions** | [Authentication](docs/AUTHENTICATION.md) · [Known issues](KNOWN_ISSUES.md) |
| 🏗️ **Internals** | [Architecture](docs/ARCHITECTURE.md) · [Security](docs/SECURITY.md) · [Debugging](docs/DEBUGGING.md) |
| 📦 **Releases** | [Changelog](CHANGELOG.md) · [Roadmap](ROADMAP.md) · [Release protocol](RELEASE.md) · [Project status](docs/PROJECT_STATUS.md) |
| 🤝 **Contributing** | [Contributing](CONTRIBUTING.md) · [Development](docs/DEVELOPMENT.md) · [GitHub workflow](docs/GITHUB.md) |

## For AI agents & LLMs

gflow-cli ships four agent entry points. Pick the one your tool reads first.

| File | Audience | Tools |
|---|---|---|
| [**AGENTS.md**](AGENTS.md) | Universal coding-agent spec | Cursor · Codex · Aider · Gemini CLI · Jules · Devin · Windsurf · Zed · Warp · opencode · Copilot |
| [**CLAUDE.md**](CLAUDE.md) | Claude Code's auto-loaded memory | Claude Code |
| [**llms.txt**](llms.txt) | LLM-readable summary (llmstxt.org format) | Paste into ChatGPT, Claude, or Gemini to onboard the model |
| [`skills/gflow-cli/SKILL.md`](skills/gflow-cli/SKILL.md) | Claude Code Skill | Symlink into `~/.claude/skills/` |

Onboard any agent in one line. Paste this into your agent of choice:

> *"Read [AGENTS.md](https://github.com/ffroliva/gflow-cli/blob/main/AGENTS.md) and [docs/INDEX.md](https://github.com/ffroliva/gflow-cli/blob/main/docs/INDEX.md), then help me with my Flow batch."*

## Architecture & current limitations

```text
gflow CLI  →  Provider (interchangeable)  →  Flow (ui_automation) / Mock (tests) / [planned: Official Veo]
                                              ↓
                                      Playwright Chromium (headed login, headless after)
                                              ↓
                              aisandbox-pa.googleapis.com  (Google's private Flow API)
```

**Current transport:** `ui_automation` drives Flow through a persistent Playwright Chromium profile. It is production-stable and verified end-to-end every release (see the per-release `LIVE_VERIFICATION_*` evidence files).

**What's blocked:** a pure HTTP transport for video generation. The video upload endpoint returns HTTP 401 under non-Chrome browsers plus a reCAPTCHA mint we cannot reproduce headlessly. Three earlier HTTP strategies (`evaluate_fetch`, `bearer`, `sapisidhash`) live under `src/gflow_cli/api/transports/experimental/` for research, off the production path.

**How you can help:** if you have driven `aisandbox-pa.googleapis.com` from outside a real Chrome session, or you understand Google's anti-bot stack here, please open an issue. A working REST transport would unlock serverless deployments, true horizontal concurrency, and roughly 10x the project's reach. Details: [docs/ARCHITECTURE.md § Headed-browser dependency](docs/ARCHITECTURE.md#headed-browser-dependency--current-limitation).

## Project status

**Alpha.** Image (t2i, i2i, upload, upscale, batch) and video (t2v, i2v, r2v, batch, chain) run end-to-end on `ui_automation`, with a 5-model Veo picker plus `--duration` and `--count`. Beyond single generations: `gflow movie` renders multi-scene manifests, `gflow instructions` manages persistent Agent-Mode brief cards (credits-free), `gflow character` handles reusable subjects, `gflow scene` does credit-free server-side stitching, `--tool` applies prompt-rewriting tools, and an MCP server (`gflow mcp run` stdio / `gflow serve` HTTP-SSE) exposes the core surface to AI agents with a CI-enforced CLI↔MCP parity contract.

Full milestone history lives in [CHANGELOG.md](CHANGELOG.md). Where the project is heading: [ROADMAP.md](ROADMAP.md).

## License & legal

[MIT License](LICENSE) © 2026 Flavio Oliva (`ffroliva`). The MIT license covers `gflow-cli`'s code only. It grants no rights to Flow, Veo model output, or any Google service. Google's own terms (Labs Additional Terms, Ultra/Pro subscription terms) govern your generations. See the [DISCLAIMER](DISCLAIMER.md).

## Acknowledgements

- [`edge-tts`](https://github.com/rany2/edge-tts), design inspiration for community SDKs over private cloud APIs.
- [`googleapis/python-genai`](https://github.com/googleapis/python-genai), the official Veo SDK that a future provider release may alias.
- [Keysight, *Google Labs – Flow AI with Veo3: A Network Traffic Analysis*](https://www.keysight.com/blogs/en/tech/nwvs/2025/08/04/google-flow-ai-har-analysis), an independent capture that helped validate the route patterns.

---

## Stats

[![GitHub stars](https://img.shields.io/github/stars/ffroliva/gflow-cli?style=social&cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/ffroliva/gflow-cli?style=social&cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli/network/members)
[![GitHub watchers](https://img.shields.io/github/watchers/ffroliva/gflow-cli?style=social&cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli/watchers)
[![GitHub issues](https://img.shields.io/github/issues/ffroliva/gflow-cli?cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli/issues)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/ffroliva/gflow-cli?cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli/pulls)
[![GitHub last commit](https://img.shields.io/github/last-commit/ffroliva/gflow-cli?cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli/commits/main)
[![GitHub repo size](https://img.shields.io/github/repo-size/ffroliva/gflow-cli?cacheSeconds=3600)](https://github.com/ffroliva/gflow-cli)
[![PyPI downloads](https://static.pepy.tech/badge/gflow-cli/month)](https://pepy.tech/project/gflow-cli)

If `gflow-cli` saves you time, please ⭐ the repo. It is the cheapest way to support the project.
