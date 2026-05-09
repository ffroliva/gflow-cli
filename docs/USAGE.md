# Usage

CLI command reference. For environment variables see [CONFIGURATION](CONFIGURATION.md). For auth see [AUTHENTICATION](AUTHENTICATION.md).

> ⚠️ **Status.** Most commands below are CLI surface decisions documented for v0.1. The route wiring lands in Phase 2 (images) and Phase 3 (videos) — see [PLAN](../PLAN.md). Until then the CLI returns `NotImplementedError` for the substantive subcommands.

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

  image     Image generation (Imagen via Flow).
    generate                    Generate one image from a prompt.
    batch                       Generate many images from a TSV manifest.

  video     Video generation (Veo I2V via Flow).
    generate                    Generate one video from a start image + prompt.
    batch                       Generate many videos from a TSV manifest.
    i2v                         Convenience: upload + generate + poll + download.
```

Global flags:

- `-V`, `--version` — print version and exit.
- `-v`, `--verbose` — log at DEBUG level.
- `--profile NAME` — pick which Google profile to use (any subcommand).

## `gflow auth`

See [AUTHENTICATION § Commands](AUTHENTICATION.md#commands).

## `gflow image generate`

```text
gflow image generate -p "<prompt>" [OPTIONS]

Options:
  -p, --prompt TEXT         Image prompt.                          [required]
  --aspect [1:1|9:16|16:9|4:3|3:4]
                            Aspect ratio.                       [default: 1:1]
  --count INTEGER           Number of images (1–4).               [default: 1]
  --output PATH             Output file or directory. If omitted,
                            files land at $FLOW_CLI_OUTPUT_DIR/images/<date>/.
  --async                   Don't wait — print job_id and return.
  --profile NAME            Profile name. [default: $FLOW_CLI_PROFILE]
```

**Examples:**

```bash
# Single image, default aspect 1:1
gflow image generate -p "a serene mountain lake at sunset"

# 3 portraits at 3:4
gflow image generate -p "black-and-white portrait of a fisherman" --aspect 3:4 --count 3

# Specific output file
gflow image generate -p "a moody alley" --output ./out/alley.png

# Don't wait — just queue and return job_id
JOB=$(gflow image generate -p "..." --async)
gflow status "$JOB"        # poll later
```

When `--count > 1`, output paths get `_1`, `_2`, ... suffixes. If `--output ./out/foo.png` is passed with `--count 3`, you'll get `./out/foo_1.png`, `./out/foo_2.png`, `./out/foo_3.png`.

## `gflow image batch`

```text
gflow image batch MANIFEST [OPTIONS]

Arguments:
  MANIFEST                  Path to a TSV manifest (or `-` for stdin).

Options:
  --out-dir PATH            Override the default output directory.
  --concurrency INTEGER     Max concurrent generations.           [default: 1]
  --profile NAME            Profile name. [default: $FLOW_CLI_PROFILE]
```

### Manifest format

Tab-separated, optional header (lines starting with `#` are ignored):

```tsv
# prompt	count	aspect	output_path
a serene mountain lake at sunset	2	1:1	./out/lake-{i}.png
black-and-white portrait of a fisherman	1	3:4	./out/fisherman.png
forest at dawn		16:9	
```

| Column | Meaning | Required | Default if blank |
|---|---|---|---|
| `prompt` | The image prompt | yes | — |
| `count` | Number of variants | no | `1` |
| `aspect` | Aspect ratio | no | `1:1` |
| `output_path` | Per-row output path; `{i}` is replaced by 1-based index | no | `<out_dir>/<job_id>_<i>.png` |

**From stdin:**

```bash
cat manifest.tsv | gflow image batch -
```

## `gflow video generate`

```text
gflow video generate -i IMAGE -p "<motion prompt>" [OPTIONS]

Options:
  -i, --start-image PATH    Start frame (PNG/JPG).                 [required]
  -p, --prompt TEXT         Motion prompt.                         [required]
  --end-image PATH          Optional end frame for transition I2V.
  --aspect [9:16|16:9|1:1]  Aspect ratio.                       [default: 9:16]
  --output PATH             Output mp4. Default:
                            $FLOW_CLI_OUTPUT_DIR/videos/<date>/<job_id>.mp4
  --async                   Don't wait — print job_id and return.
  --profile NAME            Profile name. [default: $FLOW_CLI_PROFILE]
```

**Examples:**

```bash
# Standard short-form vertical
gflow video generate -i ./input.png -p "Slow cinematic push-in"

# Specific output
gflow video generate -i ./hero.png -p "Pan left across the table" -o ./out/hero.mp4

# With end frame (transition)
gflow video generate -i ./start.png --end-image ./end.png -p "Smooth transition"
```

## `gflow video batch`

Same shape as `gflow image batch`, with TSV columns:

```tsv
# start_image	prompt	end_image	aspect	output_path
./inputs/s1c1.png	Slow push-in			9:16	./out/s1c1.mp4
./inputs/s1c2.png	Pan left	./inputs/s1c2_end.png	9:16	./out/s1c2.mp4
./inputs/s1c3.png	Crash zoom			9:16	
```

## `gflow video i2v`

Convenience alias for `upload + generate + poll + download` in one shot. Most common entry point for ad-hoc usage.

```text
gflow video i2v IMAGE PROMPT -o OUTPUT [OPTIONS]
```

```bash
gflow video i2v ./input.png "Slow cinematic push-in" -o out.mp4
```

Exits 0 on success, non-zero on failure with a remediation hint in stderr.

## `gflow status` / `gflow download` *(planned)*

For async workflows triggered with `--async`:

```bash
JOB=$(gflow image generate -p "..." --async)
# ... do other work ...
while ! gflow status "$JOB" | grep -q succeeded; do sleep 5; done
gflow download "$JOB" -o ./out.png
```

(Polled internally by the synchronous commands, so most users won't need these directly.)

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

### Generate a manifest from a TSV with prompts already in it

```bash
gflow image batch ./prompts.tsv --out-dir ./generated/
```

### Run two profiles concurrently

```bash
# Terminal 1
gflow image batch ./batch-a.tsv --profile work

# Terminal 2 (different profile = different Chromium context = OK)
gflow image batch ./batch-b.tsv --profile personal
```

(Same profile concurrently → second invocation fails with "Chromium profile locked". Use different profiles or wait.)

### JSON logs for piping into Loki/Datadog

```bash
FLOW_CLI_LOG_FORMAT=json gflow image generate -p "..." 2>&1 | jq .
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Generic error (with stderr message) |
| `2` | Bad usage / missing required argument / no auth |
| `3` | Auth expired (run `gflow auth login`) |
| `4` | Rate-limited / quota exhausted |
| `5` | Provider unavailable (Flow returned 5xx) |
| `64..125` | Reserved for future granular categorisation |

Use them to branch in shell scripts:

```bash
if ! gflow video i2v ./in.png "test" -o out.mp4; then
  case $? in
    3) echo "Re-auth needed"; gflow auth login; exit 1 ;;
    4) echo "Quota hit; cooling off 1h"; sleep 3600; exec "$0" "$@" ;;
    *) echo "Unknown failure"; exit 1 ;;
  esac
fi
```

## See also

- [CONFIGURATION](CONFIGURATION.md) — env vars, output paths, defaults
- [AUTHENTICATION](AUTHENTICATION.md) — auth flow + multi-account
- [ARCHITECTURE](ARCHITECTURE.md) — internal structure (for contributors)
- [PLAN](../PLAN.md) — what ships in which phase
