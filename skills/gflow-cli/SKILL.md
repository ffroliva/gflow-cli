---
name: gflow-cli
version: "1.1"
skillopt_epoch: 0
description: Use when the user wants to drive Google Flow (Veo image-to-video, Veo text-to-video, Imagen / Nano Banana image generation) from the terminal or a script — including text-to-video, image-to-video, image-to-image, batch video pipelines, or burning Flow Ultra/Pro credits programmatically. The CLI is `gflow` (or `flow`); install with `uv tool install gflow-cli` or run ad-hoc with `uvx --from gflow-cli gflow ...`. Bypasses the web UI entirely after a one-time browser sign-in.
optimization_notes: |
  Known weak spots for the SkillOpt training loop (targets for epoch 1+):
  - Wrong subcommand: agents emit 'gflow video generate' / 'gflow video create' instead of 'gflow video t2v' or 'gflow video i2v'
  - Wrong output flag: '--output PATH' instead of '-o PATH'
  - Prerequisite gap: Playwright Chromium install step skipped on fresh machines
  - Profile parallelism anti-pattern: two generations launched on the same profile in parallel (crashes Chromium)
  - reCAPTCHA direction inverted: agents suggest GFLOW_CLI_HEADLESS=true to fix detection; correct fix is =false
  - UUID reuse: agents call 'gflow image upload' again for an already-uploaded UUID instead of passing it directly to --ref
  - Model alias confusion: '--model imagen' / '--model quality' instead of '--model image4' / '--model nano2'
  - Auth recovery: agents suggest 'gflow auth refresh' / 'gflow auth renew' which do not exist; correct command is 'gflow auth login'
---

# gflow-cli skill

