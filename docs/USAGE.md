# Usage

CLI command reference. For environment variables see [CONFIGURATION](CONFIGURATION.md). For auth see [AUTHENTICATION](AUTHENTICATION.md).

> ⚠️ **Status.** `gflow video` commands are fully wired as of v0.2.0a1. `gflow image` commands (`upload`, `t2i`, `i2i`) are wired as of v0.3.0a1.

## Synopsis

```text
gflow [OPTIONS] COMMAND [ARGS]...

Commands:
  auth      Manage Google sessions for Flow.
    (no args)                   Show profile list, or trigger first login.
    login                       One-time interactive sign-in.
    status                      Show whether a profile has a saved session.
    list                        List every profile and indicate the default.
    use NAME                    Set NAME as the default profile.
    logout                      Delete a profile's saved session (asks first).

  image     Image generation (Imagen / Nano Banana via Flow).
    upload                      Upload a local image and print its asset UUID.
    t2i                         Generate 1-4 images from a text prompt.
    i2i                         Generate 1-4 images from a prompt + reference(s).

  video     Video generation (Veo via Flow).
    t2v                         Generate a video from a text prompt.
    i2v                         Generate a video from a start image + text prompt.
    batch                       Run a TSV manifest of video generations.
```

Global flags:

- `-V`, `--version` — print version and exit.
- `-v`, `--verbose` — log at DEBUG level.

Note: `--profile NAME` is **per-subcommand**, not global — pass it after the subcommand name (e.g. `gflow image t2i "..." --profile experiments`, not `gflow --profile experiments image t2i ...`).

## `gflow auth`

