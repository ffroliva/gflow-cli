# Usage

CLI command reference. For environment variables see [CONFIGURATION](CONFIGURATION.md). For auth see [AUTHENTICATION](AUTHENTICATION.md).

> ⚠️ **Status.** `gflow video` commands are fully wired as of v0.2.0a1. `gflow image` commands (`upload`, `t2i`, `i2i`) are wired as of v0.3.0a1. **v0.4.0a2** added per-class exit codes (3–7) for shell branching, JSON-on-pipe structured logs (`GFLOW_CLI_LOG_FORMAT=json`), per-worker batch concurrency (`GFLOW_CLI_CONCURRENCY=N`), and tenacity-driven retry/backoff on transient failures.

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
    i2v                         Generate a video from a start (+ optional end) frame + prompt.
    r2v                         Generate a video from reference images + prompt.
    batch                       Run a TSV manifest of video generations.

  data      Local provenance database (read-only queries).
    media MEDIA_ID              Show stored record for a Flow media ID.
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

Generate 1–4 images from one text prompt, or run a shell-friendly batch of 1–50
prompts through one Flow session/project.

```text
gflow image t2i PROMPT [PROMPT ...] [OPTIONS]
gflow image t2i --prompts-file FILE [OPTIONS]
gflow image t2i --stdin [OPTIONS]

Arguments:
  PROMPT                    Text prompt. Repeat for multi-prompt mode.

Options:
  --prompts-file FILE       UTF-8 text file: one prompt per non-empty line;
                            whole-line # comments skipped.
  --stdin                   Read prompts from stdin using the same format.
  --continue-on-error /
  --fail-fast               Continue after per-prompt failures or stop at the
                            first failed prompt. [default: continue-on-error]
  --model [nano2|nano-pro|image4]
                            Image model alias.                [default: nano2]
  --aspect [9:16|16:9|1:1|4:3|3:4]
                            Aspect ratio.                     [default: 9:16]
  -n, --count INTEGER       How many images to generate (1-4).  [default: 1]
  --out PATH                Output directory (see "Output paths" below).
  --profile NAME            Profile name (overrides default).
```

**Models:**

| Alias | Backing model | Notes |
|---|---|---|
| `nano2` | Nano Banana 2 (`NARWHAL`) | Default. Fast, balanced quality. |
| `nano-pro` | Nano Banana Pro (`GEM_PIX_2`) | Higher quality, slower. |
| `image4` | Imagen 4 (`IMAGEN_3_5`) | Photoreal-leaning Imagen variant. |

**Multi-prompt shortcut.**

- Positional multi-prompt: `gflow image t2i "p1" "p2" "p3"`.
- `--prompts-file FILE`: UTF-8 text, one prompt per non-empty line, whole-line
  `#` comments skipped.
- `--stdin`: same format as `--prompts-file`.
- Sources are mutually exclusive.
- Output names use `prompt_<prompt-index>_<variation-index>.png`.
- With `-n 4`, each prompt produces four images; the maximum shell shortcut
  fan-out is 50 prompts * 4 = 200 images.
- `--continue-on-error` is default; `--fail-fast` stops after the first failed
  prompt.

**Output paths.**

- **Default (`--out` omitted).** Files land under `$GFLOW_CLI_OUTPUT_DIR/images/<YYYY-MM-DD>/<media_name>_<n>.png`. The date partition keeps long-running batches navigable.
- **`--out DIR` provided.** Files are written **flat** as `<DIR>/<media_name>_<n>.png` — no date subdirectory. `--out` must be a directory; flat-file output paths are not supported (you can rename after the fact).
- **Multi-prompt mode.** Files are written as `prompt_<prompt-index>_<variation-index>.png`; the prompt index and variation index are zero-based.

**Examples:**

```bash
# Single image, default model + 9:16 aspect
gflow image t2i "a serene mountain lake at dawn"

# 16:9 with the higher-quality model
gflow image t2i "neon cyberpunk alley" --model nano-pro --aspect 16:9

# 4 variations of a logo at 1:1, written flat into ./logos/
gflow image t2i "variations of a minimalist fox logo" -n 4 --aspect 1:1 --out ./logos

# Three prompts in one warm Flow session/project
gflow image t2i "p1" "p2" "p3" --aspect 16:9 --model image4

# Text file input: comments and blank lines are ignored
gflow image t2i --prompts-file prompts.txt --fail-fast

# Pipeline input
Get-Content prompts.txt | gflow image t2i --stdin
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

## `gflow image batch`

Generate multiple images from a single manifest file. The format is dispatched by file extension (`.json` or `.tsv`).

### TSV manifest

Tab-separated columns. Only `prompt` is required; remaining columns fall back to the CLI defaults.

```
prompt<TAB>count<TAB>aspect_ratio<TAB>model
```

Lines starting with `#` and blank lines are skipped. Example: [`test_assets/sample_batch.tsv`](../test_assets/sample_batch.tsv).

