---
name: video-production
version: "1.0"
description: Use when the user wants a finished multi-shot or dialogue video out of gflow — a scripted scene, a talking-head piece, an audition or rehearsal reference, a short film — and asks for consistent actors, a consistent room, several camera angles, spoken lines, or joining clips. Also use when clips came back with a film-strip border, a room that changes between shots, rushed or cut-off dialogue, a person-policy refusal, or a RecaptchaError/exit 36 on a generation.
---

# video-production

**Core principle:** lock the beat sheet, the room, the cast and the words on paper first; spend video credits only on beats that already passed the free checks. Companion to the `gflow-cli` skill (command surface) and `known-issues`.

## Prerequisites

**Everything gflow** — Python 3.11+, `uv`, an installed `gflow`, Playwright Chromium, a signed-in profile, Flow access — is the [`gflow-cli` skill](../gflow-cli/SKILL.md)'s Prerequisites section. Run its checks; do not restate them. The Step 0 host probe below drives Chrome through Playwright against gflow's own profile directory, so it inherits all of them.

This skill adds three of its own:

1. **`ffmpeg` and `ffprobe` on PATH, 5.0 or newer** — `ffmpeg -version`. The assembler uses `-fps_mode cfr`, which does not exist before 5.0.
2. **An ffmpeg built with `libass` and `libfreetype`** — the same version banner lists the enabled libraries. Burned subtitles (the `subtitles` filter) and title cards (`drawtext`) are simply absent from minimal or "essentials" builds, which is the usual Windows trip.
3. **`faster-whisper`, only if you want the transcript gate** — `python -c "import faster_whisper"`. **Nothing in gflow installs it.** `pip install faster-whisper`, then the first run downloads `base.en` (~75 MB, needs network once, cached after). Without it you lose the word-hit check and keep every other gate.

`clip_qa.py`, beside this file, needs **only ffmpeg, ffprobe and the Python standard library** — no numpy, no model, no network — so the fluidity, lip-sync and A/V-drift gates still run on a machine where the transcript gate cannot.

## Step 0 — check the host, or the whole plan is dead

Open `https://labs.google/fx/tools/flow/project/<id>` in the profile's Chrome and read the final URL.

