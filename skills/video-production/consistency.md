# Consistency — cast, location, props, and the grammar that holds them together

Companion to [`SKILL.md`](SKILL.md). Claims are tagged **[CONSTRAINT]** (the engine refuses), **[CALIBRATED]** (measured, conditions stated), **[CONVENTION]** (one workable shape), **[UNEXPLORED]** (untested here).

## The one fact everything else follows from

**Flow has exactly one reusable entity type: CHARACTER [CONSTRAINT].** There is no environment entity, no object entity, no prop entity and no style entity.

Two consequences, and they shape the whole method:

1. **Only the cast is name-addressable.** A character is created once, lives in the project, and is referenced by id or by name from then on.
2. **Locations and props can never be remembered.** They are re-attached, as images, on every single shot that needs them. There is no shortcut and no persistence.

## Identity versus image

The two reference mechanisms are not interchangeable. This is the distinction people get wrong.

| | `--ref` | `--reference-entity` / `@Name` |
|---|---|---|
| Wire field | `referenceImages` | `referenceEntities` |
| Means | "look like **this picture**" | "this is **this person**" |
| Use for | a location plate, a prop sheet, a one-off look | a saved character |
| Persists in the project | no | yes, project-scoped |
| Carries | one angle | a multi-angle turnaround, plus voice and personality |
| Available on | `image i2i`, `video r2v` | `image t2i`/`i2i`, `video t2v`/`r2v` — **not `i2v`** |

`@Name` and `--reference-entity` are the same mechanism; one resolves by display name, the other takes the id. They dedupe against each other.

**They are designed to combine.** An entity plus a location plate reads as *this person, in this place*. That composition is the normal case, not an advanced one, and `--ref` is **repeatable up to the model's cap** — a plate and a prop sheet in the same call is ordinary usage. To pin a person, a place and an object into one approved still before animating it, see the composited-approved-frame pattern in [`composition.md`](composition.md).

**Entities are project- and account-scoped [CONSTRAINT].** An entity minted on one account will not resolve when generating on another. Moving a production between accounts means rebuilding the cast under identical names, or accepting portrait images instead — a decision to make deliberately, not to discover mid-run.

**`i2v` has no identity channel at all [CONSTRAINT].** Its request rejects reference entities by design. Whoever is in the initial frame is who you get. Identity reaches an `i2v` clip only by being baked into that frame.

## Character sheet

### Building one

```bash
gflow character create --project <id> --name <Name> \
  --face-prompt "<unchangeable features, plain background>" \
  --body-prompt "<wardrobe, plain, no print>" \
  --voice <preset>            # gflow character voices — lists presets with samples
```

The face prompt generates the first reference image. The body prompt is wrapped into a front/side/back triptych seeded by that face, so one generation yields all three angles on-model.

### The rules that make it hold

- **Face prompt carries unchangeable features only** — build, hair, facial structure, eye colour, defining marks — on a plain or segmented background, which is also Flow's own documented guidance for references. No hats, glasses or props unless permanent.
- **An entity locks the face, not the clothing [CALIBRATED].** On one production 9 of 11 frames had the right person and only 4 of 11 the right garment. Wardrobe continuity comes from repeating **one wardrobe token verbatim** in every prompt — "the grey hoodie", "the navy cardigan" — never from the entity.
- **Pin garments as plain, no print [CALIBRATED].** "A band t-shirt" rendered a misspelled logo on every clip of a six-clip scene. Whatever carries text invents text.
- **No age words [CONSTRAINT].** One age phrasing failed eight generations running; a relational noun passed immediately, same references, same scene. Minors are refused outright — cast young roles as adults.
- **One face-bearing reference per generation [CONSTRAINT].** Two entities, or an entity plus a portrait image, returns HTTP 400 surfaced as a wire-format error whose remediation text ("simplify the prompt") is misleading. This matches the documented single-subject reference design.
- **Voice rides on the entity**, not on prose. Voice consistency across clips comes from the character, and presets are listed by `gflow character voices`.

### Two people in one scene

Give the entity to whoever carries the beat — the speaker of the long line, the face in the close-up. The other rides on the **verbatim canon plus wardrobe token**, staged in profile or turned away in that shot. Alternate across beats so each actor is entity-locked in their own close-ups, which is where an audience reads identity. Keep the non-entity description byte-identical between beats.

### Without entities

Where the lane or the account rules entities out, copy the **entire, unchanged character description into every prompt**, altering only action and setting. This is Google's own documented advice for character consistency, and it works — the whole cast canon plus a fixed geometry paragraph carried a three-scene piece with no entities at all **[CALIBRATED]**.

## Environment sets

### The failure this exists to prevent

**A multi-angle set is N independent generations that merely share a paragraph [CALIBRATED].** Nothing binds angle two to angle one. "The same room from four angles" is a fiction the description implies and the engine never enforces. A real production shipped with a window that changed between beats, and a scored rollout accepted reverse-angle drift as *unavoidable* rather than fixable.

It is fixable.

### The anchor chain

```bash
# 1. the anchor angle — geometry only, no people, from text
gflow image t2i "<room, furniture left to right, light source, materials>" \
  --aspect 16:9 --project <id> -o plate_wide.png            # free

# 2. every other angle, chained off the anchor — never a second t2i
gflow image i2i "<same room, reverse angle from the door>" \
  --ref <ANCHOR_UUID> --aspect 16:9 --project <id> -o plate_reverse.png
gflow image i2i "<same room, close angle on the bench>" \
  --ref <ANCHOR_UUID> --aspect 16:9 --project <id> -o plate_bench.png
```

Serial, and free — image generation draws on a daily cap, not credits. The only cost is that the set must be built in order.

