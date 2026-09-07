---
name: video-production
version: "2.0"
skillopt_epoch: 1
description: >
  Use when the user wants a finished video out of gflow rather than a single clip — a scripted scene, a talking-head or dialogue piece, an explainer, a product montage, a story sequence, an audition or rehearsal reference, a short film — or asks for consistent actors, a consistent location, a specific prop that must not change, several camera angles, captions or subtitles, or joining clips into one file. Also use when clips came back wrong: a film-strip border, a room that changes between shots, a prop that morphs, rushed or cut-off speech, audio out of sync, a person-policy refusal, or exit 36 / RecaptchaError on a generation.
optimization_notes: |
  Known weak spots, each observed in a scored rollout. Targets for epoch 2+ (#685 —
  the four below are MEASURED, not guessed: the tail 5 tasks scored 1/5 avg 0.280 on
  gemini-3.5-flash-lite, against a control of 4/5 avg 0.800 on the same model, so the
  gap is the skill and not the model):
  - A deliberately-staged object animated with t2v instead of i2v from an approved still
  - Re-run scope for an edited multi-scene manifest
  - An overnight batch refused correctly, but the foreground alternative never named
  - A lip-sync detector trusted without first proving it against a known delay
  - `--duration` passed without `--model`: binds veo-lite, which renders no duration control, exit 2
  - `--ref` passed with `--model veo-quality`: that model's reference cap is 0, not 3
    ADDRESSED in epoch 1 — the doc banned it in two places and named the substitute
    in none, so a rollout recited "reference cap is 0" and still picked that model.
    Step 4 now routes to omni-flash. Controlled A/B, one model: 0.00 -> 0.90.
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
| `flow.google.com/project/…` | **B, partial** | `video t2v --project`, `video i2v --initial-frame <local file> --project`, and **`video r2v --ref <local file> --project`** (ported in v0.70.0, #683). Everything else — `image`, `character create`, `extend`, `scene create`, `movie run`, r2v by `@Name` or `--reference-entity`, and i2v by media UUID / `@Name` or with `--end-frame` — exits 36 **[CONSTRAINT]** |

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

### 4a. The identity resolution ladder — deterministic, and you record the rung

A person in a shot is anchored by exactly one of these. **Take the highest rung the host
and path allow, and write down which rung you used and what blocked the one above.** This
is not a preference order, it is the production record: a film whose log does not say how
each shot was anchored cannot be debugged when a face drifts.

| # | Anchor | What it carries | Use when |
|---|---|---|---|
| **1** | **Character entity** — `@Name` or `--reference-entity <id>` | face **and** wardrobe **and** voice, server-side, durable | always, unless a rung-1 blocker is recorded |
| **2** | **That entity's own generated plate** — `--ref <its portrait or body crop>` | face and wardrobe, as a flat image | rung 1 refused by the host/path |
| **3** | **A plate cut from an approved take** — `--ref <frame>` | face, plus that take's grade, light and artefacts | no entity exists |
| **4** | **Prose canon only** | a *type*, never an identity | nothing else is available |

**Rung 4 does not hold a person.** Each generation invents someone new who merely matches
the description. Two shots on rung 4 are two different actors. Never plan a multi-shot
piece on it and never let it be the silent default.

**Rung 3 carries contamination.** v1's opening take came back with 72 px letterbox bars and
its plate carried those bars into every shot that referenced it. A rung-2 plate is generated
clean by the character editor; a rung-3 plate is only as clean as the take it was cut from.

**Descending a rung is a decision that gets written down**, in `production.json` next to the
shot, naming the blocker. "I used an image" is not a record; "rung 2, because
`--reference-entity` exits 36 on this host (#639)" is.

> **Written from a run that skipped its own ladder.** On 2026-09-07 a five-shot film created
> two real character entities and then anchored every shot on rung 2, because gflow refuses
> entity references on the migrated host. The refusal was never questioned — and a $0 probe
> the same day showed the host takes an entity mention perfectly well
> (`data-reference-type="entity"` with the real `entity_id`); it is gflow that has not ported
> the gesture. The run went a rung lower than it had to and recorded it only in passing.

### 4b. Entity beats media when a name matches both [CONSTRAINT]

Flow's `@` picker offers **character entities and media assets in one list**, and a query
matching both can resolve to either — measured 2026-09-07: the same `@Kael` returned
`reference_type="entity"` on one gesture and `reference_type="media"` (a JPEG that happened
to be named after him) on another. **Flow does not rank them.**

So you rank them. When a name matches a character and a file, the **character wins**, and if
you meant the file, reference it by path (`--ref <path>`) rather than by name. A shot that
silently binds a still image where you asked for a character produces footage that looks
right and drifts on the next cut.

### 4b-bis. A cast with VOICES — the whole recipe, end to end

A character entity is the only thing that carries a **voice**. A plate does not: it carries
face and wardrobe as pixels, and the engine invents a new voice for every clip. Measured
2026-09-07 on one character across three plate-bound takes: **88 Hz, 103 Hz, 118 Hz** — three
different actors — against an engine noise floor of **4.3 Hz** on an identical prompt
repeated three times. No re-shoot fixes that, because nothing in a plate was ever carrying
the voice.

So if anyone speaks more than once, you need rung 1. Here is the whole path.

**1 — pick the voice before you create anyone.** `gflow character voices` lists 29 presets.
Each has a public sample you can actually listen to, so audition rather than trust the
descriptor — five of the 29 descriptors disagree with the measured pitch of their own sample:

```bash
gflow character voices --json
# every voice has: https://gstatic.com/aitestkitchen/voices/samples/<Name>.wav
```

**2 — create the character with the voice bound.** Face, body and voice in one call:

```bash
gflow character create --project "$PROJECT" \
  --name    "<UniqueName>" \
  --face-prompt "a man, <face description with a GENDER WORD>" \
  --body-prompt "<outfit; no print, no logo>" \
  --voice   "<VoiceName>" \
  --personality "<how they behave>" \
  --json > cast_<name>.json
```

Three traps, each measured:

- **The name must collide with nothing in the media library.** Flow's `@` picker searches
  characters and media together and does not rank them (4b), so an uploaded `kael_ref.jpg`
  wins the query `@Kael` and your character becomes unreachable by name. **Never name a
  plate after a character**; `plate_a.png`, not `<name>_ref.jpg`.
- **A face prompt with no gender word gets a gender chosen for it.** This fires on the
  prose canon too, not just on `character create` — a two-hander whose unbound actor is
  described without one renders the wrong person.
- **Keep the `entity_id` yourself.** Read it from `--json` at creation. Do not plan on
  recovering it from the catalog afterwards.

**3 — attach the character to every shot they appear in.**

```bash
gflow video t2v "<prompt>" --project "$PROJECT" \
  --reference-entity      "<entity_id>" \
  --reference-entity-name "<UniqueName>" \
  --aspect 16:9 --duration 8
```

One face-bearing reference per generation **[CONSTRAINT]** — so in a two-hander, bind the
person who **speaks** and carry the other in prose. Getting that backwards is what produced
the 88 Hz stranger above: the beat bound the silent actor, so the speaker fell to rung 4.

**4 — expect the submit to outlive your patience, and do not read a timeout as a failure.**
Flow allows **five concurrent generations** and throttles per-minute throughput after heavy
daily use, so a queued job routinely outlives gflow's submit-reply budget. An
entity-bound run can exit **9 `TransportTimeoutError`** while the video is rendering
normally — the job is in Flow's queue and will finish. Check the project before you
re-submit, or you will double-spend on a generation you already have. (gflow-cli #723,
#741.)

**5 — verify the voice actually landed, relatively.** Never assert an absolute band: a
character legitimately speaks differently in an action beat than in a quiet one, and the
pitch follows the performance. The two sound comparisons are:

- **the same character across comparably-staged shots** — the medians should sit close
- **a bound take against that voice's own public sample** — the same neighbourhood

And stage every dialogue beat in **still air**. An energy gate is not a voicing gate: on
this production's own wordless clips the detector reported a confident 145 Hz and 280 Hz
with no speech present at all, and periodicity did not separate them either. A beat shot in
wind cannot be checked.

### 4c. A costume is part of the identity, so a costume change is a NEW entity [CONSTRAINT]

A Flow character entity bundles face **and** wardrobe — the body reference fixes the outfit.
There is no wardrobe axis inside one entity.

So a character who changes clothes is **two entities sharing a face prompt**, with different
body prompts, named for the costume state:

```
Kael_ridge   face_prompt=<the canonical face>  body_prompt=<dust-brown canvas jacket, sand scarf>
Kael_coat    face_prompt=<the same canonical face verbatim>  body_prompt=<heavy oiled coat, hood down>
```

The face prompt must be **byte-identical** across costume states; only the body prompt moves.
Then every scene names the costume-state entity, not the character, and continuity becomes a
lookup instead of a hope.

**This is not what `movie.toml` does today [CONSTRAINT].** `Character.variants` is a
`Mapping[str, str]` and `resolve_variant()` appends a text delta to the prose appearance
(`composition.py:67-80`), while the runner creates exactly **one entity per character name**
(`cli_movie.py:589-597`). On an `identity = "entity"` character a variant therefore changes
the *words* while the entity's body plate keeps the original outfit, and the two argue inside
one generation. Until that is fixed, express costume states as **separate entries in the
manifest's characters array**, each with `identity = "entity"` and a shared face prompt —
not as `variants`. See [MOVIE.md](../../docs/MOVIE.md) for the TOML.

Full method in **[`consistency.md`](consistency.md)**. The rest of the short form:

- **People** are Flow CHARACTER entities, attached with `--reference-entity` or `@Name`. Identity.
- **Locations and props** have no entity type **[CONSTRAINT]** — they are images attached per shot with `--ref`. Look.
- **One face-bearing reference per generation [CONSTRAINT].** A second entity, or an entity plus a portrait, returns HTTP 400 reported as a wire-format error. Carry other people as role nouns in prose.
- **Multi-angle locations must be chained, not generated in parallel [CALIBRATED]** — anchor angle by `t2i`, every other angle by `i2i --ref <anchor>`. Independent calls from the same paragraph produce different rooms.
- **The reference count picks the model, before quality does [CONSTRAINT].** Caps are per model and the entity counts against the same pool: `omni-flash` 7, `veo-lite` / `veo-fast` / `veo-lite-lp` 3, **`veo-quality` 0 — it accepts no references at all**. So a shot carrying any `--ref` or entity cannot use `veo-quality` however much you want its quality. For a **single** generation needing references and quality together, that makes `omni-flash` the pick; a `veo-lite` variant when 3 refs is enough. **`video chain` is the exception [CONSTRAINT]** — it refuses `omni-flash` outright (its i2v is wire-verified for single generations only) and exits with a model/mode incompatibility, so chained links take a Veo 3.1 model and its cap of 3. Full table, image models included: [`consistency.md`](consistency.md).
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
- **That avoid-list entry is not sufficient on its own [CALIBRATED, 4 clips, veo-quality].** A shot opening "Cinematic close portrait…" returned 72 px letterbox bars top and bottom *while* carrying "full-frame 16:9 image edge to edge" and "letterbox bars" first in its avoid list. The word **cinematic** appears to be enough on its own; no format was named. Restating it positively and explicitly — **"full-frame 16:9 image filling the entire frame edge to edge, no bars, no border"** — returned three consecutive bar-free shots with everything else held constant. Measure it, do not eyeball it: `cropdetect` reported nothing on the bar-carrying clip, so check the frame's own dark-row extents instead. A plate cut from a barred clip carries the bars into every shot that references it.
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

Directory mode matches **exactly** `[a-z]{2}\d{2}.mp4` — two letters then two digits, e.g.
`ka01.mp4`. Descriptive names and `0100.mp4` both silently match nothing and report
"no clips matched", which reads like a path error.

**On a clip with no speech, run `--selftest` before acting on a sync verdict [CALIBRATED].**
A wordless close-up flagged `DRIFT sync=+0.500s r=0.7` — correlation above the 0.3 threshold,
so the gate applied. `--selftest` on that same clip then failed to recover a **known injected
0.2 s** shift (`saw -0.500s`), which disqualifies the reading rather than confirming it. The
detector correlates face-region motion against audio; with only wind on the track there is
nothing for it to lock onto, and it locks onto noise. This is the skill's own listed weak spot
("a lip-sync detector trusted without first proving it against a known delay") reproduced.

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
- `--duration` with no `--model`; `--ref` with `veo-quality` (it takes 0 — reach for `omni-flash`).
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
