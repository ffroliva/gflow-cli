---
name: video-production
version: "2.0"
skillopt_epoch: 0
description: >
  Use when the user wants a finished video out of gflow rather than a single clip — a scripted scene, a talking-head or dialogue piece, an explainer, a product montage, a story sequence, an audition or rehearsal reference, a short film — or asks for consistent actors, a consistent location, a specific prop that must not change, several camera angles, captions or subtitles, or joining clips into one file. Also use when clips came back wrong: a film-strip border, a room that changes between shots, a prop that morphs, rushed or cut-off speech, audio out of sync, a person-policy refusal, or exit 36 / RecaptchaError on a generation.
optimization_notes: |
  Known weak spots, each observed in a scored rollout. Targets for epoch 1+:
  - `--duration` passed without `--model`: binds veo-lite, which renders no duration control, exit 2
  - `--ref` passed with `--model veo-quality`: that model's reference cap is 0, not 3
  - Multi-angle sets generated as N independent `t2i` calls, then drift accepted as unavoidable
  - "There is no `gflow project create`" — agents mint a project by burning a placeholder generation
  - `nano-pro` used for bulk image work; it is daily-capped, `nano2` is the batch model
  - Silent-video assumption: Veo always generates audio, a "silent" clip carries invented room tone
  - No acceptance gate after generation — clips judged by looking at frame 0 only
  - Speech length never budgeted, so lines are rushed or truncated inside the clip
  - Two face-bearing references in one generation (two entities, or entity + portrait)
---

# video-production

**Core principle: control is everything.** Every guard-rail you put in front of the engine is drift you do not pay for later. Lock the shape, the cast, the location and the words before spending, then gate every clip on evidence rather than impression.

This skill covers **composing gflow into a finished video**. It does not restate the command surface — that is the [`gflow-cli` skill](../gflow-cli/SKILL.md), which owns per-command syntax, flags and single-shot recipes. Load that one for "how do I call `t2v`", this one for "how do I turn a script into a film that holds together".

## How to read the claims in this skill

Every non-obvious statement is tagged. **This is one calibrated approach, not the only one.** Other people drive this engine with techniques not tracked here; absence from this document means untested, never forbidden.

| Tag | Meaning | You may |
|---|---|---|
| **[CONSTRAINT]** | the engine refuses, fails, or silently drops the request | not deviate |
| **[CALIBRATED]** | measured here, sample size and conditions stated | deviate with evidence |
| **[CONVENTION]** | one shape that works; alternatives exist | deviate freely |
| **[UNEXPLORED]** | known to exist, not tested here | try it, then add a task |

## When NOT to use this skill

A single clip with no continuity requirement, one image, or a pure command-syntax question — use the `gflow-cli` skill. Editing or grading existing footage — this is generation, not post. Any engine that is not Flow.

## Prerequisites

**Everything gflow** — Python 3.11+, `uv`, an installed `gflow`, Playwright Chromium, a signed-in profile, Flow access — belongs to the [`gflow-cli` skill](../gflow-cli/SKILL.md)'s Prerequisites. Run its checks; do not restate them.

This skill adds three:

1. **`ffmpeg` and `ffprobe` on PATH, 5.0 or newer** — `ffmpeg -version`. The assembly step uses `-fps_mode`, which does not exist before 5.0.
2. **An ffmpeg carrying `libass` and `libfreetype`** — the same banner lists enabled libraries. Burned subtitles (`subtitles`) and title cards (`drawtext`) are absent from minimal or "essentials" builds. Usual Windows trip.
3. **`faster-whisper`, only if you want the transcript gate** — `python -c "import faster_whisper"`. **Nothing in gflow installs it.** First run pulls `base.en`, ~75 MB, once. Without it you lose the word-hit check and keep every other gate.

`clip_qa.py`, beside this file, needs **only ffmpeg, ffprobe and the standard library** — so the fluidity, lip-sync and A/V-drift gates still run where the transcript gate cannot.

## Step 1 — intake, before anything else

The plan changes completely with the answers. Ask, or read them from the brief; do not assume.

| Question | Why it changes the plan |
|---|---|
| What is the deliverable, and who watches it? | a rehearsal reference tolerates flaws a client cut does not |
| Does anyone speak **on camera**? | decides dialogue budgeting, lip-sync gating, and i2v-vs-r2v |
| Is audio generated, added later, or discarded? | Veo always generates audio **[CONSTRAINT]** — see below |
| Captions: none, burned-in, or a sidecar file? | burned-in needs `libass` and a timing source |
| One location or several? How many camera angles each? | drives the plate-chaining work in `consistency.md` |
| Recurring people? How many in frame at once? | one face-bearing reference per generation **[CONSTRAINT]** |
| A prop that must not change? | needs its own sheet with a scale anchor |
| Aspect, total length, credit ceiling | 16:9 or 9:16; clips are 4/6/8, or 10 on omni-flash |
| One-off, or a repeatable pipeline? | decides the production shape in `composition.md` |

**Veo always generates audio [CONSTRAINT].** There is no silent mode and omitting sound from the prompt does not produce silence. A "silent" montage returns invented room tone under every shot. If the audio is unwanted, strip it at assembly (`-an`) rather than hoping for a quiet clip.