| Lands on | Lane | Usable on gflow 0.67.0 |
|---|---|---|
| `labs.google/fx/…` | **A, full** | everything below |
| `flow.google.com/project/…` | **B, text only** | `video t2v --project` only. `image`, `character create`, `i2v/r2v`, `extend`, `scene create`, `movie run` exit 36 — a free `image t2i` there dies as `RecaptchaError` (that IS the migration, gflow-cli#639) |

Do not write a plan that starts with `character create` or `movie run` before this check. Create the project with `gflow project create --name <piece> --json > out.json` (redirecting; piping through `head` kills the process before the JSON prints).

## Step 1 — beat sheet (one line per clip)

Fields: `id`, `setup`, `action`, `lines` as `[speaker, delivery, text]`, `duration`, `model`.

- **Durations**: omni-flash 4/6/8/10 s (the only model whose `--duration` is honoured on every cohort); Veo 3.1 models 4/6/8 where the account renders the control. 8 s is the safe default, 10 s (omni-flash) for a long speech.
- **≤ 2.5 spoken words per second**: 8 s ≈ 18 words, 10 s ≈ 24. Over that, the actor rushes and the last sentence is cut.
- **Never rewrite the script to fit.** Split a long speech across beats; keep the author's words verbatim (a rehearsal reference with paraphrased lines is useless).
- Two speakers per beat is fine, up to ~4 short alternating lines, written in order with a delivery note each; never three speakers.
- One moment per clip; a beat with ~1 s of action in an 8 s clip invents the rest.
- Close-ups for reaction lines, medium shots for long speeches (lip sync is more forgiving).

## Step 2 — room: fixed geometry + named setups

Flow has no environment entity; the room is text plus images attached per shot.

- **Fixed-geometry paragraph**, verbatim in every prompt: furniture, doors, windows left-to-right, who is screen-left (180° axis, camera never crosses).
- **Named setups** (`A_WIDE`, `B_MED_HUNT`, `C_CU_BELLA`, `D_TWO`), each naming the camera position and what is behind each actor; every beat references one. Arc: wide → medium → close-up → two-shot at the climax.
- Lane A plates: `image t2i` the anchor angle (geometry only, no people), then each other angle with `image i2i --ref <ANCHOR_UUID>`; one plate per shot, the angle whose geometry matches; a plate for every region on screen or `r2v` invents it. Filenames per piece (`s024_env_window.jpg`), never generic.
- **Nothing in frame carries text**: no "door marked PRINCIPAL", no posters with writing, no band logos. Whatever carries text invents text.

## Step 3 — cast

- Lane A: `gflow character create --project P --name <Name> --face-prompt "<face, plain background>" --body-prompt "<wardrobe token>" --voice <preset>` (`gflow character voices` lists the presets with samples); reuse with `@Name` or `--reference-entity <id>`. Lane B: the same description verbatim in every prompt.
- Face prompt = role noun + hair + face marks, **with no age words at all** — write "a lanky young man with messy dark hair and faint stubble", "a woman with a neat blonde ponytail", "his adult granddaughter". Any number or age band trips the person policy on image and character generation, and minors are refused outright; cast school roles as adults. (Copy the wording above, not the red-flag list.)
- **Wardrobe token** repeated in every shot; entities lock FACE, not clothing. Pin garments **plain, no print**.
- **ONE face-bearing reference per generation.** Two `--reference-entity`, or an entity plus a portrait/frame `--ref`, returns HTTP 400 (`WireFormatError` with a misleading "simplify the prompt" hint). `movie run` with `characters = ["A", "B"]` on one scene is the same mistake.
- **Two actors in one scene**: give the entity to whoever carries the beat (the speaker of the long line, the face in the close-up); the other rides on the verbatim canon plus wardrobe token, in profile or turned away in that shot. Alternate across beats so each actor is entity-locked in their own close-ups, which is where the audience reads identity; keep the non-entity description byte-identical between beats.

## Step 4 — prompt shape

`style → setting → geometry → cast → setup → action → dialogue → avoid`.

- Style: "full-frame 16:9 image edge to edge, natural soft light, shallow depth of field". **Never** "35mm film", "IMAX 70mm", "film grain", "Unreal Engine render" — format words draw a sprocket-hole border or change the medium.
- Camera words the model knows: eye-level, low/high angle, close-up, medium, wide, over-the-shoulder; static, pan, tilt, dolly, truck, zoom, handheld, rack focus.
- Dialogue: attribution and delivery **before** the words — `HUNT says, bored, drawling: Will you relax?` Delivery levers: volume, emotional state, pace, register, physical condition, accent.
- Avoid list = artefacts only: film-strip border, letterbox, vignette, subtitles, captions, readable text, extra people. Negations naming an action do not hold.
- 1,100–1,400 characters.

## Step 5 — which command, per beat

| Beat needs | Command |
|---|---|
| canonical, anchor plate, prop sheet (free) | `image t2i` |
| another angle of the same room (free) | `image i2i --ref <anchor UUID>` |
| the exact approved frame animated | `video i2v --initial-frame` (no identity; omni-flash unlocks `--duration 10`, `--end-frame`) |
| this person, this place, in motion | `video r2v --reference-entity <ONE> --ref <env> [--ref <prop>] --model omni-flash --duration 10 --count 1 --project P --json` |
| continue the same shot past 8 s | `video extend <media_id> "<next lines>" --project P` (audio carries; segment holds ~7 s, trim `0-7`) |
| a cut with continuity | `video chain` (restarts from a still, no audio carry, no omni-flash) |
| join clips (free) | `scene create wf1 wf2:0-7 … -o out.mp4 --project P` |
| scripted piece with one entity per scene | `movie run movie.toml` (`--stitch` is a preview, not a deliverable) |
| lane B | `video t2v "<prompt>" --model omni-flash --duration 8|10 --aspect 16:9 --count 1 --project P -o clip.mp4 --json` |

Model → refs, never refs → model. **Paid calls in the foreground, two beats per call**; a detached/background run lost a clip mid-poll. Trial one beat, check it, fix the template, then the rest. Never loop a retry into a refusal.

## Step 6 — gates

Before spend: prompt length; no ages; one face ref; words/second; text-free frame; the beat has motion for its whole length.

After each clip: `ffprobe` duration; 1 fps frames viewed by eye (faces match canon, wardrobe token, geometry matches the setup, no text, no third person, no border); local speech-to-text transcript (faster-whisper `base.en`) word-hit ≥ 70 %; mean volume > −40 dB.

Fluidity for dialogue shots: the whole-frame `tblend` median (calibrated on moving scenes, > 1.0) is blind to a locked-off talking head and reads 0.3–0.9 on perfectly live clips. Gate instead on **speech onset ≤ 1.6 s** (`silencedetect`) and **face-region motion**: crop the centre-top 60 % × 75 %, `tblend` median, 10th percentile > 0.15 (a held frame sits near 0). Inspect the two lowest clips at 2 fps by eye before re-rolling anything.

**Lip sync, per clip.** Cross-correlate the face-region motion against the audio envelope over ±0.6 s; the lag of the peak is the drift. Three things make the difference between a number and noise, each learned by measuring:

- **Compare like with like.** The motion series is a per-frame difference (spiky); the audio must therefore be an envelope of the same shape. Smooth the motion with a ~5-frame moving average and take audio RMS as **linear amplitude, not dB** — silence in dB is a large negative outlier that dominates the correlation. Raw difference against dB level measured r ≈ 0.0 at every lag; the corrected pair measures r = 0.6–0.9 on a single.
- **Trust the peak only if it stands up.** Require peak r ≥ 0.3, peak-minus-zero-lag prominence ≥ 0.05, and reject a peak pinned to the window edge. A flat correlation surface still has an argmax: one clip reported a 0.208 s "lag" whose peak stood 0.04 above zero lag, and another peaked at the boundary because it aligned line one's mouth with line two's audio. Both are no-opinion, not drift.
- **Sync is only measurable where one person talks and little else moves.** Singles give r = 0.6–0.9; a busy two-shot gives r ≈ 0.1. **A low r means the shot cannot be measured, never that it is in sync.**

Pass window: −0.045 s (audio early) to +0.125 s (audio late), the ITU-R BT.1359 detectability limits — the ear tolerates late sound far better than early. **Prove the detector before believing it**: re-encode a known-good clip with `adelay=200:all=1` and confirm it reports +0.2 s. Without that check a silently broken correlation reports every clip as perfect.

All of the above is implemented in **[`clip_qa.py`](clip_qa.py)** beside this file — run it rather than rebuilding it:

```bash
python clip_qa.py <clips_dir>            # every <xx00>.mp4: fluidity, sync, A/V drift
python clip_qa.py final.mp4              # the assembled cut (skips the per-shot gates)
python clip_qa.py --selftest <clip.mp4>  # prove the detector on a known-good single
```

This is worth running: on an 18-clip set where every clip had already passed the transcript, loudness and frame checks, it found two with 0.167 s and 0.333 s of audible audio lag.

Fail → delete the clip, change ONE thing, re-check. Second identical failure → restage or delete the beat.

## Step 7 — assemble and review

Lane A: `scene create` with trims. Lane B: ffmpeg with title cards (one `drawtext` per line; `\n` inside one renders as "nn") and burned `.srt` subtitles from the beat timings, joined with the **concat filter** (`-filter_complex … concat=n=N:v=1:a=1`), every segment at the clips' frame rate. The concat *demuxer* with a 25 fps card among 24 fps clips produced a 164 s video track under 171 s of audio — lips drift ahead by 4 %. **Sync gate**: `ffprobe -show_entries stream=codec_type,duration` on the cut; video and audio durations must match within 0.1 s. Ship a local `review.html` beside the cut: final video, every clip with prompt, lines, transcript, metrics, frames.

## Red flags — stop and re-plan

- The plan opens with `character create` / `movie run` and nobody has looked at the final URL.
- "teenage", "schoolgirl", "in her 20s", "about thirty" anywhere in a prompt.
- A door, sign, poster or T-shirt described with words on it.
- "35mm", "IMAX", "film grain", "Unreal Engine", "8K" in the style block.
- Two `--reference-entity` flags, or `characters = [A, B]` on one scene.
- A beat over 2.5 words/second, or a script line you shortened "to fit".
- "Launch all remaining clips in a background job."

| Rationalization | Reality |
|---|---|
| "Both faces need to be consistent, so both entities go in" | The second entity is the 400. One entity plus a role noun holds better than a refused generation. |
| "The room description is in every prompt, that is enough" | Without named setups and a left-to-right geometry line the model re-invents the door on the first new angle. |
| "Trim the monologue so it fits 8 s" | Split it into two beats. The words are the deliverable. |
| "Kick off the batch and check in the morning" | One template bug repeats twelve times at twelve credits. Trial, check, then pairs. |
