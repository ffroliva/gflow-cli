# flow-cli

> **Unofficial, reverse-engineered Python CLI for Google Flow.**
> Drive [Google Flow](https://labs.google/fx/tools/flow) Veo image-to-video generations from your terminal — **without the browser**.

[![CI](https://github.com/ffroliva/flow-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/ffroliva/flow-cli/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#project-status)

> ⚠️ **Not affiliated with Google.** Reverse-engineered from public Flow web traffic for personal automation. Endpoints can change at any time. Use at your own risk.

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

This is the same pattern as [`edge-tts`](https://github.com/rany2/edge-tts) for Microsoft's Azure TTS: a community SDK over a private cloud API, used to power batch automation that the official UI was never meant for.

### Why this exists

| Problem with the Flow UI | How flow-cli solves it |
|---|---|
| Each generation requires headed Chromium (~50s startup) | Pure REST after one-time auth (~50ms per call) |
| DOM selectors break on UI updates | API surface is more stable than DOM |
| Sequential by design (one Chromium = one project) | Parallel-by-account, easy concurrency |
| No way to script multi-clip pipelines reliably | First-class CLI + Python library |
| Hard to integrate into CI/CD or workers | `pip install flow-cli` and you're done |

---

## Project status

**v0.1.0 — pre-release alpha.** Routes captured, scaffold in place, implementations being filled in. **Not yet usable end-to-end.** Roadmap milestones below.

| Milestone | Status |
|---|---|
| Repo scaffold, CI, license, README | ✅ done |
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

## Install

### From source (current — pre-release)

```bash
git clone git@github.com:ffroliva/flow-cli.git
cd flow-cli
uv sync
uv run gflow --help
```

### From PyPI (planned, v0.2+)

```bash
pip install flow-cli
# or
uv add flow-cli

gflow --help
```

After install, the binaries `gflow` (always) and `flow` (alias if no conflict on PATH) become available.

### Requirements

- Python 3.11+
- A Google account with **Flow access** (currently rolled out to AI Ultra/Pro subscribers)
- One-time browser sign-in (captured to `~/.flow-cli/profile_default/`)

---

## Quick start

```bash
# 1. Sign in once (opens a Chromium window, persists session locally)
gflow auth login

# 2. Verify session
gflow auth status

# 3. Generate a clip end-to-end
gflow i2v ./input.png "Slow cinematic push-in, soft golden light" -o out.mp4
```

That's it. The same call from Python:

```python
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
        # poll job.status, then p.download(job.output_url, Path("out.mp4"))
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
│(v0.1)    │ Veo    │       │(tests)│
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

The `Provider` interface keeps backends interchangeable. v0.1 ships `FlowProvider`. v0.3+ will add `OfficialVeoProvider` (uses [`googleapis/python-genai`](https://github.com/googleapis/python-genai) against `generativelanguage.googleapis.com`) — same code, swap with `--provider official`.

### Auth strategy

`flow-cli` doesn't reverse-engineer Google's OAuth flow. Instead it **piggybacks on Playwright's persistent context**: `gflow auth login` opens a Chromium window, you sign in normally, and the resulting cookie jar is saved to `~/.flow-cli/profile_default/`. Subsequent commands launch a **headless** Playwright context using that profile and call REST endpoints via Playwright's HTTP client — which auto-attaches the cookies. No tokens to refresh manually, no SSO scraping.

This means flow-cli depends on Playwright as a runtime, but never automates UI clicks. Auth is the only browser interaction, and it's a one-time event.

---

## Releases

`flow-cli` follows **[Semantic Versioning](https://semver.org/) 2.0.0** — breaking changes bump MAJOR, new features bump MINOR, fixes bump PATCH.

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
   - Publishes to PyPI (uses [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) — no API tokens stored in the repo)
   - Creates a GitHub Release with the changelog excerpt + built artifacts attached

### Pre-release channel

Tags matching `v*.*.*-rc*`, `v*.*.*-alpha*`, `v*.*.*-beta*` publish to PyPI under the same package as pre-releases. Install with `pip install --pre flow-cli` or pin a specific version.

### Yanking

If a published release breaks downstream users, yank it on PyPI immediately:

```bash
# Yanking does NOT delete — it just hides from `pip install` defaults.
# Existing users still see the version; new installs skip it.
```

---

## Contributing

Pre-1.0 the repo is private and managed by [@ffroliva](https://github.com/ffroliva). PRs and issues will open up at the v0.2 alpha milestone.

Until then, if you have access:

```bash
uv sync --extra dev
uv run ruff check src tests
uv run pyright src
uv run pytest -q
```

CI runs all three on every push and PR.

---

## License

[MIT](LICENSE) © 2026 Felipe Oliva (`ffroliva`).

This software is provided as-is. It interacts with a Google service whose terms you have already accepted. The author makes no representation about whether automated use of Flow is permitted by Google — please review the [Google Labs Additional Terms](https://policies.google.com/terms/generative-ai) before deploying this in production. If Google asks the project to take down or restrict any reverse-engineered surface, the maintainer will comply promptly.

## Acknowledgements

- [`edge-tts`](https://github.com/rany2/edge-tts) — design inspiration for community SDKs over private cloud APIs.
- [`googleapis/python-genai`](https://github.com/googleapis/python-genai) — the official Veo SDK that v0.3+ will alias.
- [Keysight Technologies — *Google Labs – Flow AI with Veo3: A Network Traffic Analysis*](https://www.keysight.com/blogs/en/tech/nwvs/2025/08/04/google-flow-ai-har-analysis) — independent traffic capture that helped validate the captured route patterns.