## Step 2 — check the host, or the whole plan is dead

Load `https://labs.google/fx/tools/flow/project/<id>` in the profile's Chrome and read the **final** URL.

| Lands on | Lane | What runs there |
|---|---|---|
| `labs.google/fx/…` | **A, full** | everything |
| `flow.google.com/project/…` | **B, partial** | `video t2v --project` and `video i2v --initial-frame <local file> --project`. Everything else — `image`, `character create`, `r2v`, `extend`, `scene create`, `movie run`, and i2v by media UUID / `@Name` or with `--end-frame` — exits 36 **[CONSTRAINT]** |

Lane B grows as forms are ported, so **confirm the row rather than trusting it**: the
maintained list is [CONFIGURATION § `GFLOW_CLI_FLOW_HOST`](../../docs/CONFIGURATION.md#gflow_cli_flow_host)
and the [#639 entry in KNOWN_ISSUES](../../KNOWN_ISSUES.md). Exit 36 on a form the table
says is ported is a regression worth filing, not the environment.

An unported command on lane B exits **36**, non-retryable, with a message naming the
migration. A `RecaptchaError` instead means one of two other things: on gflow ≤ 0.68.0 the
migration guard ran *after* the reCAPTCHA mint on the image path, so image commands died
as exit 1 there (gflow-cli#673, fixed); on any build, a `RecaptchaError` right after
`gflow auth login` is the cookie harvest keyed on the old host (gflow-cli#644). Either
way the first move is the same: **read the final host URL before anything else.** Do not
plan entities or plates before this check.

**There is a `gflow project create` [CONSTRAINT].** `gflow project create --name <piece> --json > out.json`, redirected to a file, never piped through `head`, which truncates the process before the JSON prints. Do not mint a project by burning a placeholder generation, and do not scrape the id from a browser URL.

## Step 3 — the production shape

Pick one; each has a worked command chain in **[`composition.md`](composition.md)**.

| Shape | When | Driver |
|---|---|---|
| One-off sequence | a scene, an audition, a montage | shell, clip per beat |
| Manifest-driven | a script that will be re-run as it is edited | `movie run` with a stable scene id per slot |
| Continuous shot | one camera move longer than 8 s | `video extend` |
| Deliberate cut with continuity | a new angle that must match the last frame | `video chain` |
| Assembly only | clips already exist | `scene create` (free) |

## Step 4 — lock the assets before spending

Full method in **[`consistency.md`](consistency.md)**. The short form:

- **People** are Flow CHARACTER entities, attached with `--reference-entity` or `@Name`. Identity.
- **Locations and props** have no entity type **[CONSTRAINT]** — they are images attached per shot with `--ref`. Look.
- **One face-bearing reference per generation [CONSTRAINT].** A second entity, or an entity plus a portrait, returns HTTP 400 reported as a wire-format error. Carry other people as role nouns in prose.
- **Multi-angle locations must be chained, not generated in parallel [CALIBRATED]** — anchor angle by `t2i`, every other angle by `i2i --ref <anchor>`. Independent calls from the same paragraph produce different rooms.
- Attaching by media UUID buys asset identity, never scene coherence.

## Step 5 — beat sheet

One row per clip: id, camera setup, action, lines with delivery, duration, model, references.

- **Durations are 4/6/8, plus 10 on omni-flash [CONSTRAINT].** `--duration` requires an explicit `--model`; omitted, it binds `veo-lite`, which renders no duration control, and exits 2.
- **≤ 2.5 spoken words per second [CALIBRATED, 25 clips, one model]** — 8 s ≈ 18 words, 10 s ≈ 24. Above it, delivery rushes and the last sentence is cut.
- **Never rewrite the script to fit.** Split a long speech across beats; the words are the deliverable.
- Two speakers per beat is fine with the order explicit; three is where sync breaks **[CALIBRATED]**.
- One moment per clip. A beat with a second of action inside eight seconds invents material to fill the rest **[CALIBRATED]**.
- Named camera setups and a declared 180° axis: see `consistency.md`.

## Step 6 — prompt shape

`style → setting → geometry → cast → setup → action → dialogue → avoid`.

- **Never name a film format** — "35mm", "IMAX", "film grain" draw a sprocket-hole border or change the medium **[CALIBRATED]**. Say "full-frame 16:9 image edge to edge" and put border, letterbox and vignette in the avoid list.
- **No age words near a person [CONSTRAINT].** One age phrasing failed eight generations running; a relational or role noun passed immediately with everything else held constant. Minors are refused outright.
- Dialogue as prose with the delivery **before** the words: `NAME says, weary: …`. Levers, most to least reliable: volume, emotional state, pace, register, physical condition, accent.
- The avoid list holds artefacts only. **Negations that name an action do not suppress it** — restate positively.
- ~1,100–1,400 characters **[CALIBRATED]**; longer prompts have returned 400.
- Nothing in frame carries text unless you have a glyph master. Whatever carries text invents text.

## Step 7 — trial one beat, then batch in pairs

Generate **one** clip, run the gates, fix the template, then continue **two beats per foreground call** **[CALIBRATED]**. A detached background run lost a clip mid-poll and a template bug repeats once per clip at full price. Never loop a retry into a refusal.

## Step 8 — gates

Nothing is accepted on impression. Run `clip_qa.py`; add the transcript check when speech matters.

```bash
python clip_qa.py <clips_dir>            # fluidity, lip sync, A/V drift, per clip
python clip_qa.py final.mp4              # the assembled cut
python clip_qa.py --selftest <clip.mp4>  # prove the detector before believing it
```

| Gate | Threshold | Tier |
|---|---|---|
| Stream lengths on the cut | video and audio within 0.1 s | CONSTRAINT (a mismatch *is* drift) |
| Speech onset after the cut | ≤ 1.6 s | CALIBRATED |
| Face-region motion floor | 10th percentile > 0.15 | CALIBRATED, 25 clips @ 24 fps 720p |
| Lip-sync lag | −0.045 s to +0.125 s, when correlation ≥ 0.3 | ITU-R BT.1359 detectability |
| Transcript word-hit | ≥ 70 % of scripted words | CALIBRATED |
| Mean volume | > −40 dB | CALIBRATED |
| Frames, by eye, 1 fps | identity, wardrobe, geometry, no text, no extra person, no border | judgment |

**The whole-frame motion median does not work for dialogue [CALIBRATED].** Calibrated on moving scenes it reads above 1.0, but a locked-off talking head sits at 0.3–0.9 while performing normally. Gating on it condemns good work.

**No metric can tell good motion from bad.** A hallucinated object is motion, so it *raises* every score; the highest-scoring take of five was the broken one. The eye stays in the loop.

Failure → delete the clip, change **one** thing, re-check. A second identical failure means the diagnosis is wrong: restage or delete the beat rather than rewrite the prompt again.

## Step 9 — assemble and hand over

Lane A joins with `scene create` and per-clip trims, free and server-side. Otherwise ffmpeg, and **join with the concat filter, not the demuxer** — mixed frame rates through the demuxer produced 164 s of video under 171 s of audio, lips running 4 % ahead **[CALIBRATED]**. Title cards need one `drawtext` per line; a newline inside one renders literally as "nn". Captions come from the **script**, not the transcript, so the reader sees the correct line even where the engine fluffed it.

Ship a review page beside the cut: the final video, and every clip with its prompt, lines, transcript, metrics and frames.

## Red flags — stop and re-plan

- Planning entities or plates before anyone looked at the final host URL.
- An age word anywhere near a person.
- A sign, poster, door or garment described as carrying words.
- A film format in the style block.
- Two `--reference-entity` flags, or `characters = [A, B]` on one manifest scene.
- `--duration` with no `--model`; `--ref` with `veo-quality`.
- A beat over 2.5 words per second, or a line shortened "to fit".
- Angles of one location generated as independent `t2i` calls.
- "Launch the rest in the background and check later."
- Accepting any clip without running a gate.

| Rationalisation | Reality |
|---|---|
| "Both faces must stay consistent, so both entities go in" | the second entity is the 400. One entity plus a role noun beats a refused generation. |
| "The room is described in every prompt, that is enough" | without an anchored plate chain, the first new angle invents a different room. |
| "Trim the speech so it fits 8 s" | split it across beats. The words are the deliverable. |
| "Reverse-angle drift is unavoidable" | it is avoidable: chain the angle off the anchor plate with `i2i`. |
| "Kick the batch off and review in the morning" | one template bug bills once per clip. Trial, gate, then pairs. |
| "It looks fine" | run the gates. Two clips that looked fine carried audible lag. |

## What this skill does not cover

Untested here, not discouraged. If you try one and it works, add a scored task and say so in `optimization_notes`.

**[UNEXPLORED]** seed locking across generations for consistency · a single-image storyboard sheet fed to a video model as a *generation* input rather than a review artefact · first-and-last-frame interpolation for transitions · custom voices and voice references · agent-mode brief cards · character archetype generation · manifest runs at large scale · reference-to-video at high reference counts · non-English delivery, which Google documents as unevaluated · and on the tooling side, a real face detector in place of `clip_qa.py`'s fixed crop, plus frame-rate normalisation in the motion metric.

## Keeping this skill honest

The repo ships a scored harness. Measure before and after any edit:

```bash
python scripts/dev/skillopt/harness.py --skill skills/video-production/SKILL.md \
                                       --tasks skills/video-production/tasks.json --dry-run
```

`tasks.json` beside this file holds the scored scenarios; every entry exists because an agent got it wrong in a rollout. When you find a new failure, add a task **first**, confirm it fails, then edit the skill until it passes. A rule added without a failing task is a guess.

## Reference

- **[`consistency.md`](consistency.md)** — character sheets, environment sets, prop sheets, film grammar
- **[`composition.md`](composition.md)** — reference budget and ordering, command chains per production shape
- **[`failure-modes.md`](failure-modes.md)** — symptom to cause to fix
- **[`clip_qa.py`](clip_qa.py)** — the gates
- **[`tasks.json`](tasks.json)** — the scored set
