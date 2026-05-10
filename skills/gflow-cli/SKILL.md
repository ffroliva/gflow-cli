---
name: gflow-cli
description: Use when the user wants to drive Google Flow (Veo image-to-video, Veo text-to-video, Imagen / Nano Banana image generation) from the terminal or a script — including text-to-video, image-to-video, image-to-image, batch video pipelines, or burning Flow Ultra/Pro credits programmatically. The CLI is `gflow` (or `flow`); install with `uv tool install gflow-cli` or run ad-hoc with `uvx --from gflow-cli gflow ...`. Bypasses the web UI entirely after a one-time browser sign-in.
---

# gflow-cli skill

`gflow-cli` is an unofficial Python CLI that drives [Google Flow](https://labs.google/fx/tools/flow) — Veo (T2V/I2V) and Imagen / Nano Banana — from the terminal, bypassing the web UI. Source: <https://github.com/ffroliva/gflow-cli>. Canonical command reference: [`docs/USAGE.md`](https://github.com/ffroliva/gflow-cli/blob/main/docs/USAGE.md).

## When to invoke this skill

The user wants to:

- Generate one or many Veo videos from text prompts (T2V) or from start-image + motion prompt (I2V)
- Generate one or many Imagen / Nano Banana images from text (T2I) or from prompt + reference images (I2I)
- Build a batch pipeline for video generations
- Use their Google AI Ultra or Pro Flow credits via script instead of clicking through the UI
- Automate Flow inside a content pipeline, AI video production stack, or research project

**Do NOT use this skill** when:

- The user wants production-grade reliability with SLAs — recommend the [official Gen AI SDK](https://github.com/googleapis/python-genai) instead.
- The user asks about audio, music, or anything outside Flow's video/image surface — wrong tool.

## Prerequisites

Before any gflow-cli invocation, verify:

1. **Python 3.11+** is available (`python --version`).
2. **uv** is installed (`uv --version`). If not, install: `curl -LsSf https://astral.sh/uv/install.sh | sh` (or Windows equivalent from <https://docs.astral.sh/uv/>).
3. **gflow-cli** is installed OR available via uvx:
   - Quick: `uvx --from gflow-cli gflow --help` (no install)
   - Persistent: `uv tool install gflow-cli && gflow --help`
4. **Playwright Chromium** has been downloaded once: `uvx --from gflow-cli playwright install chromium` (~150 MB).
5. **A signed-in profile** exists: `gflow auth status` should show `Profile 'default' is configured`. If not, run `gflow auth login` and walk the user through the one-time browser sign-in.
6. **The user has Flow access** — Google AI Ultra or Pro subscription with Flow rolled out. If `gflow image upload` returns 403, this is the cause.

## Core commands

```bash
# Auth (one-time)
gflow auth login                                          # opens Chromium, user signs in
gflow auth status                                         # confirms session
gflow auth                                                # bare: list profiles or trigger first login
gflow auth logout                                         # delete a saved session

# Image generation (Imagen / Nano Banana)
gflow image upload <path>                                 # → asset UUID + dimensions
gflow image t2i "<prompt>" [--model {nano2|nano-pro|image4}] \
                            [--aspect {9:16|16:9|1:1|4:3|3:4}] \
                            [-n 1..4] [--seed N] [--out DIR]
gflow image i2i "<prompt>" --ref PATH_OR_UUID [--ref ...] [...same as t2i]

# Video generation (Veo 3.1)
gflow video t2v "<prompt>" [-o out.mp4] [--aspect ...] [--seed N]
gflow video i2v <image> "<prompt>" [-o out.mp4] [...same as t2v]
gflow video batch <manifest.tsv> [--out-dir DIR]
```

Every subcommand accepts `--profile <name>` (per-subcommand, not global) to drive multiple Google accounts side-by-side.

## Recipes

### Single image (most common)

```bash
gflow image t2i "a hot air balloon over Tokyo at sunrise" --aspect 16:9
```

### Image fan-out (4 variants in parallel)

```bash
gflow image t2i "variations of a minimalist fox logo" -n 4 --aspect 1:1 --out ./logos/
```

### Image-to-image with a local reference

```bash
gflow image i2i "make it cinematic, golden hour" --ref hero.png
```

### Image-to-image with an already-uploaded asset UUID (no re-upload)

```bash
UUID=$(gflow image upload hero.png | awk '/Asset UUID:/ {print $3}')
gflow image i2i "stylize this asset" --ref "$UUID"
```

### Single clip from start image

```bash
gflow video i2v ./input.png "Slow cinematic push-in, soft golden light at sunset" -o out.mp4
```

### Batch from a directory of inputs (bash)

```bash
mkdir -p out
for img in ./inputs/*.png; do
  name=$(basename "$img" .png)
  gflow video i2v "$img" "Cinematic push-in" -o "out/${name}.mp4"
done
```

### Batch via a TSV manifest

```bash
# manifest.tsv columns: start_image \t prompt \t end_image? \t aspect? \t output_path?
# Empty start_image = T2V; lines starting with `# ` are comments.
gflow video batch ./manifest.tsv --out-dir ./out/
```

### Use as a Python library

```python
import asyncio
from pathlib import Path
from gflow_cli.api.client import FlowApiClient
from gflow_cli.paths import profile_dir

async def make_clip(image: Path, prompt: str, out: Path) -> None:
    async with FlowApiClient(profile_dir=profile_dir("default")) as client:
        project = await client.create_project(title="gflow-cli demo")
        asset = await client.upload_image(image, project.project_id)
        op = await client.generate_video(
            project_id=project.project_id,
            prompt=prompt,
            start_asset=asset,
            aspect="9:16",
        )
        # Poll op.workflow_id with client.poll_video_status(...) and
        # client.download_video(...) when status reaches succeeded.

asyncio.run(make_clip(Path("in.png"), "Push-in", Path("out.mp4")))
```

## Common errors and fixes

| Error | Cause | Fix |
|---|---|---|
| `No session for profile 'default'` | First run, no auth | `gflow auth login` |
| `403 Forbidden` from upload / generate | Account doesn't have Flow access | Verify in [labs.google/fx/tools/flow](https://labs.google/fx/tools/flow) |
| reCAPTCHA refuses to mint a token (headless detected) | Google bot-detection | Set `GFLOW_CLI_HEADLESS=false` and re-run; the visible window passes detection |
| `Playwright Executable doesn't exist` | Chromium not downloaded | `uvx --from gflow-cli playwright install chromium` |
| Generations all fail with the same UUID | Stale Flow session | `gflow auth login` again to refresh cookies |
| Quota exceeded | Burned through monthly credits | Wait for reset, or upgrade subscription |

## Important constraints

- **Costs real money / credits.** Each `gflow video t2v|i2v` and `gflow image t2i|i2i` call burns credits from the user's Google AI Ultra/Pro subscription. Confirm before running batches.
- **Not for production-grade SLAs.** gflow-cli reverse-engineers a private Google API. It can break without notice. For production, use the [official Gen AI SDK](https://github.com/googleapis/python-genai).
- **Don't share auth profiles.** The Playwright profile dir lives at the per-OS user-data location (Windows: `%LOCALAPPDATA%\gflow-cli\profile_*`; macOS: `~/Library/Application Support/gflow-cli/profile_*`; Linux: `~/.local/share/gflow-cli/profile_*`) and contains Google session cookies — treat as secrets.
- **Same profile can't run in parallel.** Chromium refuses two persistent contexts on the same profile dir; use different `--profile` names for parallel work.
- **Respect Google's [Generative AI Prohibited Use Policy](https://policies.google.com/terms/generative-ai/use-policy).** Don't generate content that would get the user's Google account banned.

## Disclaimer

gflow-cli is **not affiliated with Google**. Reverse-engineered, alpha-stage (v0.3.0a1), may break. Read the [DISCLAIMER](https://github.com/ffroliva/gflow-cli/blob/main/DISCLAIMER.md) before deploying in any sensitive setting.