`gflow-cli` is an unofficial Python CLI that drives [Google Flow](https://labs.google/fx/tools/flow) — Veo (T2V/I2V) and Imagen / Nano Banana — from the terminal, bypassing the web UI. Source: <https://github.com/ffroliva/gflow-cli>. Canonical command reference: [`docs/USAGE.md`](https://github.com/ffroliva/gflow-cli/blob/main/docs/USAGE.md).

## When to invoke this skill

The user wants to:

- Generate one or many Veo videos from text prompts (T2V) or from initial frame + motion prompt (I2V)
- Generate one or many Imagen / Nano Banana images from text (T2I) or from prompt + reference images (I2I)
- Build a batch pipeline for video generations
- Create a reusable, project-scoped Flow **Character** (a named subject with reference images, optional voice + personality) for consistent subjects across generations (`gflow character`)
- Compose ordered clips into a **scene** and optionally render a credit-free server-side extended video (`gflow scene`)
- Stitch a multi-clip story where each clip is seeded by the previous clip's last frame (`gflow video chain`)
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
gflow video t2v "<prompt>" [--out-dir DIR] [--aspect ...] [--seed N]
gflow video i2v --initial-frame <image> "<prompt>" [--out-dir DIR] [...same as t2v]
gflow video batch <manifest.tsv> [--out-dir DIR]
gflow video chain <manifest.jsonl> [--out-dir DIR] [--dry-run] \
                  [--max-links N] [--resume-from N]   # last-frame I2V chaining; veo models only

# Characters (reusable, project-scoped subjects)
gflow character create --project <id> --name "<name>" --face-prompt "<prompt>" \
                       [--body-prompt "<prompt>"] [--voice <id>] [--personality "<text>"] \
                       [--model {nano2|nanopro}]
gflow character list --project <id>
gflow character show <character-id> --project <id>
gflow character rm --project <id> (--id <character-id> | --name "<name>") [--yes]   # delete (FREE)
gflow character voices                                    # list the Gemini voice catalog

# Scenes (Add Clip / compose ordered clips)
gflow scene create --project <id> <clip-id> [<clip-id> ...] \
                   [-o extended.mp4]                       # --output = credit-free server-side concat
gflow scene show <scene-id> --project <id>
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

### Single clip from initial frame

```bash
gflow video i2v --initial-frame ./input.png "Slow cinematic push-in, soft golden light at sunset" --out-dir outputs
```

### Batch from a directory of inputs (bash)

```bash
mkdir -p out
for img in ./inputs/*.png; do
  name=$(basename "$img" .png)
  gflow video i2v --initial-frame "$img" "Cinematic push-in" --out-dir out
done
```

### Batch via a TSV manifest

```bash
# manifest.tsv columns: initial_frame \t prompt \t end_frame? \t aspect? \t output_path?
# Empty initial_frame = T2V; lines starting with `# ` are comments.
gflow video batch ./manifest.tsv --out-dir ./out/
```

### Create a reusable Character for consistent subjects

```bash
# A Character is a named, project-scoped subject reused across generations.
gflow character create --project "$PROJECT_ID" --name "Joaquim" \
  --face-prompt "weathered fisherman, grey beard, kind eyes" \
  --body-prompt "tall, broad-shouldered, wearing a navy wool sweater" \
  --voice <voice-id> --model nano2
gflow character voices            # discover valid --voice ids first
gflow character list --project "$PROJECT_ID"
```

See [`docs/CHARACTER.md`](https://github.com/ffroliva/gflow-cli/blob/main/docs/CHARACTER.md) for the full domain model, wire protocol, and the crash-recoverable persist-before-spend saga.

### Compose clips into an extended video (credit-free)

```bash
# Concatenate ordered clips server-side via runVideoFxConcatenation — no local ffmpeg, no credits.
gflow scene create --project "$PROJECT_ID" "$CLIP_A" "$CLIP_B" -o extended.mp4
```

### Chain clips by last frame (story stitching)

```bash
# manifest.jsonl: one JSON object per line. Link 0 = t2v; later links = i2v seeded by the
# previous clip's last frame. One credit per link. veo models only.
gflow video chain ./story.jsonl --out-dir ./out/ --dry-run   # preview the plan first
gflow video chain ./story.jsonl --out-dir ./out/             # then run for real
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

## Known agent failure modes

Documented errors agents commonly make — negative examples for the SkillOpt training loop:

| Mistake | Correct behaviour |
|---|---|
| `gflow video generate` or `gflow video create` | `gflow video t2v` (text→video) or `gflow video i2v` (image→video) |
| `--output PATH` on any video/image command | `-o PATH` (short flag) or `--out DIR` (image output dir) |
| `gflow auth` bare or `gflow login` to sign in | `gflow auth login` — bare `gflow auth` only lists profiles |
| `gflow auth refresh` / `gflow auth renew` (don't exist) | `gflow auth login` to refresh a stale or expired session |
| `playwright install` or `playwright install --all` | `uvx --from gflow-cli playwright install chromium` (Chromium only, ~150 MB) |
| Running two generations on the same `--profile` in parallel | Use different `--profile` names — Chromium refuses two persistent contexts on the same dir |
| `GFLOW_CLI_HEADLESS=true` to fix reCAPTCHA failures | `GFLOW_CLI_HEADLESS=false` — headless mode *causes* bot-detection, not prevents it |
| Calling `gflow image upload` again for an already-uploaded UUID | Pass the UUID directly to `--ref UUID` — no re-upload needed |
| `--model imagen` / `--model quality` / `--model high` | `--model image4` (Imagen 3.5), `--model nano-pro` (Gem Pix 2), `--model nano2` (Narwhal) |
| Python: `client = FlowApiClient(...)` then method calls | Must use `async with FlowApiClient(...) as client:` — it's an async context manager |
| Python: `from gflow_cli import FlowApiClient` | `from gflow_cli.api.client import FlowApiClient` |
| Piping `gflow video t2v` output into a shell loop for batch | Use `gflow video batch manifest.tsv` — the CLI has a native batch runner |

## Disclaimer

gflow-cli is **not affiliated with Google**. Reverse-engineered, unofficial; may break when Google changes Flow's private API. Read the [DISCLAIMER](https://github.com/ffroliva/gflow-cli/blob/main/DISCLAIMER.md) before deploying in any sensitive setting.