```tsv
a small calico kitten sitting on a windowsill
a watercolor sunset over rolling hills	2	16:9
an isometric pixel-art bakery	1	1:1	nano2
```

### JSON manifest

```json
[
  {"text": "a small calico kitten sitting on a windowsill"},
  {"text": "a watercolor sunset over rolling hills", "count": 2, "aspect_ratio": "16:9"},
  {"text": "an isometric pixel-art bakery", "count": 1, "aspect_ratio": "1:1", "model": "nano2"}
]
```

Example: [`test_assets/sample_batch.json`](../test_assets/sample_batch.json).

### Session behaviour

All prompts in a batch share one Flow project. The editor is opened once; each prompt is submitted in turn with a random 3–7 second pause between submissions. This jitter is a **submission-cadence anti-bot-detection measure** — it spaces out the submission clicks, not the generation wait. All generations run in parallel inside Flow; only the click timing is jittered. The command returns once every submitted generation has resolved (success or failure), not after the last click.

### Flags

- `--continue-on-error` / `--fail-fast` — keep going past row failures or stop at the first one (default: `--fail-fast`). On fail-fast, already-completed images are downloaded before the error is surfaced.

### Limits

- `MAX_BATCH_PROMPTS = 5` (defined in `src/gflow_cli/image_batch.py`). To raise, edit the constant.

### Exit codes

- `0` — all rows succeeded.
- `1` — invalid manifest (file not found, parse error, unknown aspect/model).
- non-zero (other) — transport-level failure.

### Observability

`gflow image batch` emits four structlog events per run, useful for debugging throttling regressions:

- `image_batch.submission_attempt {row_idx, prompt_hash, aspect, model, jitter_enabled, t_since_prev_submit_ms, project_id}`
- `image_batch.submission_result {row_idx, outcome, latency_ms, ...}`
- `image_batch.row_completed {row_idx, file_path, sha256_prefix}` (per image)
- `image_batch.inter_submission_latency_ms {row_idx, latency_ms}` (fires from row 1 onward)

> **Shared video flags** (`t2v` / `i2v` / `r2v`):
> `--model [omni-flash|veo-lite|veo-fast|veo-quality|veo-lite-lp]` (omit → Flow's
> current UI default), `--duration [4|6|8|10]` (10 requires `--model omni-flash`),
> `--count INTEGER` (1–4; >1 multiplies credit cost), `--aspect [9:16|16:9]`,
> `--profile NAME`, `--out-dir DIR` (default `tmp/`). The mp4 lands at
> `<out-dir>/<media_id>.mp4`.

## `gflow video t2v`

Generate a video from a text prompt only.

```text
gflow video t2v PROMPT [--model] [--duration] [--count] [--aspect] [--profile] [--out-dir]
```

```bash
gflow video t2v "Slow cinematic push-in toward a candle flame"
gflow video t2v "Aerial shot of a coastline at sunset" --aspect 16:9 --out-dir ./out
gflow video t2v "A neon city timelapse" --model omni-flash --duration 10 --count 2
```

## `gflow video i2v`

Generate a video from a START frame (+ optional END frame) and a motion prompt.
Each image is a local PNG/JPEG; it is bound into the editor's frame slot via the
media dialog, then Flow fires `batchAsyncGenerateVideoStartImage` (start only) or
`…StartAndEndImage` (start+end interpolation).

```text
gflow video i2v IMAGE PROMPT [--end-image LAST] [--model] [--duration] [--count] [--aspect] [...]

Arguments:
  IMAGE   Local start frame (PNG/JPEG). [required]
  PROMPT  Motion prompt.                [required]

Options:
  --end-image PATH  Optional end frame — Flow interpolates start -> end.
```

```bash
gflow video i2v ./hero.png "Slow camera arc, soft golden light"
gflow video i2v ./first.png "morph between scenes" --end-image ./last.png --model veo-quality
```

## `gflow video r2v`

Reference-to-video (Flow "ingredients"): condition a generation on reference
images. Per-model cap: `omni-flash` ≤7, the `veo-*` models ≤3. Fires
`batchAsyncGenerateVideoReferenceImages`.

