# Failure modes — symptom, cause, fix

Companion to [`SKILL.md`](SKILL.md). Each entry was paid for. Tags: **[CONSTRAINT]** engine refuses · **[CALIBRATED]** measured here.

Find your symptom in the left column first. Several of these present as something other than their cause, which is why the table is ordered by what you actually see.

## The command fails or is refused

| Symptom | Real cause | Fix |
|---|---|---|
| Exit 2 naming the model | `--duration` passed with no `--model`. An omitted model is not "no model": it binds `veo-lite`, which renders no duration control **[CONSTRAINT]** | name the model explicitly whenever length matters; `omni-flash` for 10 s |
| A reference is rejected or ignored | `--model veo-quality` accepts **zero** references **[CONSTRAINT]** | use omni-flash (7) or a veo-lite/fast variant (3) |
| A reference silently stops applying | model selected *after* attaching **[CONSTRAINT]** | choose model, then attach — never the reverse |
| HTTP 400 as `WireFormatError`, advice says "simplify the prompt" | two face-bearing references in one generation, **or** an age word in the prose **[CONSTRAINT]** | keep one face reference and carry others as role nouns; strip every age phrase |
| The same refusal repeats no matter how the prose is rewritten | the diagnosis is wrong, not the wording **[CALIBRATED]** | after two identical failures stop rewriting and look one step upstream — the references, the casting, the beat itself |
| `RecaptchaError` on a free image command | on gflow ≤ 0.68.0, the migrated host — the guard ran *after* the reCAPTCHA mint on the image path (#673, fixed: it is exit 36 now). On any build, right after `auth login`, the cookie harvest keyed on the old host (#644) **[CALIBRATED]** | check the final host URL before anything else; if it followed a fresh login, see #644 |
| Exit 36 | migrated host, command not ported | that lane runs `t2v` and `i2v --initial-frame <local file>`; everything else is unported. Do not retry, it is not transient |
| A run 400s only after a plate was attached | the plate pushed a multi-person prompt over the person policy | lean the prose to role nouns before attaching |
| Exit 11 or a parse error on a manifest | a model and duration pair that could never render | `--dry-run` catches it before spending |

## The clip generates but is wrong

| Symptom | Real cause | Fix |
|---|---|---|
| A dark strip with sprocket holes down one edge | a **film format** named in the style block — "35mm", "IMAX", "film grain" **[CALIBRATED]** | describe the optical effect, not the stock: "full-frame 16:9 image edge to edge, shallow depth of field", with border and letterbox in the avoid list |
| The room changes between shots | angles generated as independent `t2i` calls from one paragraph **[CALIBRATED]** | anchor plate by `t2i`, every other angle by `i2i --ref <anchor>`; add a verbatim geometry paragraph and named setups |
| Every shot handed one plate lands in the wrong place | the plate itself contradicts the brief **[CALIBRATED]** | retire the plate and regenerate it; do not fight it in prose |
| A defect appears in some shots and not others | a shared reference, not the prompts **[CALIBRATED]** | diff the reference lists of failing against passing shots before touching any wording |
| The right face, the wrong clothes | an entity locks the face, never the clothing — measured 9 of 11 faces right, 4 of 11 garments **[CALIBRATED]** | repeat one wardrobe token verbatim in every prompt |
| Invented lettering on a sign, poster or garment | anything carrying text invents text **[CALIBRATED]** | pin props as blank; prefer a prop that changes *kind* over one that changes *wording* |
| A region of the frame keeps re-inventing itself as the camera moves | reference-to-video authors whatever the references do not cover **[CALIBRATED]** | supply a plate for every region that will be on screen, not just the room |
| The staged hero object is missing from the clip | `r2v` composes its own frame and cannot guarantee a staged element **[CALIBRATED]** | use `i2v --initial-frame` when the staged object is the mechanism |
| A prop stays stuck to a surface, or duplicates | it was touching that surface in the initial frame **[CALIBRATED]** | never stage a prop against a surface it must leave; three prompt rewrites failed where restaging an arm's length away worked immediately |
| A prop vanishes instead of being put away | "disappears" is an instruction satisfied by duplication | end the beat by hiding it — into a drawer, and the drawer closes |
| An object comes back the wrong size | the prop sheet had no scale anchor **[CALIBRATED]** | put a hand or a bench edge in every sheet |
| The clip invents action in the back half | roughly a second of written action stretched across eight **[CALIBRATED]** | give the beat enough real motion, shorten the duration, or delete the beat |
| A negation is ignored, or the named thing happens | negations name the unwanted action and do not suppress it **[CALIBRATED]** | restate positively: describe what *does* happen |
| The wrong asset is attached from the picker | the picker deduplicates by exact filename **[CALIBRATED]** | scope every filename to the production |

## The audio is wrong

| Symptom | Real cause | Fix |
|---|---|---|
| Delivery rushed, last sentence cut | more than about 2.5 spoken words per second **[CALIBRATED, 25 clips]** | split across beats; never shorten the script to fit |
| Room tone under a montage that should be silent | Veo always generates audio; there is no silent mode **[CONSTRAINT]** | strip it at assembly with `-an` |
| Lips ahead of the voice across a whole cut | segments joined at mixed frame rates through the concat demuxer — 164 s of video under 171 s of audio **[CALIBRATED]** | join with the concat filter at one frame rate; gate on the stream lengths agreeing within 0.1 s |
| One clip audibly out of sync while others are fine | genuine per-clip lag; two clips in an 18-clip set measured 0.167 s and 0.333 s after passing every other check **[CALIBRATED]** | measure it, do not listen for it — cross-correlate face motion against the audio envelope |
| Speech starts late after the cut | the beat opens on a held face | open on an action, not a still pose |

## The measurement is wrong

| Symptom | Real cause | Fix |
|---|---|---|
| A locked-off dialogue clip scores "frozen" | the whole-frame motion median is calibrated on moving scenes; a talking head sits at 0.3–0.9 while performing **[CALIBRATED]** | gate on speech onset and face-region motion instead |
| The best-scoring take is the broken one | a hallucinated object is motion, so it **raises** every score — the highest of five takes had invented an object that then vanished **[CALIBRATED]** | no metric can tell good motion from bad; the eye stays in the loop |
| A confident sync lag that is not real | a flat correlation surface still has a maximum | require the peak to exceed zero lag by a margin, reject peaks pinned to the search boundary, and treat correlation below 0.3 as *unmeasurable*, never as *in sync* |
| Every clip reports perfect sync | the detector is broken and silently returning nothing | prove it first: re-encode a known clip with a 200 ms audio delay and confirm it reports 200 ms |
| A clip's numbers look like the previous clip's | a tool read a stale metadata file after a failed subprocess | check exit codes and delete the file before each write |

## The run wastes money

| Symptom | Real cause | Fix |
|---|---|---|
| One template bug billed once per clip | the batch was launched before a trial clip was gated **[CALIBRATED]** | trial one, gate it, fix the template, then two beats per foreground call |
| A clip billed but never downloaded | a detached background run died mid-poll **[CALIBRATED]** | run paid calls in the foreground |
| A whole manifest regenerated after a one-word change | editing the shared style block re-hashes every scene **[CONSTRAINT]** | keep style edits off script-only runs |
| Everything after an inserted row re-billed | scene ids shifted, so the content hashes moved | keep scene ids stable and slot-shaped |
| Two runs collided | concurrent generations on one profile take a lease | one profile, one run |
| A placeholder generation burned to obtain a project id | `gflow project create --name <x> --json` exists **[CONSTRAINT]** | redirect its output to a file; piping through `head` truncates the process before the JSON prints |
| An image daily cap hit early | `nano-pro` used for bulk plate work | `nano2` for volume |

## Two habits that prevent most of the above

**Diff before you rewrite.** When a defect clusters, compare what the failing shots were *given* against the passing ones. Most repeat failures are one bad shared asset, not a prompt problem, and rewriting prose cannot fix an input.

**Change one thing per re-roll, and cap it at two.** A re-roll that fixes one axis frequently breaks another; re-gate against the originals rather than assuming forward progress. If a beat fails twice for the same reason, the beat is the problem — restage it or cut it.