**Attaching by media UUID buys asset identity, never scene coherence.** Same bytes, no duplicate upload, clean provenance — and nothing whatsoever about whether two plates depict the same place. Asset management is not continuity. Believing otherwise is the design gap this section exists to close.

### Plate rules

- **Geometry only, no people.** Light and grade belong in the shot prose, not baked into the plate.
- **Minimum useful set: a wide plus its reverse.** The reverse is what sits behind each actor in a two-shot, and it is the highest-drift angle.
- **One plate per shot** — the angle whose geometry matches. Not all plates on every shot.
- **A plate for every region that will be on screen.** Reference-to-video invents whatever the references do not cover, and re-invents it as the camera moves: a shot looking into a display case needs a case-interior plate, not just a room plate **[CALIBRATED]**.
- **Scope filenames to the production** — `<piece>_env_window.png`, never `window.png`. The picker deduplicates by exact filename inside a project, so a generic name silently selects the wrong asset.
- **Retire a bad plate; do not fight it in prose [CALIBRATED].** One mis-generated plate relocated every one of the four scenes it was attached to.
- A different lighting state is a different plate, chained off the same anchor.

### The text half

Plates do not carry geometry reliably on their own. Pair every plate with a **fixed geometry paragraph, repeated verbatim in every prompt**: furniture, doors and windows named left to right, the light source, and who stands screen-left. Adding named camera setups plus that paragraph stopped room drift across ten consecutive shots where the first two had already diverged **[CALIBRATED]**.

## Prop and object sheets

- **One sheet per prop** that carries text, is counted, or is ever the focal object.
- **The prop fills most of the frame.** Upload re-encoding destroys small detail and lettering.
- **Every sheet needs a scale anchor** — a hand, a bench edge. Unanchored objects come back giant **[CALIBRATED]**.
- **State-keyed sheets**: a prop that changes state is two sheets, not one.
- **One assembly plate** with the props in place pins their relative scale and spatial relationships.
- **Text-bearing props need a straight-on, glyph-exact master** — and prefer to avoid them. A prop that changes **kind** communicates a mechanism with no words at all; a prop that changes **wording** invites invented text.
- **Never stage a prop touching a surface it must leave [CALIBRATED].** A card shown flat against a pane animated as attached to it, and the engine grew a duplicate in the actor's hand across three separate attempts. Restaging it an arm's length away fixed what three prompt rewrites could not.
- **End a beat by hiding a prop, not by making it vanish.** "Into the drawer, and the drawer closes" is safe; "disappears" is an instruction satisfied by duplication.

## Film grammar as data, not vibes

Carry these as fields in the beat sheet, so they can be checked rather than remembered.

- **Named camera setups [CONVENTION].** `A_WIDE`, `B_MED_<name>`, `C_CU_<name>`, `D_TWO` — each naming the camera position and what sits behind each actor. Every beat references one. Recurrence is what makes cuts read as edits of a single scene instead of unrelated shots.
- **A declared 180° axis.** Who owns frame-left; the camera never crosses. One overhead blocking sketch settles axis, eyeline and reverse geography for the whole piece more cheaply than any amount of prose.
- **A closed shot vocabulary** — wide, medium, close-up, extreme close-up, over-the-shoulder — with a deliberate arc across the piece rather than a random walk.
- **A declared role for every reference injected**: geometry, dressing, or grade. A plate attached without a role gets treated as all three.

## Three artefacts people call "storyboard"

Keep them apart; they do different jobs.

| Artefact | What it is | When | Status |
|---|---|---|---|
| **Beat sheet** | the machine-checkable table: id, setup, action, lines, duration, model, references | before any generation | required |
| **Visual storyboard** | a single image containing a grid of panels for the whole piece, generated before final stills | between assets and production | advisory |
| **Frame board** | the real generated frames laid out per beat, with prompts and metrics | after each batch | the review artefact |

The visual storyboard earns its place as a **director's gate**: it catches shot-size monotony, axis breaks and geography errors before any per-shot budget is spent, because all panels are drawn in one generation and therefore share a look for free. It is **not** a stills replacement — panel resolution cannot hold fine detail, and it bypasses the per-shot reference machinery entirely.

**[UNEXPLORED]** Feeding that sheet to a video model as a *generation* input, rather than using it for review, is a technique other people report. It has not been tested here.

## Composition order

When a shot carries several references, order and budget matter.

1. **Characters first**, one face-bearing reference maximum.
2. **Environment next, and environment is never trimmed.** A naive tail-trim once dropped the location plate from exactly the multi-character shots, producing set drift nobody could trace back **[CALIBRATED]**. If the budget would trim a location, fail loudly and split the shot instead.
3. **Props last**, as budget allows; demote the rest to prose.
4. **Preserve the order you wrote.** Do not sort the reference list.

Caps are per model, and the entity counts against the same pool as the images: omni-flash 7, veo-lite / veo-fast / veo-lite-lp 3, **veo-quality 0 — it accepts no references at all [CONSTRAINT]**. On the image side nano2 and nano-pro take 10, imagen4 3.

So a referenced shot cannot be `veo-quality`, whatever the quality target: take **omni-flash** for a single generation needing more than 3 references or the best quality with any reference at all, and a veo-lite variant when 3 is enough. **`video chain` refuses omni-flash [CONSTRAINT]** — its i2v is verified for single generations only — so chained links take a Veo 3.1 model and its cap of 3.

**Select the model before attaching references [CONSTRAINT].** Switching model afterwards invalidates what was attached.

**Use `nano2` for bulk image work.** `nano-pro` is daily-capped and is the wrong default for building a set of plates.