```text
gflow video r2v PROMPT --ref IMG [--ref IMG ...] [--model] [--duration] [--count] [--aspect] [...]

Options:
  --ref PATH  Reference image; repeat for up to 7 (omni-flash) / 3 (veo). [required]
```

```bash
gflow video r2v "a knight in this armor walks forward" --ref armor.png
gflow video r2v "blend these worlds" --ref a.png --ref b.png --ref c.png --model omni-flash
```

## `gflow video batch`

> ⚠️ **Not yet implemented.** The `batch` subcommand currently exits with
> `[yellow]gflow video batch is not yet available.[/yellow]` (exit 1).
> Manifest-driven batching on `UiAutomationTransport` is queued for a later
> release (see Phase B follow-ups).

### Workaround — shell for-loop

Until the manifest runner lands, you can drive sequential video generations
through a plain shell loop. Each `gflow video t2v` / `i2v` / `r2v` call opens
its **own Flow project**, so the resulting videos will NOT share a
`project_id` (unlike `gflow image batch`, which mounts one project across
all prompts) — but they DO get generated and downloaded:

```bash
# bash / WSL / macOS — one prompt per line
while IFS= read -r prompt; do
  gflow video t2v "$prompt" --aspect 9:16
done < prompts.txt
```

```powershell
# PowerShell — one prompt per line
Get-Content prompts.txt | ForEach-Object {
  gflow video t2v $_ --aspect 9:16
}
```

The trade-off vs. a true manifest runner: separate `project_id`s mean each
generation re-mints a reCAPTCHA (a few extra seconds per shot) and the
videos won't appear together in your Flow gallery. The files on disk are
identical to what `batch` would produce. The same pattern works for
`gflow video i2v <image> "<prompt>"` and `gflow video r2v "<prompt>" --ref <img>`.

## `gflow data media`

Look up a recorded operation by its Flow media ID. Prints a summary of the stored provenance record: profile, media ID, Flow project ID, kind (image/video), and the local file paths that were written for that operation.

```text
gflow data media MEDIA_ID [--profile NAME]

Arguments:
  MEDIA_ID              Flow media UUID (e.g. ddb6ef97-262d-49f4-8269-4a28c0fae6a2). [required]

Options:
  --profile NAME        Profile name (overrides default).
```

**Example output:**

```text
Profile:    default
Media ID:   ddb6ef97-262d-49f4-8269-4a28c0fae6a2
Project ID: f1a2b3c4-0000-0000-0000-000000000001
Kind:       image
Paths:
  /home/user/Downloads/gflow-cli/images/2026-05-24/ddb6ef97_1.png
  /home/user/Downloads/gflow-cli/images/2026-05-24/ddb6ef97_2.png
```

Exit codes: `0` success, `2` media ID not found in the local database, `16` database error (see exit code table below).

## `gflow run`

Sequential JSON-described batch image generation. New in v0.5.0a1.

```text
gflow run --config FILE [--output-dir DIR] [--profile NAME] [--continue-on-error|--fail-fast]
```

The config is a JSON file with a top-level `prompts` array; each entry
produces 1–4 images through one `FlowApiClient` session (one Playwright
browser, one Flow project, sequential reCAPTCHA mints).

### Config schema

```json
{
  "profile": "<your-profile>",
  "transport": "ui_automation",
  "output_dir": "out/example-batch",
  "prompts": [
    {
      "text": "a quiet mountain lake at dawn, cinematic photography",
      "aspect_ratio": "9:16",
      "model": "nano2",
      "count": 1,
      "output_filename": "lake_scene"
    },
    {
      "text": "a sunlit forest path in autumn",
      "aspect_ratio": "16:9"
    }
  ]
}
```

| Key | Required | Default | Notes |
|---|---|---|---|
| `prompts` | **yes** | — | 1–50 entries. |
| `prompts[].text` | **yes** | — | 1–2000 chars. |
| `prompts[].aspect_ratio` | no | `9:16` | `9:16` / `16:9` / `1:1` / `4:3` / `3:4`. |
| `prompts[].model` | no | `nano2` | `nano2` / `nano-pro` / `imagen4`. |
| `prompts[].count` | no | `1` | 1–4. |
| `prompts[].output_filename` | no | `prompt_<index>` | Filename stem; saved as `<stem>_<image-index>.png`. |
| `profile` | no | active profile | CLI `--profile` overrides. |
| `transport` | no | `ui_automation` | Experimental strategies need `GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1`. |
| `output_dir` | no | `out/<UTC-timestamp>/` | CLI `--output-dir` overrides. |

### Error semantics