See [AUTHENTICATION § Commands](AUTHENTICATION.md#commands).

## `gflow image upload`

Upload a local PNG/JPEG/WebP/GIF into a fresh Flow project and print the asset UUID + dimensions Flow inferred. The UUID is what later subcommands (`gflow image i2i --ref UUID`, `gflow video i2v`) accept as a starting frame.

```text
gflow image upload PATH [OPTIONS]

Arguments:
  PATH                      Local image file (PNG, JPEG, WebP, or GIF). [required]

Options:
  --profile NAME            Profile name (overrides default).
```

The uploader **validates the file's magic bytes** (PNG `\x89PNG`, JPEG `\xff\xd8\xff`, WebP `RIFF...WEBP`, or GIF87a/89a) before calling Flow — anything else is rejected client-side. There is also a hard **20 MB size cap** to match Flow's documented per-file limit; oversize files fail fast without burning a network round-trip.

**Examples:**

```bash
# Upload and read the printed UUID
gflow image upload hero.png

# Pick a profile explicitly
gflow image upload ./shots/01.jpg --profile experiments
```

Output (truncated):

```text
Asset UUID: ddb6ef97-262d-49f4-8269-4a28c0fae6a2
Dimensions: 1024 x 1024  Project: <project-id>
```

Capture the UUID into a shell variable to chain into `i2i` or `video i2v`:

```bash
UUID=$(gflow image upload hero.png | awk '/Asset UUID:/ {print $3}')
gflow image i2i "make it cinematic" --ref "$UUID"
```

## `gflow image t2i`

Generate 1–4 images from a text prompt using Google Flow's Imagen / Nano Banana models.

```text
gflow image t2i PROMPT [OPTIONS]

Arguments:
  PROMPT                    Text prompt.                           [required]

Options:
  --model [nano2|nano-pro|image4]
                            Image model alias.                [default: nano2]
  --aspect [9:16|16:9|1:1|4:3|3:4]
                            Aspect ratio.                     [default: 9:16]
  -n, --count INTEGER       How many images to generate (1-4).  [default: 1]
  --seed INTEGER            RNG seed (only valid when -n 1).
  --out PATH                Output directory (see "Output paths" below).
  --profile NAME            Profile name (overrides default).
```

**Models:**

| Alias | Backing model | Notes |
|---|---|---|
| `nano2` | Nano Banana 2 (`NARWHAL`) | Default. Fast, balanced quality. |
| `nano-pro` | Nano Banana Pro (`GEM_PIX_2`) | Higher quality, slower. |
| `image4` | Imagen 4 (`IMAGEN_3_5`) | Photoreal-leaning Imagen variant. |

**Seed-requires-count==1 invariant.** `--seed` is only valid when generating a single image (`-n 1`). For multi-image runs the CLI rejects the combination upfront — multi-image fan-out uses N independent random seeds (one per shot) wired to a shared `batch_id`, which gives you variation. If you need reproducibility across many shots, run `--seed` once per call in a loop.

**Output paths.**

- **Default (`--out` omitted).** Files land under `$FLOW_CLI_OUTPUT_DIR/images/<YYYY-MM-DD>/<media_name>_<n>.png`. The date partition keeps long-running batches navigable.
- **`--out DIR` provided.** Files are written **flat** as `<DIR>/<media_name>_<n>.png` — no date subdirectory. `--out` must be a directory; flat-file output paths are not supported (you can rename after the fact).

**Examples:**

```bash
# Single image, default model + 9:16 aspect
gflow image t2i "a serene mountain lake at dawn"

# 16:9 with the higher-quality model
gflow image t2i "neon cyberpunk alley" --model nano-pro --aspect 16:9

# 4 variations of a logo at 1:1, written flat into ./logos/
gflow image t2i "variations of a minimalist fox logo" -n 4 --aspect 1:1 --out ./logos

# Reproducible single shot
gflow image t2i "reproducible reference shot" --seed 42
```

A 4-image run with `--out ./logos/` produces:

```text
./logos/<media_name>_1.png
./logos/<media_name>_2.png
./logos/<media_name>_3.png
./logos/<media_name>_4.png
```

(`<media_name>` is the per-image UUID Flow assigns; the `_<n>` suffix is the 1-based index in the batch.)

## `gflow image i2i`

Generate 1–4 images by blending a text prompt with one or more reference images. Same flag set as `t2i`, plus a required `--ref` (repeatable).

```text
gflow image i2i PROMPT --ref PATH_OR_UUID [--ref ...] [OPTIONS]

Arguments:
  PROMPT                    Text prompt.                           [required]

Options:
  --ref PATH_OR_UUID        Reference image. Repeat for multiple. [required]
  --model [nano2|nano-pro|image4]
                            Image model alias.                [default: nano2]
  --aspect [9:16|16:9|1:1|4:3|3:4]
                            Aspect ratio.                     [default: 9:16]
  -n, --count INTEGER       How many images to generate (1-4).  [default: 1]
  --seed INTEGER            RNG seed (only valid when -n 1).
  --out PATH                Output directory (same semantics as t2i).
  --profile NAME            Profile name (overrides default).
```

**Path-or-UUID semantics.** Each `--ref` value is classified at the CLI boundary:

- **Looks like a Flow asset UUID** (case-insensitive 8-4-4-4-12 hex, e.g. `ddb6ef97-262d-49f4-8269-4a28c0fae6a2`) → passed through verbatim. No upload, no extra round-trip.
- **Anything else** → treated as a local path. The CLI canonicalises it (resolving symlinks once at validation time, closing the symlink-laundering vector where `./hero.png -> ~/.ssh/id_rsa` could exfiltrate secrets), uploads it, then uses the resulting UUID.

Mix and match in a single call. UUIDs and paths can co-exist on the same command line; order is preserved so `imageInputs[]` matches the order you typed.

**Examples:**

```bash
# Single ref by path (auto-uploaded)
gflow image i2i "make it cinematic, golden hour" --ref hero.png

# Two refs, both by path
gflow image i2i "blend these two compositions" --ref a.png --ref b.png

# Already-uploaded asset by UUID
gflow image i2i "stylize this asset" --ref ddb6ef97-262d-49f4-8269-4a28c0fae6a2

# Mix: one path, one UUID
gflow image i2i "mix references" --ref hero.png --ref ddb6ef97-262d-49f4-8269-4a28c0fae6a2

# 4-image fan-out from one ref, written flat
gflow image i2i "4 variants of this scene" --ref hero.png -n 4 --out ./variants
```

A 2-image run produces files numbered `_1.png`, `_2.png`:

```text
./variants/<media_name_a>_1.png
./variants/<media_name_b>_2.png
```

## `gflow video t2v`

Generate a video from a text prompt only.

```text
gflow video t2v PROMPT [OPTIONS]

Options:
  -o, --output PATH       Output mp4. Default: $FLOW_CLI_OUTPUT_DIR/videos/<date>/<media>.mp4
  --aspect 9:16|16:9|1:1  Default: 9:16
  --seed INTEGER          Reproducibility. Default: random.
  --profile NAME          Account profile. Default: resolved from env/config.
  --poll-interval FLOAT   Seconds between status polls. Default: 5.
```

Examples:

```bash
gflow video t2v "Slow cinematic push-in toward a candle flame"
gflow video t2v "Aerial shot of a coastline at sunset" --aspect 16:9 -o ./coast.mp4
```

## `gflow video i2v`

Generate a video from a START IMAGE + text prompt. The image must be a local PNG, JPEG, WebP, or GIF; it's uploaded once per call and the resulting clip animates from it according to PROMPT.

```text
gflow video i2v IMAGE PROMPT [OPTIONS]

Arguments:
  IMAGE                   Local start image (PNG/JPEG/WebP/GIF). [required]
  PROMPT                  Text prompt.                            [required]

Options:
  -o, --output PATH       Output mp4. Default: $FLOW_CLI_OUTPUT_DIR/videos/<date>/<media>.mp4
  --aspect 9:16|16:9|1:1  Default: 9:16
  --seed INTEGER          Reproducibility. Default: random.
  --profile NAME          Account profile. Default: resolved from env/config.
  --poll-interval FLOAT   Seconds between status polls. Default: 5.
```

```bash
gflow video i2v ./hero.png "Slow camera arc, soft golden light"
```

## `gflow video batch`

Run a TSV manifest of generations against ONE shared project.

```text
gflow video batch MANIFEST [--out-dir DIR] [--profile NAME] [--poll-interval SEC]
```

Manifest format (tab-separated; `# `-prefixed lines are comments):

```tsv
# start_image	prompt	end_image	aspect	output_path
	A serene mountain lake at sunset		9:16	./out/lake.mp4
hero.png	Slow camera arc		9:16	./out/hero.mp4
	Aerial coastline		16:9	./out/coast.mp4
```

| Column | Required | Default |
|---|---|---|
| `start_image` | no (empty -> T2V) | - |
| `prompt` | **yes** | - |
| `end_image` | no (reserved, not yet wired) | - |
| `aspect` | no | `9:16` |
| `output_path` | no | `<out_dir>/videos/<date>/<media>.mp4` |

## Recipes

### Burn through a directory of inputs

```bash
mkdir -p out
for img in ./inputs/*.png; do
  name=$(basename "$img" .png)
  gflow video i2v "$img" "Cinematic push-in" -o "out/${name}.mp4"
done
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force -Path out | Out-Null
Get-ChildItem ./inputs/*.png | ForEach-Object {
    gflow video i2v $_.FullName "Cinematic push-in" -o "out/$($_.BaseName).mp4"
}
```

### Fan out an image prompt 4-way

```bash
gflow image t2i "variations of a minimalist fox logo" -n 4 --aspect 1:1 --out ./logos/
```

### Run two profiles concurrently

```bash
# Terminal 1
gflow video batch ./batch-a.tsv --profile work

# Terminal 2 (different profile = different Chromium context = OK)
gflow video batch ./batch-b.tsv --profile personal
```

(Same profile concurrently → second invocation fails with "Chromium profile locked". Use different profiles or wait.)

### JSON logs for piping into Loki/Datadog

```bash
FLOW_CLI_LOG_FORMAT=json gflow image t2i "..." 2>&1 | jq .
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Generic error (with stderr message) |
| `2` | Bad usage / missing required argument / auth/profile error |
| `64..125` | Reserved for future granular categorisation (rate-limit, provider-unavailable, etc.) |

Use them to branch in shell scripts:

```bash
if ! gflow video i2v ./in.png "test" -o out.mp4; then
  case $? in
    2) echo "Bad usage or auth issue"; gflow auth status; exit 1 ;;
    *) echo "Unknown failure"; exit 1 ;;
  esac
fi
```

## Programmatic use

The CLI is a thin shell over `flow_cli.api.client.FlowApiClient`. All public methods used by the commands above are also available directly:

```python
import asyncio
from pathlib import Path
from flow_cli.api.client import FlowApiClient
from flow_cli.paths import profile_dir

async def main() -> None:
    async with FlowApiClient(profile_dir=profile_dir("default")) as client:
        project = await client.create_project(title="archive demo")
        # ... do work in this project ...
        # When you no longer need it, archive to keep the library tidy:
        await client.archive_workflow(workflow_id=project.workflow_id, project_id=project.project_id)

asyncio.run(main())
```

`FlowApiClient.archive_workflow(workflow_id, project_id)` issues `PATCH /v1/flowWorkflows/{id}` to soft-delete a workflow. Useful in batch scripts that spin up a project per call and want to clean up afterwards.

## See also

- [CONFIGURATION](CONFIGURATION.md) — env vars, output paths, defaults
- [AUTHENTICATION](AUTHENTICATION.md) — auth flow + multi-account
- [ARCHITECTURE](ARCHITECTURE.md) — internal structure (for contributors)
- [PLAN](../PLAN.md) — what ships in which phase
