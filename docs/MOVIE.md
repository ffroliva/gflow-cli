# Movie — multi-scene, character-consistent films

`gflow movie` turns a single TOML manifest into a sequence of generated video
clips that share the **same characters** (face + voice) across every scene. It
is the orchestration layer on top of `gflow character` (entity creation) and
`gflow video` (R2V generation).

> Status: under active development on the movie feature branch. The wire
> protocol and CLI surface below are live-verified but may still change before
> release.

## Quick start

```bash
# 1. Scaffold a manifest
gflow movie template movie.toml

# 2. Edit movie.toml (characters + scenes), then run
gflow movie run movie.toml --profile <chrome-profile>
```

`movie run` is **generate-only** by default: each scene becomes its own clip in
the output directory. Pass `--stitch` for an optional ffmpeg hard-concat preview
(no transitions — not a deliverable).

## Manifest (`movie.toml`)

```toml
schema_version = 1
title = "E2E Stickman"
project = "6ba50219-…"            # reusable Flow project id
output_dir = "./out"

[[characters]]
name = "Stickman"
identity = "entity"               # reuse a Flow CHARACTER entity across scenes
face_prompt = "Simple round stickman, black ink lines, smiley face"
voice = "alnilam"                 # voice baked into the entity at creation

[[scenes]]
id = "summit"                     # stable key — used for resume
action = "stands on a clifftop at sunset, waves at the camera"
framing = "wide"
characters = ["Stickman"]         # which characters appear (drives R2V reuse)
speaker = "Stickman"
line = "We finally made it to the top!"
aspect = "9:16"
model = "veo-lite"
duration = 8
```

On first run, characters with `identity = "entity"` are created once (image
generation — **free**, no credits) and cached. Each scene then generates a clip
that **reuses the same Flow CHARACTER entity** so the character drives every
scene from one identity.

> **Consistency is best-effort, not pixel-exact.** gflow guarantees the *right
> entity rides the wire* (`consistency_method = entity`); the final on-screen
> fidelity is Flow's Veo R2V model, which may reinterpret minimalist or
> hand-drawn references (e.g. a single round body can render as stacked
> circles). For tighter results, use clearer/richer reference images and a
> higher-tier model (`veo-quality`/`veo-fast` rather than `veo-lite`) — the same
> trade-offs you would hit driving Flow's UI by hand.

## Run lifecycle — keep the browser open

`gflow movie run` drives the **real Flow web UI** in a headed Chrome window
(your `--browser chrome` profile). For **each scene** the same browser window
is used end-to-end:

1. **Attach** the character entity to the generation (see below).
2. **Submit** the prompt — this passes reCAPTCHA and **spends 1 video credit**.
3. **Poll** Flow for completion (the clip renders server-side, ~30–90s).
4. **Download** the finished mp4 into `output_dir`.

> **Do not close the browser window while a run is in progress.** Steps 3 and 4
> still use the browser page (polling even brings it to the foreground). If the
> window is closed before a scene finishes downloading, the in-flight scene
> **aborts** — you will see a "Target/page/context closed" or `scene_failed`
> error. **This is expected, not a bug.**

If a run is interrupted (closed window, crash, network drop), it is **safe to
re-run**: the sibling `<manifest>-state.json` records completed scenes (keyed on
`scene.id`) and they are skipped, so the command resumes where it left off. A
versioned handoff manifest (`<manifest>-handoff.json`) is always written at the
end.

> **Future — fire-and-forget.** Flow's status-poll and download endpoints are
> non-generative and *could* be driven browser-free over Bearer REST (no
> reCAPTCHA, no extra credits), letting the browser close right after submit
> while gflow finishes over the API. That mode is **not implemented yet**; today
> the browser is required for the full generate → poll → download cycle.

## Character consistency (how the entity rides)

A scene listing `characters = ["Stickman"]` is generated as **R2V**
(reference-to-video) so the named Flow CHARACTER entity is reused. The entity is
attached by driving Flow's resource picker:

1. Click **Add Media** in the composer (references sub-mode).
2. Switch to the **Personagens** (Characters) tab.
3. **Right-click** the entity tile — addressed by entity id as
   `data-tile-id="fe_id_<entityId>"` — and choose the **include-in-prompt**
   action from the context menu (the `add`-ligature menu item; its caption is
   localized per account language, e.g. "Incluir no comando" on pt-BR or
   "Добавить в запрос" on ru — selectors are locale-free since issue #170).
   *(A plain left-click navigates into the character editor; the inline include
   button on the **Tudo** tab attaches the character's thumbnail as a plain
   image, not the entity.)*

This puts `referenceEntities:[{entityId}]` on the
`video:batchAsyncGenerateVideoReferenceImages` **request**. Flow's **response**
echoes the accepted entity at:

```
media[].mediaMetadata.requestData.videoGenerationRequestData
      .videoGenerationEntityInputs[].entityId
```

gflow asserts this on every entity scene (`_assert_entities_attached`) and
**refuses to report success** if the entity did not ride — a text/image-only
clip is never silently passed off as character-consistent. The recorded
`consistency_method` in the run state is `entity` when the entity rode (vs
`text`).

See [CHARACTER.md](CHARACTER.md) for the underlying entity model and
[CHARACTER_RECON.md](CHARACTER_RECON.md) for the reverse-engineered wire
protocol.

## `movie run` options

| Flag | Default | Effect |
|------|---------|--------|
| `--profile <name>` | default profile | Chrome profile to drive (must be a `chrome`-strategy profile). |
| `--out-dir <dir>` | manifest `output_dir` | Override the output directory. |
| `--dry-run` | off | Print the plan + credit estimate; make **no** API calls. |
| `--fail-fast` / `--continue-on-error` | continue | Stop on the first scene failure, or attempt the rest (default). |
| `--stitch` | off | After generating, hard-concat all clips into one preview mp4 (ffmpeg, no transitions). |

## Credits

- Character creation (image generation) is **free** — no credits, no reCAPTCHA.
- Each **scene** is one video generation = **1 credit**, spent at submit (step 2
  above). A `--dry-run` shows the estimate without spending anything.