`--continue-on-error` (default): one prompt failing logs the error and continues. Final exit code is the max per-prompt exit code (so a `WafRejectionError` anywhere in the batch makes the whole run exit 10).

`--fail-fast`: first failure stops the batch. Remaining prompts are reported as SKIPPED in the summary table.

### Example

```bash
GFLOW_EXAMPLE_PROFILE=<your-profile> python examples/batch_from_config.py
```

The bundled `examples/sample_config.json` produces three images at three aspect ratios in `gflow-output/example-batch/`. Copy and edit for your own scenes.

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
GFLOW_CLI_LOG_FORMAT=json gflow image t2i "..." 2>&1 | jq .
```

## Exit codes

Phase 4 (v0.4.0a1+) maps every `GFlowError` subclass to a stable exit code so
shell scripts can branch on the failure mode without parsing stderr.

| Code | Error class           | Meaning                                          | Remediation                                                |
|------|-----------------------|--------------------------------------------------|------------------------------------------------------------|
| `0`  | —                     | Success                                          | —                                                          |
| `1`  | unhandled exception   | Anything not derived from `GFlowError`           | Re-run with `--verbose`; file a bug if it persists         |
| `2`  | usage error (Click)   | Bad usage / missing arg / profile missing        | Standard CLI usage error                                   |
| `3`  | `AuthExpiredError`    | Session cookies rejected by Flow (401/403)       | `gflow auth login --profile <name>`                        |
| `4`  | `RateLimitError`      | Quota / rate limit hit, exhausted retries        | Wait + reduce `GFLOW_CLI_CONCURRENCY`                      |
| `5`  | `ContentPolicyError`  | Flow rejected the prompt (200 + empty `media[]`) | Soften prompt wording                                      |
| `6`  | `NetworkError`        | Network failure persisted across 3 attempts      | Check connectivity                                         |
| `7`  | `WireFormatError`     | Unexpected response shape — Flow API changed     | File a bug (do NOT include captured tokens or signed URLs) |
| `8`  | `AuthMissingError`    | Required auth credential is absent from profile   | `gflow auth login --profile <name>`                        |
| `9`  | `TransportTimeoutError` | Browser/API operation exceeded its timeout      | Retry; raise the relevant timeout if needed                |
| `10` | `WafRejectionError`   | Flow security layer rejected the request          | Change prompt/request and retry                            |
| `11` | `ConfigurationError`  | Local configuration or browser mode is invalid    | Fix the option/env var shown in the error                  |
| `12` | `AuthLoginTimeoutError` | Browser sign-in was not completed in time       | Re-run login or raise `GFLOW_CLI_AUTH_LOGIN_TIMEOUT`       |
| `13` | `SecurityError`       | Unsafe local profile or secret handling blocked   | Follow the error's safety guidance                         |
| `14` | `AuthBrowserRejectedError` | Google rejected the login browser             | `gflow auth login --browser chrome`                        |
| `16` | `DataStoreError`      | Local database cannot be opened, a migration failed, or the DB schema is newer than the installed gflow-cli | See below                                  |
| `130`| SIGINT                | User-interrupted (Ctrl-C)                        | —                                                          |

**Exit code 16 — data store / migration error.** Fires when:

- The database file cannot be opened (filesystem permission or path issues).
- A migration fails or the migration checksum drifts from what the installed version expects.
- The database has a **newer schema** than the installed gflow-cli (i.e. you downgraded after a migration already ran).

Recovery for the "newer schema" case: upgrade gflow-cli to a version that understands the schema (`uv tool upgrade gflow-cli`), OR point `GFLOW_CLI_DB_PATH` to a different database location (a fresh path creates a new empty database automatically).

All errors emit a structured `error_raised` event (or `error_unhandled` for
exit code 1) with stable fields — `error_class`, `problem` (RFC 9457 Problem
Details), `cli_command`, `correlation_id`. Pipe stderr to a file and grep
for telemetry forensics:

```bash
GFLOW_CLI_LOG_FORMAT=json gflow video t2v "..." 2> events.jsonl
jq 'select(.event == "error_raised") | .error_class' events.jsonl
```

Branch in shell scripts — capture the exit code **before** the `if`/`case` consumes it:

```bash
gflow video i2v ./in.png "test" -o out.mp4
rc=$?
if [ "$rc" -ne 0 ]; then
  case "$rc" in
    2)   echo "Bad CLI usage (missing arg, bad flag)"; exit 1 ;;
    3)   echo "Auth expired — run: gflow auth login"; exit 1 ;;
    4|6) echo "Transient infra issue (rate limit / network) — try again later"; exit 1 ;;
    5)   echo "Content policy rejected the prompt — rewrite and retry"; exit 1 ;;
    7)   echo "Flow API shape changed — upgrade gflow-cli or file a bug"; exit 1 ;;
    8)   echo "Auth profile is missing a required credential — run: gflow auth login"; exit 1 ;;
    9|12) echo "Operation timed out — retry with a larger timeout if needed"; exit 1 ;;
    10)  echo "Flow rejected the request — adjust the prompt/request and retry"; exit 1 ;;
    11)  echo "Configuration error — fix the option or env var shown above"; exit 1 ;;
    13)  echo "Security guard blocked unsafe local state — follow the error guidance"; exit 1 ;;
    14)  echo "Google rejected the login browser — run: gflow auth login --browser chrome"; exit 1 ;;
    16)  echo "Database error — check permissions or upgrade gflow-cli"; exit 1 ;;
    130) echo "Cancelled with Ctrl-C"; exit 130 ;;
    *)   echo "Unknown failure (exit $rc)"; exit 1 ;;
  esac
