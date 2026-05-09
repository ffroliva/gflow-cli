---
name: gflow-cli
description: Use when the user wants to generate Veo videos via Google Flow from the terminal or a script — including image-to-video (I2V) clips, batch video pipelines, or burning Flow Ultra/Pro credits programmatically. The CLI is `gflow` (or `flow`); install with `uv tool install gflow-cli` or run ad-hoc with `uvx --from gflow-cli gflow ...`. Bypasses the web UI entirely after a one-time browser sign-in.
---

# gflow-cli skill

`gflow-cli` is an unofficial Python CLI that drives [Google Flow](https://labs.google/fx/tools/flow) Veo image-to-video generations from the terminal, bypassing the web UI. Source: <https://github.com/ffroliva/gflow-cli>.

## When to invoke this skill

The user wants to:

- Generate one or many Veo videos from images and motion prompts
- Build a batch pipeline that creates dozens of clips
- Use their Google AI Ultra or Pro Flow credits via script instead of clicking through the UI
- Automate Flow inside a content pipeline, AI video production stack, or research project

**Do NOT use this skill** when:

- The user wants to generate text-to-video (Flow's primary surface is I2V; pure T2V belongs to the official [Gemini Veo API](https://ai.google.dev/gemini-api/docs/video)).
- The user asks about anything other than video generation (audio, image gen, music, etc. — wrong tool).
- The user wants production-grade reliability with SLAs — recommend the [official Gen AI SDK](https://github.com/googleapis/python-genai) instead.

## Prerequisites

Before any gflow-cli invocation, verify:

1. **Python 3.11+** is available (`python --version`).
2. **uv** is installed (`uv --version`). If not, install: `curl -LsSf https://astral.sh/uv/install.sh | sh` (or Windows equivalent from <https://docs.astral.sh/uv/>).
3. **gflow-cli** is installed OR available via uvx:
   - Quick: `uvx --from gflow-cli gflow --help` (no install)
   - Persistent: `uv tool install gflow-cli && gflow --help`
4. **Playwright Chromium** has been downloaded once: `uvx --from gflow-cli playwright install chromium` (~150 MB).
5. **A signed-in profile** exists: `gflow auth status` should show `Profile 'default' is configured`. If not, run `gflow auth login` and walk the user through the one-time browser sign-in.
6. **The user has Flow access** — Google AI Ultra or Pro subscription with Flow rolled out. If `gflow upload` returns 403, this is the cause.

## Core commands

```bash
# Auth (one-time)
gflow auth login                   # opens Chromium, user signs in
gflow auth status                  # confirms session

# Atomic operations
gflow upload <image>               # → asset UUID printed to stdout
gflow generate -s <uuid> -p "<motion prompt>"
                                   # → job_id printed to stdout
gflow status <job_id>              # → "running" | "succeeded <url>" | "failed"
gflow download <job_id> -o out.mp4

# Convenience (does all four)
gflow i2v <image> "<motion prompt>" -o out.mp4
```

All commands accept `--profile <name>` to drive multiple Google accounts side-by-side.

## Recipes

### Single clip (most common)

```bash
gflow i2v ./input.png "Slow cinematic push-in, soft golden light at sunset" -o out.mp4
```

### Batch from a directory of inputs

```bash
mkdir -p out
for img in ./inputs/*.png; do
  name=$(basename "$img" .png)
  gflow i2v "$img" "Cinematic push-in" -o "out/${name}.mp4"
done
```

For PowerShell:

```powershell
New-Item -ItemType Directory -Force -Path out | Out-Null
Get-ChildItem ./inputs/*.png | ForEach-Object {
    gflow i2v $_.FullName "Cinematic push-in" -o "out/$($_.BaseName).mp4"
}
```

### Per-clip prompts from a manifest

```bash
# manifest.tsv: <image-path>\t<motion-prompt>\t<output-path>
while IFS=$'\t' read -r img prompt out; do
  gflow i2v "$img" "$prompt" -o "$out"
done < manifest.tsv
```

### Async pattern: kick off, poll later

```bash
ASSET=$(gflow upload ./input.png)
JOB=$(gflow generate -s "$ASSET" -p "Slow camera arc")

# ... do other work ...

while true; do
  STATUS=$(gflow status "$JOB")
  case "$STATUS" in
    succeeded*) gflow download "$JOB" -o out.mp4; break ;;
    failed*)    echo "$STATUS"; exit 1 ;;
    *)          sleep 5 ;;
  esac
done
```

### Use as a Python library

```python
import asyncio
from pathlib import Path
from flow_cli.providers.flow import FlowProvider
from flow_cli.auth import profile_dir
from flow_cli.models import GenerationRequest, JobStatus

async def make_clip(image: Path, prompt: str, out: Path):
    async with FlowProvider(profile_dir=profile_dir()) as p:
        await p.upload_image(image)
        job = await p.start_generation(GenerationRequest(
            start_image=image, motion_prompt=prompt, aspect="9:16",
        ))
        while job.status not in (JobStatus.SUCCEEDED, JobStatus.FAILED):
            await asyncio.sleep(5)
            job = await p.get_job(job.job_id)
        if job.status == JobStatus.SUCCEEDED:
            await p.download(job.output_url, out)

asyncio.run(make_clip(Path("in.png"), "Push-in", Path("out.mp4")))
```

## Common errors and fixes

| Error | Cause | Fix |
|---|---|---|
| `No session for profile 'default'` | First run, no auth | `gflow auth login` |
| `403 Forbidden` from upload | Account doesn't have Flow access | Verify in [labs.google/fx/tools/flow](https://labs.google/fx/tools/flow) |
| `RuntimeError: project.createProject not yet wired` | Pre-release stub still in place | Check `gflow --version`; install latest |
| `Playwright Executable doesn't exist` | Chromium not downloaded | `uvx --from gflow-cli playwright install chromium` |
| Generations all fail with the same UUID | Stale Flow session | `gflow auth login` again to refresh cookies |
| Quota exceeded | Burned through monthly credits | Wait for reset, or upgrade subscription |

## Important constraints

- **Costs real money / credits.** Each `gflow i2v` call burns a Veo credit from the user's Google AI Ultra/Pro subscription. Confirm before running batches.
- **Not for production-grade SLAs.** gflow-cli reverse-engineers a private Google API. It can break without notice. For production, use the [official Gen AI SDK](https://github.com/googleapis/python-genai).
- **Don't share auth profiles.** `~/.gflow-cli/profile_*` contains Google session cookies — treat as secrets.
- **Respect Google's [Generative AI Prohibited Use Policy](https://policies.google.com/terms/generative-ai/use-policy).** Don't generate content that would get the user's Google account banned.

## Disclaimer

gflow-cli is **not affiliated with Google**. Reverse-engineered, pre-release, may break. Read the [DISCLAIMER](https://github.com/ffroliva/gflow-cli/blob/main/DISCLAIMER.md) before deploying in any sensitive setting.
