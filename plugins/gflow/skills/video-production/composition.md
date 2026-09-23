# Composition — choosing and chaining commands into a finished video

Companion to [`SKILL.md`](SKILL.md). Tags: **[CONSTRAINT]** engine refuses · **[CALIBRATED]** measured here · **[CONVENTION]** one workable shape · **[UNEXPLORED]** untested.

Per-command syntax lives in the [`gflow-cli` skill](../gflow-cli/SKILL.md). This file is about which command a beat needs, and how the calls join up.

## Free versus paid, because it sets your risk appetite

**Images are free** — they draw on a daily cap, which is a rate limit, not a charge. `image t2i`, `image i2i`, the image slots of `character create`, `instructions`, and `scene create` all cost nothing. **Only video bills.**

So: never accept a weak plate or a weak still to save budget — re-roll it. Conversely every clip is a real spend, which makes the gate *before* a clip the expensive one to skip.

## Which command for this beat

| The beat needs | Command | Notes |
|---|---|---|
| a character reference, a location plate, a prop sheet | `image t2i` | free; text only |
| another angle of the same location, or a still inheriting a set | `image i2i --ref <anchor>` | free; the anchor chain |
| the exact approved frame, animated | `video i2v --initial-frame` | **carries no identity [CONSTRAINT]**; omni-flash unlocks 10 s and `--end-frame` |
| this person, in this place, in motion | `video r2v --reference-entity <one> --ref <plate>` | the only video path carrying identity; composes its own frame |
| a shot continuing past 8 s | `video extend <media_id>` | seeded server-side, motion **and audio** carry |
| a deliberate cut with visual continuity | `video chain` | restarts from an extracted still; audio does **not** carry |
| clips that already exist, joined | `scene create` | free, server-side, per-clip trims |
| a script re-run as it is edited | `movie run` | resumable, one entity per scene |
| no anchor at all | `video t2v` | the only option on lane B |

### The trade that decides i2v against r2v

**`i2v` guarantees the exact frame a human approved.** It pays for that by carrying one image and no identity whatsoever.

**`r2v` is the only video path that carries a character.** It pays by giving up the guaranteed first frame — it composes its own.

Measured both ways on the same beat: reference-to-video lifted a frozen opening from a median motion of 0.50 to 3.94, roughly eight times the still baseline **[CALIBRATED]**. On a different beat, the same command **omitted the staged focal object entirely** and rebuilt the location despite a plate being attached, because it composes rather than inherits **[CALIBRATED]**.

So the rule is conditional. **When the staged object is the mechanism of the shot, use `i2v`.** When the shot needs a specific person moving through a specific place, use `r2v`. Mixing them beat by beat is the intended use, not a compromise.

### extend against chain

`extend` continues **the same shot** — seeded server-side from the source clip, so motion and audio run across the join. `chain` **cuts to a new shot** — it extracts the previous clip's last frame locally and restarts from a still, which is why it needs a fade guard and why audio does not carry.

Use `extend` where a cut would break the effect: a continuous camera move, an unbroken performance, footage timed to a narration beat. Use `chain` when a cut is what you want.

**An extend segment carries about 7 s of real content though 8 s is billed [CONSTRAINT].** Concatenating several leaves a frozen, silent second before every internal seam. Render without `-o` and trim each segment to `0-7` in `scene create`.

## Chain 1 — one-off sequence

A scene, an audition, a montage. Clip per beat, assembled locally or server-side.

```bash
gflow project create --name "<piece>" --json > project.json     # redirect, never pipe to head

# assets (free)
gflow character create --project P --name Lead \
  --face-prompt "<canon, plain background>" --body-prompt "<wardrobe, no print>" --voice <preset>
gflow image t2i "<location, geometry only, no people>" --aspect 16:9 --project P -o plate_wide.png
gflow image i2i "<same room, reverse angle>" --ref <ANCHOR_UUID> --aspect 16:9 --project P -o plate_rev.png

# trial ONE beat, gate it, fix the template, then continue in pairs
gflow video r2v "<style + geometry + cast + setup + action + dialogue + avoid>" \
  --reference-entity <LEAD_ID> --ref <PLATE_UUID> \
  --model omni-flash --duration 8 --aspect 16:9 --count 1 --project P --json

python clip_qa.py clips/

# assemble (free)
gflow scene create <wf1> <wf2> <wf3> -o final.mp4 --project P
```

## Chain 2 — manifest-driven, re-run as the script is edited

The shape for a pipeline: a stable id per slot, one entity, resumable.

```bash
gflow movie template movie.toml          # scaffold
gflow movie run movie.toml --dry-run     # parses, prices, spends nothing
gflow movie run movie.toml --continue-on-error --out-dir ./out
```

Manifest essentials: a fixed `project`; one `[[characters]]` block with `identity = "entity"` so the same character is created once and reused; one `[[scenes]]` block per slot with a **stable `id`**; and an explicit `model` on every scene so the duration is validated at parse time rather than mid-spend.

**Resume is by content hash, not by position [CONSTRAINT].** A sibling state file records each completed scene against a hash of its fully composed prompt. On re-run, an unchanged scene is skipped and never re-billed; only edited ones regenerate.

Three consequences worth planning around:

- **Scene ids must be stable across runs.** Reordering or inserting rows renumbers slots and re-bills everything after the insertion point.
- **Editing the shared style block re-hashes every scene [CONSTRAINT].** A one-word style tweak silently regenerates the whole manifest. Keep style edits off script-only runs.
- **`--stitch` is a hard concat preview, not a deliverable.** Assemble properly afterwards.

Two-speaker scenes use per-character entries rather than the single-speaker shorthand — and remember one face-bearing reference per generation, so `characters = [A, B]` on one scene is the same 400 as two `--reference-entity` flags.

## Chain 3 — a shot longer than eight seconds

```bash
gflow video r2v "<opening beat>" --reference-entity <ID> --ref <PLATE> \
  --model omni-flash --duration 8 --project P --json          # note the media_id

gflow video extend <media_id> "<next beat>" "<beat after>" --project P --aspect 16:9

gflow scene create <wf1> <wf2>:0-7 <wf3>:0-7 -o shot.mp4 --project P
```

Trim every extension to `0-7`; see the 7-second constraint above. `1:1` is refused — there is no square extend model.

## Chain 4 — the composited approved frame

The pattern to reach for when a shot needs **both** a specific person and a specific staged object, and the object is the point of the shot. `r2v` cannot serve it: it carries identity but composes its own frame, and has dropped a staged focal object outright. `i2v` guarantees the frame but carries no identity. So build the frame first, with identity baked in, then animate it.

```bash
# 1. compose the still: entity for the face, plates for the place and the prop.
#    image i2i takes REPEATED --ref up to the model's cap (nano2: 10), so a plate
#    and a prop sheet in one call is the normal case, not an advanced one. Free.
gflow image i2i "<close crop, the action's start state, stated exactly>" \
  --reference-entity <CHARACTER_ID> --ref <PLATE_UUID> --ref <PROP_UUID> \
  --model nano2 --aspect 16:9 --project P -o frame_approved.png

# 2. a human looks at it. This is the gate the whole pattern exists for.
#    Crop to the prop and confirm the beat's start state is literally true in the
#    frame — a prop touching a surface it must leave will animate as attached to it.

# 3. animate the approved frame. No identity flag: the identity is in the pixels.
gflow video i2v --initial-frame frame_approved.png "<what happens next>" \
  --model omni-flash --duration 8 --aspect 16:9 --count 1 --project P --json
```

Use it for a mechanism-critical prop, a precise start state, or a shot that must match the previous clip's last frame. The cost is one extra free image and one human look.

## Chain 5 — assembly only

```bash
gflow data list videos --json                  # workflow ids
gflow scene create <wf1> <wf2>:2.0-6.5 <wf3> -o cut.mp4 --project P
```

Free, server-side, no re-encode, per-clip trim windows in seconds.

## Local assembly, when lane or format rules out `scene create`

Join with the **concat filter**, never the demuxer:

```bash
ffmpeg -i card.mp4 -i clip1.mp4 -i clip2.mp4 \
  -filter_complex "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[v][a]" \
  -map "[v]" -map "[a]" -r 24 -fps_mode cfr -c:v libx264 -c:a aac -ar 48000 final.mp4
```

**Mixed frame rates through the concat demuxer produced 164 s of video under 171 s of audio — lips 4 % ahead by the end [CALIBRATED].** Every segment, title cards included, must be built at the clips' frame rate. Verify with `clip_qa.py final.mp4`; the stream lengths must agree within 0.1 s.

Title cards need one `drawtext` filter **per line** — a newline inside a single `drawtext` renders literally as "nn".

Captions come from the **script**, not from the transcript, so the reader sees the correct line even where the engine fluffed the delivery. Where speech is added in post and does not exist yet, the caption timing has to come from the voiceover once recorded; a per-shot even split is a placeholder, not a deliverable **[CONVENTION]**.

Strip generated audio with `-an` where the sound is unwanted — the engine always produces some **[CONSTRAINT]**.

## Pacing, sessions and what wastes money quietly

- **Paid calls in the foreground, roughly two beats per invocation [CALIBRATED].** A detached background run lost a clip mid-poll after the credit was spent.

  **This is about interactive iteration, not a ban on automation.** A scheduled pipeline cannot have a human per call, and does not need one — what it needs is the same guarantee by other means:

  - Gate on a **canary scene** each run: generate one, check it, and stop the rest if it fails.
  - Prefer `--fail-fast` over `--continue-on-error` unattended, so a broken template bills once instead of once per scene.
  - **Reconcile rather than retry.** After any interrupted run, list the project's media and compare against the state file; a run cut off between submit and download may already have billed.
  - Run the job in the foreground **of its own scheduled process**, one profile, never two overlapping invocations.
  - Keep a credit ceiling per run and fail the job rather than the budget.

  The rule the incident actually supports is: never detach a paid call from the process that is waiting on it. A cron job that blocks on its own run satisfies that; a background job inside an interactive session does not.
- **One profile, no parallel generations.** Concurrent runs on one profile collide on its lease.
- **Never auto-retry into a refusal.** A refusal aborts and keeps what completed; re-running into a block only raises the profile's risk score.
- **`--dry-run` before any manifest run.** It prices the plan and catches model-duration mismatches without opening a browser.
- **A run interrupted after submit but before download may still have billed.** Reconcile by listing media rather than re-running blind.
- Keep prompts near the calibrated length; over-long prompts have returned 400 as a wire-format error whose advice is misleading.