fi
```

> **Why `rc=$?` first?** Inside `if ! cmd; then ...`, `$?` reflects the negation pipeline (always `0` when the `then` branch fires), not the failing command. Capturing into `rc` immediately after the call is the portable pattern across bash/zsh/dash. PowerShell uses `$LASTEXITCODE` for the same purpose.

## Programmatic use

The CLI is a thin shell over `gflow_cli.api.client.FlowApiClient`. All public methods used by the commands above are also available directly.

### Importing errors

Two module paths resolve to the same error classes. Use whichever feels natural for your codebase:

```python
from gflow_cli.errors import GFlowError, AuthExpiredError   # canonical
from gflow_cli.exceptions import GFlowError, AuthExpiredError  # standard alias
```

Both are identical objects — `gflow_cli.exceptions` is a re-export of `gflow_cli.errors`. The alias exists because many developers and tools expect the conventional `exceptions` name.

### Single-shot generation

```python
import asyncio
from pathlib import Path
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import GenerateImageRequest, Model, Aspect
from gflow_cli.config import get_settings

async def main() -> None:
    settings = get_settings()
    profile_dir = settings.profile_subdir("default")
    async with FlowApiClient(profile_dir=profile_dir, headless=settings.headless) as client:
        req = GenerateImageRequest(prompt="a peaceful lake at dawn", model=Model.IMAGE4)
        # project_id is optional — omit it and a new project is created automatically.
        image = await client.generate_image(req=req)
        await client.download_image(image, Path("lake.png"))

asyncio.run(main())
```

`project_id` defaults to `None`. When omitted, `generate_image()` (and `generate_images_batch()`) call `create_project()` internally. Pass an explicit `project_id` when you want multiple generations to land in the same Flow project.

### Archive / cleanup

```python
async with FlowApiClient(profile_dir=profile_dir) as client:
    project = await client.create_project(title="archive demo")
    asset = await client.upload_image(project.project_id, Path("hero.png"))
    # Each uploaded asset and each generated media item has its own workflow_id.
    await client.archive_workflow(
        workflow_id=asset.workflow_id,
        project_id=project.project_id,
    )
```

`FlowApiClient.archive_workflow(workflow_id, project_id)` issues `PATCH /v1/flowWorkflows/{id}` to soft-delete a workflow. Useful in batch scripts that spin up a project per call and want to clean up afterwards. `workflow_id` comes from any `AssetInfo` (`upload_image` return) or `VideoOperation` / `VideoStatus` / `ImageResult` (generation returns) — `ProjectInfo` itself only carries `project_id` and `title`.

### Health check (long-lived workers)

For worker processes that hold a `FlowApiClient` open across many requests, call `health_check()` before dispatching to detect a dead browser context without catching exceptions yourself:

```python
async with FlowApiClient(profile_dir=profile_dir) as client:
    while True:
        job = await queue.get()
        if not await client.health_check():
            # browser context is dead — re-enter or restart the worker
            break
        image = await client.generate_image(req=job.req)
        await handle_result(image)
```

`health_check()` returns `True` if the underlying Playwright page is alive and the current URL is on a Google domain. It returns `False` (never raises) on `TargetClosedError` or any other exception.

## See also

- [CONFIGURATION](CONFIGURATION.md) — env vars, output paths, defaults
- [AUTHENTICATION](AUTHENTICATION.md) — auth flow + multi-account
- [ARCHITECTURE](ARCHITECTURE.md) — internal structure (for contributors)
- [PLAN](../PLAN.md) — what ships in which phase
