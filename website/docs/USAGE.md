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
    i2v                         Generate a video from an initial frame + motion prompt.
    r2v                         Generate a video from reference images + prompt.
    batch                       Run a TSV manifest of video generations.
    chain                       Render a JSONL manifest as a last-frame I2V chain.

  scene     Compose Flow Scenes (Add Clip) — credit-free REST.
    create                      Compose ordered, trimmable clips into a scene (optional extended .mp4).
    show                        Read back a scene's clip order and trims.

  character Manage Flow Character entities for a project (#145).
    create                      Generate a reusable character (paid face + body refs).
    list                        List the characters in a project.
    show                        Show one character by --id or --name.
    rm                          Delete a character by --id or --name (FREE).
    voices                      List the preset Gemini voices for character TTS.

  data      Local provenance database (read-only queries).
    media MEDIA_ID              Show stored record for a Flow media ID.
    list {projects,images,videos,profiles}  Browse the catalog.
    prune                       Remove stale local file entries.

  models    Print the image/video model catalog (Rich table or --json).
```

Global flags:

- `-V`, `--version` — print version and exit.
- `-v`, `--verbose` — log at DEBUG level.

Machine-readable output: the generation commands (`image t2i` / `image i2i`,
`video t2v` / `i2v` / `r2v`), `auth list`, and `gflow models` accept `--json` to
emit a single parseable object on stdout instead of Rich tables. See
[§ JSON output](#json-output---json).

Note: `--profile NAME` is **per-subcommand**, not global — pass it after the subcommand name (e.g. `gflow image t2i "..." --profile experiments`, not `gflow --profile experiments image t2i ...`).

## `gflow auth`

See [AUTHENTICATION § Commands](AUTHENTICATION.md#commands).

## `gflow image upload`

Upload a local PNG/JPEG/WebP/GIF into a fresh Flow project and print the asset UUID + dimensions Flow inferred. The UUID is what later subcommands (`gflow image i2i --ref UUID`, `video i2v`) accept as an initial frame. Note for `video i2v`: the UUID is selected from the generation project's media picker, and `upload` puts the asset in a *fresh scratch project* — so pass `--project <id>` of the project that holds the asset, or the picker lookup fails with exit 9 (#287).

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

## `gflow image upscale`

Upscale a **platform-generated** image to 2K or 4K (the same 1K/2K/4K options Flow's
download menu offers) and save it locally. Uploaded images are not supported.

```text
gflow image upscale MEDIA_ID --scale 2k|4k [OPTIONS]

Arguments:
  MEDIA_ID                  UUID of a Flow-generated image (find one with
                            `gflow data list images`).

Options:
  --scale [2k|4k]           Target resolution. 4k requires a Flow Ultra
                            subscription; 1k is the original (no upscale).
  --project ID              Project that owns the image. Resolved from the local
                            catalog when omitted; pass it explicitly for images
                            gflow didn't record (e.g. generated in the web UI).
  --out PATH                Output directory (see "Output paths" below).
  --profile NAME            Profile name (overrides default).
```

```bash
# Upscale a previously generated image to 2K (project auto-resolved from the catalog)
gflow image upscale 3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14 --scale 2k

# Upscale an image generated in the Flow web UI (not in the local catalog)
gflow image upscale <mediaId> --scale 2k --project <projectId>
```

Notes:

- **Credit-free** — upscaling is an image operation and spends no credits.
- **4K is Ultra-only.** On a non-Ultra account a 4K request fails with exit code 22
  (`UpscaleUnavailableError`) and a hint to use `--scale 2k` or upgrade.
- The result is saved as `<output_dir>/images/<YYYY-MM-DD>/<mediaId>_<scale>.<ext>`
  (extension matches the returned format — usually `.jpg`).

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
  --jitter SPEC             Anti-bot pause between multi-prompt submissions:
                            'MIN-MAX' seconds, a single number for 0-N, or 0
                            to disable. Widen (e.g. 10-30) if runs hit WAF
                            403s. [default: 0.5-1.5; GFLOW_CLI_JITTER_RANGE
                            overrides the default]
  --model [nano2|nano-pro|image4]
                            Image model alias.                [default: nano2]
  --aspect [9:16|16:9|1:1|4:3|3:4]
                            Aspect ratio.                     [default: 9:16]
  -n, --count INTEGER       How many images to generate (1-4).  [default: 1]
  --out PATH                Output directory (see "Output paths" below).
  --project ID              Generate in this EXISTING Flow project instead of a
                            scratch project. Required to reference locked
                            entities/assets in that project. Single-prompt only.
  --reference-entity ID     Flow CHARACTER entity id to reference for character
                            consistency (repeatable; must live in --project).
                            Single-prompt only.
  --reference-entity-name N Display name paired with --reference-entity.
  -t, --tool NAME[:k=v]     Apply a prompt tool before generating, e.g.
                            `creative-director:style=cinema`. Repeatable; applied
                            per prompt on multi-prompt/batch. See "Prompt tools".
  --ui-mode [auto|classic|agentic]
                            Require a Flow UI arm: classic (hard aspect
                            controls) / agentic (chat surface; forced by -i) /
                            auto (default). Aborts exit 28 if unreachable.
                            Single-prompt only; batch uses GFLOW_CLI_UI_MODE.
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

**Prompt tools (`-t` / `--tool`).**

A *tool* is a named, single-purpose transform applied to your prompt before generating —
the first built-in is `creative-director`, which rewrites a terse prompt into a richer one
using Google's five-component formula (Subject + Action + Context/Location +
Composition/Camera + Style) via the public Gemini API. `-t/--tool` is **repeatable** and
works on every generation command (`image t2i`/`i2i`/`batch`, `video t2v`/`i2v`/`r2v`/`chain`);
on multi-prompt/batch it is applied per prompt.

- Requires `GFLOW_CLI_GEMINI_API_KEY` ([get one](https://aistudio.google.com/apikey));
  optionally `GFLOW_CLI_GEMINI_MODEL` (default `gemini-2.5-flash`).
- Graceful: if the key is unset or the API errors, gflow prints a notice and
  generates from your **original** prompt — the run never fails because of a tool
  (each call is bounded by an overall ~60s wall-clock budget).
- The local catalog records **both** the original prompt and the submitted
  expansion (withheld under `GFLOW_CLI_HISTORY_PROMPTS=redacted`), plus a
  `metadata_json.tool` provenance descriptor.
- Discover and preview tools with `gflow tools list` / `show` / `run` (see the
  **`gflow tools`** section below). Full reference: [TOOLS.md](TOOLS.md) and
  [PROMPT_EXPANSION.md](PROMPT_EXPANSION.md).

```bash
# Rewrite "cat in space" into a detailed prompt with the cinema style, then generate
gflow image t2i "cat in space" --tool creative-director:style=cinema
```

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
  --project ID              Generate in this EXISTING Flow project (skip scratch).
  --reference-entity ID     Flow CHARACTER entity id to reference (repeatable;
                            must live in --project). Counts toward the ref cap.
  --reference-entity-name N Display name paired with --reference-entity.
  --profile NAME            Profile name (overrides default).
```

**Path-or-UUID semantics.** Each `--ref` value is classified at the CLI boundary:

- **Looks like a Flow asset UUID** (case-insensitive 8-4-4-4-12 hex, e.g. `ddb6ef97-262d-49f4-8269-4a28c0fae6a2`) → passed through verbatim. No upload, no extra round-trip.
- **Anything else** → treated as a local path. The CLI canonicalises it (resolving symlinks once at validation time, closing the symlink-laundering vector where `./hero.png -> ~/.ssh/id_rsa` could exfiltrate secrets), then attaches it — **deduplicated by filename since v0.38.0 (#314):** if the target project's library already holds an asset with the exact same filename, that existing tile is selected in the picker instead of re-uploading, so repeating a ref across runs no longer piles up duplicate library entries. On a picker miss the file is uploaded as before. Caveat: the match is by exact filename only — a *different* image that happens to share the name of one already in the project will be reused, not uploaded; rename the file if you need a fresh upload. (Video `r2v` refs keep upload-only behavior.)

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

## Character-consistent images (entity references)

`--reference-entity` makes `image t2i`/`i2i` reference a locked Flow **CHARACTER
entity** (minted via `gflow character create`) so the generated subject stays
on-model across shots — no per-shot prompt wrangling or fragile image refs.

How it works: entities are **project-scoped**, so you must generate **in the
project that owns them** via `--project <id>` (this also means no throwaway
scratch project is created). The CLI attaches each entity through the Flow editor's
resource picker; the submit then carries `referenceEntities`, exactly like the
video R2V path. Entities count toward the same per-model reference cap as `--ref`
images. `--reference-entity` / `--project` are **single-prompt only**.

For a *pure* character reference (no starting image) use `t2i` — `i2i` still
requires at least one `--ref`.

```bash
# One locked character, consistent across runs (t2i = no image ref needed)
gflow image t2i "Aria explores a neon market, full body" \
  --project 7fa97443-… --reference-entity 8a77f8cb-… --reference-entity-name Aria \
  --model nano2 --aspect 9:16 -n 3

# Several characters in one scene (each --reference-entity repeatable)
gflow image t2i "Aria and Drako meet at the gate" --project 7fa97443-… \
  --reference-entity 8a77f8cb-… --reference-entity 4ed5cb7f-…

# i2i: a starting image PLUS an entity for the character
gflow image i2i "place this hero in a snowy pass" --ref hero.png \
  --project 7fa97443-… --reference-entity 8a77f8cb-…
```

> Ids are validated at the CLI boundary (letters/digits/hyphens, ≤128 chars).
> The entity must already exist in `--project` (see `gflow character create`).

## Referencing saved assets by name (`@Name` mentions)

Instead of ids or paths, you can reference a saved **Character** — or, on image
paths, a saved media asset — **inline in the prompt** by name, prefixed with `@`.
The mention is resolved against the project's assets, staged through the same
reference machinery as `--reference-entity` / `--ref`, then stripped from the text
the model sees. Shipped in v0.40.0 (#344).

```bash
# One saved Character by name (t2i needs no image ref)
gflow image t2i "@CaptainZoro on a rain-soaked neon rooftop, cinematic" --project <id>

# Two saved Characters in one video prompt (each staged as a reference, cap-checked)
gflow video r2v "@Zoro hands @Mika the sword" --project <id>
```

**Where `@Name` works** — character mentions on image **and** video; media/asset
mentions on image **only**:

| Path | Character `@Name` | Media/asset `@Name` |
|---|---|---|
| `image t2i` | ✅ | ✅ (in-project asset → `referenceImages`) |
| `image i2i` | ✅ | ✅ |
| `video t2v` / `i2v` / `r2v` | ✅ | ❌ — media-on-video is Phase 3; fails fast with **exit 11** |

Rules:
- **`--project <id>` is required** — mentions resolve against that project's assets.
- A name resolves against **Characters** (`gflow character create`) *and* saved
  media assets; on a name clash the **Character wins**. Matching is
  **case-insensitive**.
- A Character must have **at least one reference image** before it can be tagged,
  or the mention fails early.
- Write a **literal `@`** by doubling it: `@@` → `@` (e.g. `"ping me @@ 5pm"` →
  `ping me @ 5pm`). An `@` glued to a preceding word (`user@host`) is never a
  mention, so emails and handles are safe.
- Unknown or ambiguous names fail fast with **exit 11** (`ConfigurationError`),
  listing the available or colliding assets. If the asset catalog itself cannot be
  loaded you get **exit 29** (`MentionIndexUnavailableError`) — so scripts can tell
  "catalog unreachable" apart from "no such name".

`@Name` and `--reference-entity <id>` resolve to the **same** wire
(`referenceEntities`) and dedupe against each other: reach for `@Name` for the
inline, by-name path — it's also the **only** name-based option on video, which
has no `--reference-entity` flag — and `--reference-entity` when you'd rather pin
an explicit entity id in a script (image paths only). `--ref` is different: it
attaches an arbitrary **image** (`referenceImages`), not a saved identity, and can
be combined with a `@Name` for "this identity, in this look".

See [REFERENCE_STRATEGIES.md](REFERENCE_STRATEGIES.md) for the full decision guide.

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

All prompts in a batch share one Flow project. The editor is opened once and stays mounted; the transport is **strictly serial** — each prompt is configured, submitted, and its generation awaited before the next prompt is submitted, so only one generation is in flight at a time. The small random pause between submissions (default 0.5–1.5 seconds; tune with `--jitter` or `GFLOW_CLI_JITTER_RANGE`) is a **submission-cadence anti-bot-detection measure** on top of that serial rhythm. The command returns once every row has resolved (success or failure). See [DEBUGGING § WAF cadence](DEBUGGING.md#waf-cadence) for when to widen the range.

### Flags

- `--continue-on-error` / `--fail-fast` — keep going past row failures or stop at the first one (default: `--continue-on-error`, matching the CLI flag default). On fail-fast, already-completed images are downloaded before the error is surfaced.
- `--jitter SPEC` — anti-bot pause between submissions: `MIN-MAX` seconds (e.g. `10-30`), a single number for `0`–`N`, or `0` to disable. Default `0.5-1.5`; widen if runs hit WAF 403s. `GFLOW_CLI_JITTER_RANGE` overrides the default, the flag beats both.

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
>
> **`i2v` is narrower (issue #125):** `omni-flash` does NOT support image-to-video
> interpolation — Flow silently drops the start/end frames and produces a
> text-only video — so it is **not** an accepted `--model` for `i2v`. The `i2v`
> choices are `[veo-lite|veo-fast|veo-quality|veo-lite-lp]` and the **default is
> `veo-lite`** (not Flow's UI default). `--duration` for `i2v` is `[4|6|8]` (10s
> is omni-flash-only). `t2v` and `r2v` keep the full shared set above.

## `gflow video t2v`

Generate a video from a text prompt only.

```text
gflow video t2v PROMPT [--model] [--duration] [--count] [--aspect] [--profile] [-t/--tool] [--project] [--out-dir]

Options:
  --project ID          Generate in this EXISTING Flow project instead of a
                        scratch project (see "Sharing one project across calls").
```

```bash
gflow video t2v "Slow cinematic push-in toward a candle flame"
gflow video t2v "Aerial shot of a coastline at sunset" --aspect 16:9 --out-dir ./out
gflow video t2v "A neon city timelapse" --model omni-flash --duration 10 --count 2
# Apply the creative-director tool (cinematic style) before generating
gflow video t2v "a dog surfing" --tool creative-director:style=cinematic
```

`-t` / `--tool` applies a prompt tool before generating — see
[prompt tools](#gflow-image-t2i) under `image t2i` for the full contract
(requires `GFLOW_CLI_GEMINI_API_KEY`; degrades gracefully to the original prompt).

## `gflow video i2v`

Generate a video from an INITIAL frame (+ optional END frame) and a motion prompt.
Each frame is a local PNG/JPEG **or the media UUID of an existing in-project asset**
(#287 — the asset is selected in place, no duplicate upload; pair with `--project`
so the UUID's project is the one being generated in). A local file is bound into
the editor's frame slot via the media dialog, then Flow fires
`batchAsyncGenerateVideoStartImage` (initial only) or `…StartAndEndImage`
(initial+end interpolation).

```text
gflow video i2v --initial-frame INITIAL [--end-frame LAST] PROMPT [--model] [--duration] [--count] [--aspect] [...]

# Back-compat positional form (still supported):
gflow video i2v IMAGE PROMPT [--end-frame LAST] [...]

Arguments:
  PROMPT  Motion prompt.  [required]

Options:
  --initial-frame PATH|UUID  Initial frame to animate: local image path or in-project
                             asset media UUID. Canonical form; replaces the positional IMAGE.
  --end-frame PATH|UUID      Optional end frame — Flow interpolates initial frame -> end frame.
  --project ID               Generate in this EXISTING Flow project instead of a
                             scratch project (see "Sharing one project across calls").
```

```bash
gflow video i2v --initial-frame ./hero.png "Slow camera arc, soft golden light"
gflow video i2v --initial-frame ./first.png --end-frame ./last.png "morph between scenes" --model veo-quality
# Reference an existing in-project asset by media UUID (no re-upload, #287):
gflow video i2v --initial-frame d6f1927a-3eae-4626-bc90-9a6ea7637bab "pan left" --project f6caf027-...
# Back-compat positional form:
gflow video i2v ./hero.png "Slow camera arc, soft golden light"
```

Exit codes specific to this surface: **27** — Flow's upload endpoint rejected a
local frame file (`MediaUploadRejectedError`; try re-encoding, e.g.
`ffmpeg -i in.jpg -q:v 2 -map_metadata -1 out.jpg`, or reference the asset by
UUID instead); **9** — a frame UUID could not be located in the project's media
picker (`TransportTimeoutError`; wrong `--project` or foreign UUID).

## `gflow video r2v`

Reference-to-video (Flow "ingredients"): condition a generation on reference
images. Per-model cap: `omni-flash` ≤7, the `veo-*` models ≤3. Fires
`batchAsyncGenerateVideoReferenceImages`.

```text
gflow video r2v PROMPT --ref IMG [--ref IMG ...] [--model] [--duration] [--count] [--aspect] [...]

Options:
  --ref PATH    Reference image; repeat for up to 7 (omni-flash) / 3 (veo). [required]
  --project ID  Generate in this EXISTING Flow project instead of a scratch
                project (see "Sharing one project across calls").
```

```bash
gflow video r2v "a knight in this armor walks forward" --ref armor.png
gflow video r2v "blend these worlds" --ref a.png --ref b.png --ref c.png --model omni-flash
```

### Sharing one project across calls

By default each `video t2v` / `i2v` / `r2v` call creates its own scratch Flow
project. Pass `--project <id>` (same contract as `image t2i`/`i2i` — see
[`gflow image t2i`](#gflow-image-t2i)) to generate into an existing project
instead, e.g. to avoid one-throwaway-project-per-clip when scripting a
multi-clip storyboard:

```bash
gflow video t2v "establishing shot" --project PROJ123
gflow video t2v "close-up reaction" --project PROJ123
```

## Batch video generation (shell loop)

`gflow video` has no `batch` subcommand — a manifest-driven video runner
was scaffolded early on but never worked and was removed. `gflow image
batch` (manifest-driven image generation) is unaffected and still ships. For
video, drive sequential generations through a plain shell loop instead.
Without `--project`, each `gflow video t2v` / `i2v` / `r2v` call opens its
**own Flow project**, so the resulting videos will NOT share a `project_id`
(unlike `gflow image batch`, which mounts one project across all prompts) —
but they DO get generated and downloaded. Pass the same `--project <id>` to
every call in the loop (see "Sharing one project across calls" above) if you
want them to land in one project instead:

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

The trade-off vs. a manifest runner: separate `project_id`s mean each
generation re-mints a reCAPTCHA (a few extra seconds per shot) and the
videos won't appear together in your Flow gallery. The same pattern works
for `gflow video i2v <image> "<prompt>"` and
`gflow video r2v "<prompt>" --ref <img>`.

## `gflow video chain`

Render a JSONL manifest of *links* into one continuous last-frame I2V chain.
Link 0 is a text-to-video (T2V) generation; every later link is an
image-to-video (I2V) generation **seeded by the extracted last frame of the
previous clip**, giving visual continuity with no server-side stitching.

> ⚠️ **Costs N credits — one per link.** A chain is N sequential paid Veo
> generations. Run `--dry-run` first to print the plan and the credit estimate
> without spending anything. The cost-confirmation prompt is shown unless you
> pass `-y` / `--yes`.

> **Requires the `chain` extra.** The last-frame extractor decodes the previous
> clip with [PyAV](https://pyav.org/) (no system ffmpeg needed). Install it
> with:
>
> ```bash
> pip install 'gflow-cli[chain]'
> # or:  uv tool install 'gflow-cli[chain]'
> ```

```text
gflow video chain MANIFEST [OPTIONS]

Arguments:
  MANIFEST                  JSONL manifest, one link per line. [required]

Options:
  --model [veo-lite|veo-fast|veo-quality|veo-lite-lp]
                            Veo 3.1 model for every link.    [default: veo-lite]
  --max-links INTEGER       Cap link count; error (exit 11) if the manifest
                            has more links than this.
  -y, --yes                 Skip the per-credit cost confirmation prompt.
  --dry-run                 Resolve the manifest, print the plan + credit cost,
                            and spend nothing.
  --resume-from CHAIN_ID    Resume a prior chain by its id; already-paid links
                            are skipped (not re-billed).
  --jitter FLOAT            Random 0..JITTER second pause between links
                            (anti-bot cadence).               [default: 0.0]
  --seed-offset INTEGER     Extract the seed frame this many ms before EOF
                            (fade-to-black guard).            [default: 0]
  --aspect [9:16|16:9]      Uniform aspect for every link.    [default: 9:16]
  --profile NAME            Profile name (overrides default).
  --out-dir DIR             Directory for the link mp4s + seed frames.
  --json                    Emit a machine-readable JSON result.
```

> **`omni-flash` is rejected.** Only the Veo 3.1 family supports i2v
> interpolation. `omni-flash` silently drops the seed frame and routes to
> text-to-video (issue #125), which would break every seeded link, so it is not
> an accepted `--model` for `chain`. The chain also aborts a link loudly (rather
> than reporting a fake success) if a generation is observed routing to the
> text-only endpoint — see [KNOWN_ISSUES](../KNOWN_ISSUES.md).

### JSONL manifest format

One JSON object per line. Only `prompt` is required; `model` / `duration` /
`aspect` are optional per-link overrides (omit to inherit the chain default).
Blank lines and `#`-prefixed comment lines are skipped.

```jsonl
{"prompt": "a lone wolf on a snowy ridge at dawn, cinematic", "model": "veo-lite", "duration": 4, "aspect": "16:9"}
{"prompt": "it lifts its head and turns to face the camera"}
{"prompt": "it bounds down the slope toward the valley"}
```

The chain enforces a **uniform aspect** across links for continuity, so the
chain-level `--aspect` is applied to every link (a per-link `aspect` override in
the manifest is currently informational).

### Output — N clips, not one file

Each link is saved as its own mp4 (plus a `linkN_lastframe.jpg` seed frame
between links) under `--out-dir` (or the default output dir). **Stitching the
clips into a single video is a separate step — use `gflow scene` to
concatenate them server-side.** Chain does not auto-concat: see the
*deferred auto-concat* entry in
[KNOWN_ISSUES](../KNOWN_ISSUES.md) for why.

**Examples:**

```bash
# Preview the plan + credit cost — spends nothing
gflow video chain story.jsonl --dry-run

# Generate the chain (one credit per link), no confirmation prompt
gflow video chain story.jsonl --model veo-fast --aspect 16:9 --yes

# Resume a chain that died partway — already-paid links are skipped
gflow video chain story.jsonl --resume-from 1f2e3d4c-...

# Seed 150 ms before EOF to dodge a fade-to-black final frame
gflow video chain story.jsonl --seed-offset 150 --yes
```

## `gflow instructions`

Manage a project's **Agent Instruction cards** — the brief Flow's agent consults when it
generates. Enabled cards steer output (style, references) on **agentic-cohort** sessions.
Setting up cards is **credits-free**. Full guide: [INSTRUCTIONS.md](INSTRUCTIONS.md).

**Shipped (v0.28.0):** the ephemeral `-i / --instruction "text"` flag on `gflow image t2i` /
`i2i` adds a one-off enabled text card for that generation.

```bash
gflow image t2i "a cat on a chair" -i "flat 2D children's crayon drawing"
```

**Persistent cards:** the `gflow instructions add/list/enable/disable/rm/apply/toggle-mode`
CRUD group manages a project's brief, with a single generic `--ref` per card that accepts an
image path, a generated-image UUID, or a character id/name (image → `imageReferenceMediaIds`,
character → `characterReferenceEntityNames`). Cards are selected by **title** (case-insensitive)
by default, or by the stable server `--id` for the ambiguous-title / scripting case (mirrors
`gflow character`'s `--name` / `--entity-id`). Every subcommand requires `--project`. See
[INSTRUCTIONS.md](INSTRUCTIONS.md) for the full reference.

```text
gflow instructions add   TITLE --text TEXT [--ref REF]... --project ID [--disabled]
gflow instructions list  --project ID [--json]
gflow instructions enable  (TITLE | --id ID) --project ID
gflow instructions disable (TITLE | --id ID) --project ID
gflow instructions rm      (TITLE | --id ID) --project ID
gflow instructions apply   FILE  --project ID     # declarative full-sync (TOML/JSON)
gflow instructions toggle-mode (--on | --off) --project ID
```

## `gflow tools`

Discover and run **prompt tools** — named, single-purpose transforms applied to a prompt
before generation. The first built-in is `creative-director` (the Gemini "Creative Director").
Apply a tool inline on any generation command with `-t/--tool` (see [prompt tools](#gflow-image-t2i)),
or use this group standalone. Full reference: [TOOLS.md](TOOLS.md) · [PROMPT_EXPANSION.md](PROMPT_EXPANSION.md).

```text
gflow tools list [--json]
gflow tools show NAME [--json]
gflow tools run NAME "INPUT" [--style MODE] [--json]
```

- **`list`** — registered tools (name, title, category, description). Includes your own
  "My Tools" TOMLs from `<GFLOW_CLI_HOME>/tools/*.toml`.
- **`show NAME`** — full spec incl. required env and available `--style` modes.
- **`run NAME "INPUT"`** — run the tool standalone (no generation, no credits); pipeable.
  Emits `{name, original, expanded, was_expanded}` with `--json`.

```bash
gflow tools list
gflow tools show creative-director
# Preview an expansion (needs GFLOW_CLI_GEMINI_API_KEY); never fatal
gflow tools run creative-director "a cat on a couch" --style cinema --json
```

To author your own tool, drop a TOML in `<GFLOW_CLI_HOME>/tools/` — see
[TOOLS.md § My Tools](TOOLS.md).

## `gflow scene`

Compose ordered, trimmable video clips into a Flow **Scene** (the "Add Clip"
timeline). This is a **credit-free, reCAPTCHA-free** REST path: it arranges
*existing* clips (e.g. the per-link mp4s from `gflow video chain`) — it does not
generate anything. Each clip is referenced by its **workflow id**, optionally
trimmed with a `start-end` window in seconds.

### `gflow scene create`

```text
gflow scene create CLIP_REFS... [OPTIONS]

Arguments:
  CLIP_REFS...              One or more clips, in order. Each is
                            `workflowId[:start-end]` (trim in seconds, start<end).
                            [required]

Options:
  --project PROJECT_ID      Flow project id.                          [required]
  -o, --output PATH         Render the composed scene into ONE extended .mp4 at
                            PATH (server-side, credit-free, no local ffmpeg).
                            Without it, only the Flow scene is composed (no file).
  --force                   Overwrite --output if it already exists.
  --profile NAME            Profile name (overrides default).
```

With `--output`, gflow calls Flow's server-side `runVideoFxConcatenation` to
stitch the clips into a single extended video. The render is **credit-free** and
needs **no local ffmpeg** — Flow returns the encoded video inline (base64) and
gflow writes it to PATH (a `.mp4` suffix is enforced; takes up to ~3 min). The
compose is **persisted to the local catalog before** the render, so a render
failure is recoverable without re-composing. A non-`.mp4` `--output` is
rewritten to `.mp4`; an existing file errors unless `--force` is passed.

**Examples:**

```bash
# Compose three chain clips into a scene (no local file)
gflow scene create wf-aaa wf-bbb wf-ccc --project proj-123

# Same, but also render one extended .mp4 (credit-free, server-side)
gflow scene create wf-aaa wf-bbb wf-ccc --project proj-123 --output story.mp4

# Trim the middle clip to its 2.0–6.5s window before concatenating
gflow scene create wf-aaa wf-bbb:2.0-6.5 wf-ccc --project proj-123 -o story.mp4 --force
```

### `gflow scene show`

```text
gflow scene show --scene SCENE_ID --project PROJECT_ID [--profile NAME]

Options:
  --scene SCENE_ID          Scene id to read back.                     [required]
  --project PROJECT_ID      Flow project id.                           [required]
  --profile NAME            Profile name (overrides default).
```

Reads a scene back and prints each clip's position, workflow id, trim window,
source duration, and the composed total duration.

## `gflow character`

Create and manage reusable **Flow Character entities** — project-scoped,
broadly-available identities you can reuse across generations (#145). Full
design + wire protocol: [CHARACTER](CHARACTER.md). Every subcommand takes
`--project <id>` (required); use `--id`/`--name` flags, never a positional.

### `gflow character create`

> ⚠️ **Costs credits.** `create` runs **two paid image generations**: a face
> reference (slot 0), then a front/side/back triptych body (slot 1) seeded by
> that face. It runs as a **persist-before-spend, crash-recoverable saga** — the
> entity id and workflow rows are recorded before the credited generation, so a
> crashed run is recoverable. Generation requires a Chrome-strategy profile.

```text
gflow character create [OPTIONS]

Options:
  --project PROJECT_ID      Flow project id.                           [required]
  --name NAME               Display name for the new character.        [required]
  --face-prompt TEXT        Prompt for the face reference image.       [required]
  --body-prompt TEXT        Body/outfit DESCRIPTION (not a full prompt). gflow
                            wraps it in a self-contained front/side/back triptych
                            instruction seeded by the generated face, so one
                            generation yields all three angles. Omit to skip the
                            body step.
  --voice NAME              Preset voice (e.g. Charon); case-insensitive,
                            validated against `gflow character voices`.
  --personality TEXT        Personality notes for the character.
  --model [nano2|nanopro]   nano2 = Nano Banana 2 (default);
                            nanopro = Nano Banana Pro.            [default: nano2]
  --profile NAME            Profile name (overrides default).
  --locale TEXT             BCP-47 locale.                       [default: en-US]
  --json                    Emit a machine-readable JSON result.
```

There is **no aspect-ratio control** for characters. Generated images are
**downloaded** to local (or cloud) storage; the signed `fifeUrl` is used only at
download time and never persisted. The result reports the entity id, the bound
workflow ids, and each saved slot's local path (face, body).

### `gflow character list`

```text
gflow character list --project PROJECT_ID [--json] [--profile NAME]
```

Lists every Character entity in a project (name, entity id, voice, reference
count).

### `gflow character show`

```text
gflow character show --project PROJECT_ID (--id ENTITY_ID | --name NAME) [--json] [--profile NAME]
```

Shows one character's detail (entity id, project, voice, personality, reference
workflow ids). Provide **exactly one** of `--id` or `--name`. An ambiguous
`--name` (multiple characters share it) exits with code **11**.

### `gflow character rm`

```text
gflow character rm --project PROJECT_ID (--id ENTITY_ID | --name NAME) [--yes] [--json] [--profile NAME]
```

Deletes a Character by `--id` or `--name`. Provide **exactly one** of `--id` or
`--name`. Prompts for confirmation unless `--yes` (or `--json`) is supplied; an
ambiguous `--name` (multiple characters share it) exits with code **11**.
**FREE** — no reCAPTCHA, no credit (`POST flow:batchDeleteAssets`).

### `gflow character voices`

```text
gflow character voices [--json]
```

Lists the **29 preset Gemini voices** available for character TTS — each with a
name, description, and sample URL. These names are what `--voice` validates
against (case-insensitively) on `gflow character create`.

## `gflow data list`

Read-only browse over the local SQLite catalog. Shipped in v0.9.0.

```text
gflow data list projects   [--profile NAME] [--limit N] [--offset N] [--json]
gflow data list images     [--profile NAME] [--limit N] [--offset N] [--json] [--all-copies]
gflow data list videos     [--profile NAME] [--limit N] [--offset N] [--json] [--all-copies]
gflow data list profiles                    [--limit N] [--offset N] [--json]
gflow data list errors     [--profile NAME] [--limit N] [--offset N] [--json]

Options:
  --profile NAME        Filter to one profile (not available on `profiles`).
  --limit N             Max rows returned. Range 1..1000. Default 20.
  --offset N            Rows to skip (pagination). Default 0.
  --json                Force JSONL output (one record per line).
  --all-copies          Show every local file copy separately (images/videos only).
```

By default, `images` and `videos` aggregate rows by Flow media ID. If an asset
has multiple local copies (e.g. re-downloaded to different paths), they appear
as a single row with a `COPIES` count and the path of the latest copy. Use
`--all-copies` to see every path as a separate row.

Output:
- TTY stdout → Rich-formatted table.
- Pipe / non-TTY / `--json` → JSONL.

Default sort: newest first (by `created_at`). Exit codes: 0 success (including the empty-catalog case — a missing/freshly-created DB is auto-migrated and returns 0 with no rows) / 2 Click usage / **16** `DataStoreError` family (migration mismatch, permission denied, corrupt schema). See [#88](https://github.com/ffroliva/gflow-cli/issues/88).

**Examples:**

```bash
# Newest 20 projects across all profiles
gflow data list projects

# All images for one profile, aggregated by asset by default
gflow data list images --profile ffroliva --limit 50 --offset 0

# Show every local file copy separately
gflow data list images --all-copies

# Videos as JSONL for piping into jq
gflow data list videos --json | jq '.media_id'

# Profiles with at least one recorded generation
gflow data list profiles

# Failed generations, newest first (v0.39.0+, #341): started_at, command, mode,
# model, profile, error_type (waf-rejection, content-policy, ...), redacted detail
gflow data list errors --profile my-profile --json | jq '{started_at, error_type}'
```

`errors` lists terminal `status="failed"` operation rows — every paid
generation that raised (WAF 403, content policy, cohort pin, timeout, auth)
is recorded before the CLI exits, so block onset/recovery windows are
measurable. `error_detail` is redacted before persistence (no tokens, cookies,
or signed URLs). See
[`DATA_LAYER.md § Failure recording`](DATA_LAYER.md#failure-recording-341-v0390).

> **`data list profiles` vs `gflow auth list`** — `data list profiles` shows profiles that have **recorded generations** in the catalog; `gflow auth list` shows profiles that have ever **logged in** via `gflow auth login`. A profile that logged in but never generated anything will appear in `auth list` but not in `data list profiles`.

For full schema details and JOIN semantics, see [`docs/DATA_LAYER.md § Querying the data layer`](DATA_LAYER.md#querying-the-data-layer).

## `gflow data prune`

Remove `local_files` database entries whose local paths no longer exist on
disk. Shipped in v0.9.1.

```bash
gflow data prune [--dry-run] [--profile NAME]
```

Useful after test runs that wrote to temporary directories, or after manually
deleting media files. Only **local files** (where `storage_provider` is NULL)
are scanned; cloud-stored assets are ignored to prevent accidental pruning of
remote objects.

Options:
  --dry-run             Preview dead rows without deleting.
  --profile NAME        Limit scan to a specific profile.

## `gflow data media`

Look up a recorded operation by its Flow media ID. Prints a summary of the stored provenance record: profile, media ID, Flow project ID, kind (image/video), and the local paths or cloud URIs that were written for that operation.

Without `--profile` the lookup spans **every profile** in the catalog — matching the cross-profile default of `gflow data list`. Pass `--profile NAME` to scope to a specific profile (this is the way to disambiguate the rare case where two profiles share the same Flow `media_id`; the command refuses to guess and lists the candidates annotated with `kind`). See [#87](https://github.com/ffroliva/gflow-cli/issues/87).

```text
gflow data media MEDIA_ID [--profile NAME]

Arguments:
  MEDIA_ID              Flow media UUID (e.g. ddb6ef97-262d-49f4-8269-4a28c0fae6a2). [required]

Options:
  --profile NAME        Scope the lookup to a specific profile.
                        Default: search all profiles.
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

When [`GFLOW_CLI_STORAGE_URI`](EXTERNAL_STORAGE.md) was active for the run, the
same command prints `cloud_uri_1`, `cloud_uri_2`, and so on instead of
`local_path_N` rows.

Exit codes: `0` success, `2` media ID not found in the local database, `16` database error (see exit code table below).

## `gflow models`

Print the image and video model catalog — the source of truth for what
`--model` accepts on the generation commands. Defaults to a Rich table; pass
`--json` for a single machine-readable object.

```text
gflow models [OPTIONS]
```

Per model the catalog reports: `name`, the CLI aliases the matching generation
command's `--model` choice actually accepts, `ref_cap` (max reference images),
plus `default` (image models) and `max_duration` (video models). Because it is
built from the same `Model` / `VideoModel` enums + alias maps the generation
commands use, any alias it prints is guaranteed to be accepted back by
`--model` — a UI populating its model picker from `gflow models --json` can
round-trip every value.

```bash
# Human-readable table
gflow models

# Machine-readable catalog for a worker / model picker
gflow models --json
```

## JSON output (`--json`)

The generation commands (`image t2i` / `image i2i`, `video t2v` / `i2v` /
`r2v`), `auth list`, and `gflow models` accept `--json` for machine-to-machine
use. When set:

- The command emits **one** parseable JSON object (or array, for `auth list`)
  on **stdout** and nothing else — progress chatter is suppressed and structured
  logs go to **stderr**, so `json.loads(stdout)` always succeeds.
- `image t2i/i2i` emits the complete `GeneratedImage` result (`media_name`,
  `workflow_id`, `seed`, `prompt`, `model_name_type`, `aspect_ratio`,
  `dimensions`, `fife_url`, `is_signed_url`) plus the on-disk `local_path`;
  `ref_count` is included on `i2i`. Single-prompt only — `--json` rejects
  multi-prompt batches with a Click usage error.
- `video t2v/i2v/r2v` emits the `VideoResult` (`status`, `command`, `media_id`,
  `generation_status`, `succeeded`, `local_path`, `failure_reasons`,
  `error_message`) plus the request echo (`model`, `mode`, `aspect`,
  `duration`, `count`, `seed`).
- On failure, the command still emits a JSON payload — an RFC 9457
  problem-details object including a `retryable` flag (true for WAF /
  rate-limit / network / timeout) that a worker scheduler can key its
  retry-vs-absorb decision off — and exits with the same non-zero code as the
  Rich path.

The data-layer recorder fires regardless of `--json`, so audit history is
independent of the output channel.

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

## `gflow movie`

Sequential TOML-described multi-scene AI movie generation. New in v0.14.0.

### `gflow movie template`

Writes a starter `movie.toml` template.

```text
gflow movie template [OUTPUT] [--force]
```

* `OUTPUT`: The destination path for the template file (defaults to `./movie.toml`).
* `--force`: Overwrite the destination file if it already exists.

### `gflow movie run`

Generates individual video clips sequentially from a single TOML manifest file, maintaining crash-recoverable state in a local state JSON file.

```text
gflow movie run MANIFEST [--out-dir DIR] [--profile NAME] [--continue-on-error|--fail-fast] [--dry-run] [--stitch]
```

Refer to the complete [Movie Manifests Guide](MOVIE.md) for detailed syntax, character entity creation, and style consistency features.

### Parameters

* `MANIFEST`: Path to your `movie.toml` manifest file (required positional argument).
* `--out-dir DIR`: Output directory for generated video clips and the handoff file. Defaults to `out/` or `GFLOW_CLI_OUTPUT_DIR`.
* `--profile NAME`: Specify Google Flow profile to use.
* `--continue-on-error`: Skip failed scenes and continue generating subsequent scenes (default).
* `--fail-fast`: Terminate immediately on the first failed scene generation.
* `--dry-run`: Parse the manifest, estimate credits, and print the generation plan without calling Google Flow.
* `--stitch`: Run a post-generation preview stitch using ffmpeg to concatenate clips into a single video file.

### Manifest structure (movie.toml)

```toml
title = "My Cinematic Film"
project = "Cinematics"

[style]
look = "3d animation"
mood = "mysterious"
negative = "text, watermark"
prefix = "Cinematic film still, high detail."
suffix = "Shot on IMAX 70mm, unreal engine 5 render."

[style.variants.warm]
suffix = "Golden hour light, warm color grading."

[[characters]]
name = "Stickman"
identity = "text"
voice = "alnilam"

[[scenes]]
id = "scene_01"
action = "A mysterious stickman walks slowly through a dark forest."
framing = "wide"
duration = 5
characters = ["Stickman"]

[[scenes]]
id = "scene_02"
action = "Close up of the stickman looking back in shock."
framing = "close-up"
duration = 5
characters = ["Stickman"]
style_variant = "warm"
```

### Outputs

1. **Clip Files:** Individual video files saved under `<out_dir>/scene_<id>.mp4` (or other formats).
2. **State File:** A `<stem>-state.json` file recording completed operations, seeds, and metadata, allowing a run to resume without wasting credits.
3. **Handoff Manifest:** A `<stem>-handoff.json` file that projects the run's metadata and clip paths, designed for downstream assemblers.

## Recipes

### Burn through a directory of inputs

```bash
mkdir -p out
for img in ./inputs/*.png; do
  name=$(basename "$img" .png)
  gflow video i2v "$img" "Cinematic push-in" --out-dir out
done
```
PowerShell:

```powershell
New-Item -ItemType Directory -Force -Path out | Out-Null
Get-ChildItem ./inputs/*.png | ForEach-Object {
    gflow video i2v $_.FullName "Cinematic push-in" --out-dir out
}
```
### Fan out an image prompt 4-way

```bash
gflow image t2i "variations of a minimalist fox logo" -n 4 --aspect 1:1 --out ./logos/
```

### Run two profiles concurrently

```bash
# Terminal 1
gflow image batch ./batch-a.tsv --profile work

# Terminal 2 (different profile = different Chromium context = OK)
gflow image batch ./batch-b.tsv --profile personal
```

(Same profile concurrently → the second invocation fails fast with `ProfileLockedError`, exit code 11, before any Chrome process starts. Use different profiles or wait.)

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
| `9`  | `TransportTimeoutError` | Browser/API operation exceeded its timeout (incl. an i2v frame UUID not found in the media picker, #287) | Retry; raise the relevant timeout — or, for a frame-UUID miss, verify the UUID belongs to the `--project` passed |
| `10` | `WafRejectionError`   | Flow security layer rejected the request          | Change prompt/request and retry                            |
| `11` | `ConfigurationError`  | Local configuration or browser mode is invalid — includes `ProfileLockedError` (same-profile lease contention: another `gflow`/daemon/MCP call already owns this profile) | Fix the option/env var shown in the error, or wait / use a different `--profile` for lease contention |
| `12` | `AuthLoginTimeoutError` | Browser sign-in was not completed in time       | Re-run login or raise `GFLOW_CLI_AUTH_LOGIN_TIMEOUT`       |
| `13` | `SecurityError`       | Unsafe local profile or secret handling blocked   | Follow the error's safety guidance                         |
| `14` | `AuthBrowserRejectedError` | Google rejected the login browser             | `gflow auth login --browser chrome`                        |
| `15` | `BrowserSessionClosedError` | The automation browser window was closed mid-operation | Re-run; keep the browser window open until the command finishes |
| `16` | `DataStoreError`      | Local database cannot be opened, a migration failed, or the DB schema is newer than the installed gflow-cli | See below                                  |
| `17` | `ModelModeIncompatibilityError` | The chosen video model can't do the requested mode (e.g. `--model omni-flash` with an `i2v` start/end frame — issue #125) | Use `--model veo-lite` (or veo-fast / veo-quality / veo-lite-lp) for `i2v` |
| `18` | `VideoModelSelectionError` | gflow could not select the requested video model in Flow's editor for an `i2v` run (model-picker option not found) | Usually transient — retry; if it persists, Flow's model-picker UI changed (report referencing #125) |
| `19` | `SceneConcatError`    | Server-side scene render/concat failed (`gflow scene --output`) | Retry; the recorded compose survives, so re-render is safe |
| `20` | `FrameExtractionError` | Could not extract the last frame for a video chain link | Check the source video downloaded intact; retry the link  |
| `21` | `ChainPartialError`   | A video chain stopped mid-way; earlier links completed | Resume from the last completed link shown in the error     |
| `22` | `UpscaleUnavailableError` | 4K upscale is gated to Flow **Ultra** accounts (HTTP 403) | Use `--scale 2k`, or upgrade the Flow plan                |
| `23` | `UiSelectorDriftError` | A Flow editor control could not be located — Google changed the frontend (issue #183) | Update gflow-cli; file a bug with the probe name + debug screenshot from the error message |
| `24` | `BrowserEngineUnavailableError` | `GFLOW_CLI_BROWSER_ENGINE=patchright` but the engine is not installed | `pip install 'gflow-cli[patchright]'`, or unset `GFLOW_CLI_BROWSER_ENGINE` |
| `25` | `FlowAgentUiError`    | The profile is on Flow's Agentic UI cohort and the classic media panel is unrecoverable for this operation | Rare since v0.38.0 (#332): the mode controller reliably recovers agentic→classic, so first retry with `--ui-mode classic`; if it persists, see KNOWN_ISSUES on the agentic cohort |
| `26` | `MediaAttributionError` | Generated media could not be reliably attributed to this request (issue #281) | Re-run; a dedicated project with fewer pre-existing assets avoids the ambiguity |
| `27` | `MediaUploadRejectedError` | Flow's upload endpoint refused the input file (`uploadImage` 4xx, issue #287) | Re-encode the image (`ffmpeg -q:v 2 -map_metadata -1`), or reference the asset by its media UUID |
| `28` | `UiModeUnavailableError` | The Flow UI arm this command required (`GFLOW_CLI_UI_MODE`, or inferred — `-i` forces agentic) couldn't be reached after a switch attempt; aborted before submitting — no credits spent (issue #299) | Retry (the cohort flaps per load); try another `--profile`; or relax `GFLOW_CLI_UI_MODE` |
| `29` | `MentionIndexUnavailableError` | An `@mention` was present but the catalog source needed to resolve it (character entities or media assets) failed to load — distinct from an empty index, which is not an error | Check network connectivity (character source) or `GFLOW_CLI_DB_PATH` / filesystem permissions (media source), then retry |
| `30` | `QueueSchemaError`    | A `gflow serve`/MCP worker-queue task payload has an unrecognized `schema_version` or fails validation against the typed request DTOs | Usually means gflow-cli was downgraded after a newer version enqueued the task, or the payload was hand-edited; re-enqueue with a compatible version |
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
gflow video i2v ./initial.png "test" --out-dir out
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
        saved = await client.download_image(image, Path("lake.png"))
        print(saved)

asyncio.run(main())
```

`project_id` defaults to `None`. When omitted, `generate_image()` (and `generate_images_batch()`) call `create_project()` internally. Pass an explicit `project_id` when you want multiple generations to land in the same Flow project.

`download_image()` returns the final write location. That is a local
`pathlib.Path` by default, or a cloud-backed path when
`GFLOW_CLI_STORAGE_URI` is set; see [EXTERNAL_STORAGE.md](EXTERNAL_STORAGE.md).

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
