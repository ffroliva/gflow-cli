# How references attach on the migrated host — the `@` picker

**Date:** 2026-09-05 · **Account:** a contributor profile Google has already moved to
`flow.google.com` · **Cost:** $0 — nothing was typed into a submitted prompt and nothing
was submitted. **Instrument:** `scripts/dev/capture_migrated_r2v_surface.py`.

## Why

`run_video` refuses every mode but T2V on the migrated host, and its reason names the gap
exactly: *"i2v/r2v attach media through labs-shaped slots that have not been recon'd on
this host."* `gflow video r2v` therefore exits 36 on a moved account. Porting it needs the
attach mechanism, and guessing a selector is not cheap — a wrong one fails *after* the run
has spent credits.

## What is already there

The settings pane's sub-mode row was captured by the wire-protocol spike
(`crop_free` Frames / `chrome_extension` Ingredients) and needs **no new machinery**: the
shipped `_select(page, pane, axis="submode", lig="chrome_extension")` selects Ingredients
on the live host, confirmed here.

## What is NOT there

Selecting Ingredients changes **nothing visible** on the page — measured, not assumed:

| after selecting Ingredients | |
|---|---|
| new ligatures | **none** |
| new buttons | **none** |
| `input[type=file]` | 0 → 0 |

So there is no labs-shaped reference slot to drive, and no drop target appears. A port
that went looking for one would have found nothing.

The `add` ligature button is **not** it either. It opens a `role=menu.add-menu` of four
`role=menuitem`s — `Upload`, `New collection`, `Create character`, `New scene` — which is
the **library**, not the composer. Useful later for `--ref <local file>`; irrelevant to
attaching something that already exists.

## What it actually is: an `@` mention picker in the composer

Typing `@` into the ProseMirror composer opens one overlay containing the whole reference
surface:

```
dashboard All | image Images | videocam Videos | voice_selection Voices |
accessibility_new Characters | face Avatars | drive_folder_upload Uploads
upload Upload media        search        Recent
  a man crying   Video
  a man running  Video
  Me             Avatar
                                                        [ Add to prompt ]
```

Items came back as `[role=menuitem], [role=option], li` entries carrying a **name plus a
type suffix** (`…Video`, `…Avatar`), and the overlay carries an explicit **"Add to prompt"**
confirm.

This is one mechanism for the entire matrix — media references, characters, avatars and
voices — where labs uses several distinct pickers. It is also the same `@Name` surface
gflow already exposes on labs, which means the CLI's existing mention vocabulary may map
onto it directly rather than needing a new one.

## The picker's real structure (phase 4)

Driving it blind failed twice, so the elements were dumped rather than guessed again:

| part | selector |
|---|---|
| category tabs | `mat-list-item[role=tab]` — All / Images / Videos / Voices / Characters / Avatars / Uploads |
| assets | `button[role=option].asset-item`, the focused one carrying `.asset-item-active` |
| confirm | `button.detail-add-to-prompt-btn` ("Add to prompt") |
| local file | `button` "Upload media" |

Two behaviours were measured that a port must not get wrong:

* **Clicking an asset dismisses the picker.** After `items.first.click()` the overlay is
  gone, `.asset-item-active` count is **0**, and the composer is back to its placeholder.
  Whatever selects an asset, it is not a plain click.
* **The first asset is already active** without any click (`.asset-item-active` = 1) and
  its detail pane — including the confirm — is already rendered. So the confirm is
  reachable directly; selecting a *specific* asset is the unsolved half.

## Still unattached, after four attempts

Clicking `button.detail-add-to-prompt-btn` directly, with the first asset active, left the
composer **empty** (`<p><span class="prosemirror-placeholder">What do you want to
create?</span>…</p>`) — no mention node, no text. Every run therefore reached submit with
an empty prompt, the submit was inert, and **zero `YhhmEf` requests were ever made** (7-8
`batchexecute` calls captured per run, all `DTaVef` / `UpteDb` / `as29s` / `jwpduf`).

That is why the wire question below is still open: no submit payload has been produced to
read.

**That lead was followed and came back negative — see Round 3 below.**

## Round 3 — the rpc lead, answered (negative), and the `insert_text` trap

`scripts/dev/capture_migrated_attach_rpcs.py` stamps every `batchexecute` POST with the
phase it fired in. No submit exists in that probe at all, so there is not even a
theoretical credit path.

| rpcid | fires | so it is |
|---|---|---|
| `DTaVef` | while **idle** | polling noise |
| `UpteDb` + `as29s` | on **`@`** | the picker loading its asset list |
| *(none)* | on **"Add to prompt"** | — |

**`confirm_only_rpcids: []`.** The confirm fires no network call whatsoever, so the lead is
dead: attachment is **not** a server-side rpc on confirm. Anything the port asserts has to
be observable in the composer or in the submit payload, not in an attach call.

### The `insert_text` trap — a real find

The composer looked empty after `@`, and that was not a measurement artifact: there is
exactly **one** `[contenteditable]`, it holds focus, and a plain prompt types into it
correctly (`<p>a man crying</p>`). The `@` genuinely was not landing.

Cause: `page.keyboard.insert_text("@")` dispatches input events **without real
keystrokes**. The ProseMirror mention plugin opens its picker on the character but has no
query to track, so every subsequent gesture is operating on a picker with no state behind
it. Switching to `page.keyboard.type("@", delay=120)` makes the character land —
`<p>@</p>` — and the picker opens with a live query.

This matters beyond the probe: production's `send_prompt` uses `insert_text` **on purpose**
(a newline in a prompt must not submit early), so a port that reuses it for mentions
inherits exactly this failure. Mentions need real key events; prompt text must not.

### Still blocked: selecting an asset

With a real `@` in the doc, every gesture tried still **dismisses** the picker and clears
the character — the composer reverts to its placeholder, `.asset-item-active` drops to 0
and the confirm disappears:

| gesture | result |
|---|---|
| `Enter` on the active asset | picker gone, `@` cleared |
| click `button.asset-item` | picker gone, `@` cleared, active 0 |
| click `button.detail-add-to-prompt-btn` | no-op, then gone |

The shape of that — a synthetic click reading as a click-away — suggests the overlay
dismisses on blur. **Untried and next in line:** `ArrowDown` then `Enter` (the canonical
mention-picker gesture, never attempted), hovering before clicking, and dispatching
`mousedown` rather than `click`.

## Round 4 — a mention chip, and what it carries

The account owner confirmed the human gesture: **ArrowDown + Enter**, and a mouse click
also works *for a human*. That last part is the tell — a synthetic Playwright click reads
as a click-away and dismisses the overlay, which is why every earlier click failed.

`ArrowDown` alone behaves correctly: the `@` survives, `.asset-item-active` stays 1, and
`UpteDb` fires (the highlighted asset's detail load). `Enter` after it still cleared the
composer, with or without waiting for that load — so the gesture was not the whole story.

What did produce an attachment was **typing a query** after the `@`:

```html
<span class="mention-chip"
      data-mention-id="c68767a5-afdd-46f1-a31d-fcc32daba716"
      data-reference-type="entity"
      data-entity-id="c68767a5-afdd-46f1-a31d-fcc32daba716"
      contenteditable="false">aloha</span>
```

Verified as genuinely created, not pre-existing: a clean editor load reads
`chips: []` with an empty placeholder composer, so the chip came from the typing.

**This settles the shape of the mechanism.** A reference is a **chip in the prompt
document** carrying an entity id — consistent with Round 3's finding that no rpc fires on
attach. Whatever the port does, it builds a prompt containing chips and can verify them by
reading `.mention-chip[data-entity-id]` out of the composer before submitting.

### Not reliable yet, and not to be built on as-is

The chip was reproduced **once**. Two later runs with the same query produced `chips: 0`.
Both failed the same way — the picker stayed open, and the *next* composer click died on
`<div class="cdk-overlay-backdrop …"> intercepts pointer events`. So the insert is
timing- or query-dependent in a way that is not yet characterised, and the gesture cannot
be called solved.

### A production-relevant side finding

`_close_pane` counts `.cdk-overlay-pane` only. A `.cdk-overlay-backdrop` can outlive it and
still intercept pointer events — measured here twice, blocking a composer click after the
pane was considered closed. That is the same class as the #669 overlay bug and the same
symptom (a click that cannot land). No shipped t2v path opens a picker, so it is not known
to be reachable in production, but the guard's scope is worth revisiting when the port
lands.

## Round 5 — SETTLED: the gesture, and the r2v submit payload

### The gesture, from a matrix rather than another guess

`scripts/dev/capture_migrated_mention_gestures.py` tries every candidate in one session,
clearing the composer between attempts:

| gesture | chip | note |
|---|---|---|
| `@` → **ArrowDown → Enter** | ✅ | `reference_type=entity`, real `entity_id` |
| `@` → query | ❌ | picker stays open — this is why earlier runs saw `chips: 0` |
| `@` → query → **Enter** | ✅ | an Avatar: `reference_type=likeness`, `entity_id` **null** |
| `@` → query → ArrowDown → **Enter** | ✅ | same |
| `@` → query typed slowly | ❌ | |

**Enter is what commits.** The one earlier "success" from a bare query was a fluke, and the
failures were not flakiness — they were a missing Enter. Note the two reference kinds:
a character carries an `entity_id`, an avatar is a `likeness` with a **null** id, so a port
cannot assume every chip has an entity id.

### Capturing the payload: three levers, one works

* `page.route` — **hangs**, >20 s, idle or busy.
* `context.route` — **hangs** the same way. Playwright interception is unusable here.
* `set_offline(True)` — suppresses the submit entirely: chip and prompt present, button
  enabled, and **no request at all**. The app evidently checks `navigator.onLine`.
* **Wrapping `fetch` and `XMLHttpRequest.send` inside the page** — works. The body is
  recorded and never passed through, so the request is never created at the network layer.
  Strictly safer than aborting one in flight.

### The r2v submit, captured at $0

```json
["MZZa6b", [[[[null,null,[[[null,[null,null,["c68767a5-…","aloha"]]],["  a man crying"]]]],
   null, "veo_3_1_r2v_lite_low_priority", 1, null,
   [null,null,null,null,"A2D760AB-…","394F970B-…"], null,null,null,
   [["c68767a5-afdd-46f1-a31d-fcc32daba716"]]]],
 [null,22,null,null,null,"66324c59-… (project id)", …]]
```

Four things this settles, none of which could be assumed:

1. **The r2v submit is rpcid `MZZa6b`, not `YhhmEf`.** `YhhmEf` is the *t2v* submit. A port
   that watched for `YhhmEf` would never see an ingredients run — and every earlier probe
   in this spike reported `submits: 0` for exactly that reason.
2. **The entity id rides the wire, twice** — inline in the prompt structure *and* in a
   trailing array of its own, which is the `referenceEntities` analogue. So the run IS
   assertable the way labs' `_assert_entities_attached` asserts it; a silent unreferenced
   generation can be caught.
3. **The prompt is segmented, not a string.** Mentions are `[entity_id, name]` nodes
   interleaved with text segments (`["  a man crying"]`). Building it means composing
   segments, not concatenating a prompt.
4. **The model key is mode-specific:** `veo_3_1_r2v_lite_low_priority` — observed in this
   capture. Mode and tier are encoded together, so the existing `VideoModel` → wire-key
   mapping does not carry over to r2v unchanged. The contrast with the
   `veo_3_1_lite_lower_priority` a t2v run sends is **inferred**, not measured: no capture
   in this repo has observed a migrated t2v body at that tier. It matters anyway, and in
   the safe direction — the r2v body assertion has to be able to *name* a mode-less key,
   because a key with no `_r2v_` infix is exactly what the body says when the picker
   inserted nothing.

Verified $0: the catalog's newest video predates the probe by ~2 h, so nothing generated.

## Round 6 — SETTLED: local files, and the three reference kinds

`scripts/dev/capture_migrated_upload_flow.py`. No submit path reaches the network (the
in-page fetch/XHR block again), so no generation; it does add assets to the library.

### The upload flow works

`Upload media` in the `@` picker opens a **native file chooser** — Playwright's
`expect_file_chooser` catches it and `set_files()` is accepted. No `input[type=file]`
exists in the DOM before or after; the chooser is the only route in.

The asset appears in the list immediately as `Uploading<name>` and is **not attachable
until that prefix clears**. Measured: appears ~1 s after `set_files`, settles ~10 s later
(1.1 MB png). Two separate waits are needed — polling "is it done?" once answers *yes*
before the entry has appeared, which silently attached nothing.

**Attach as a separate mention, not by continuing the picker that uploaded.** The native
chooser costs keyboard focus, so ArrowDown lands nowhere. Escape out, clear the composer,
then a fresh `@` + the filename prefix + Enter attaches it — which is also the shape a port
wants: upload, then reference by name.

### Three reference kinds, and they are NOT interchangeable on the wire

| kind | chip `reference_type` | chip `entity_id` | wire slot |
|---|---|---|---|
| character | `entity` | the id | trailing array, `[["<entity id>"]]` |
| avatar | `likeness` | **null** | not yet captured |
| uploaded image | `media` | **null** | **second element**, `[[null, "<media id>"]]` |

A character and an uploaded image ride in **different positions** of the `MZZa6b` payload:

```json
["MZZa6b", [[[[null,null,[[[null,[["ae3c560f-…","product1.png"]]],
   ["  a woman holding the product"]]]],
   [[null,"ae3c560f-dc0e-4399-815e-b7225456fa6b"]],        <- media slot
   "veo_3_1_r2v_lite_low_priority", 1, null, […]]],
 [null,22,null,null,null,"<project id>", …]]
```

versus the character capture in Round 5, where that second element was `null` and the id
appeared in a trailing `[["c68767a5-…"]]` instead. So a port cannot treat "a reference" as
one thing: the chip's `reference_type` decides which wire slot the id belongs in, and
`entity_id` being null does **not** mean nothing was attached.

In both cases the id also appears inline in the prompt segments alongside its display name.

### What the reported command needs

`gflow video r2v --ref me.jpg --ref …png` maps onto: upload each file, wait out its
`Uploading` state, then compose one prompt with a mention per file. Every mechanism that
requires is now captured — except attaching **two** references in one prompt, which has
never been exercised.

## Round 7 — SHIPPED, and live-verified end to end

Two references in one prompt work (the last untested step): both chips land, and the
`MZZa6b` payload carries both. Worth recording from that capture — filtering the picker on
`"me"` matched the **avatar named "Me"**, not the uploaded `me.jpg`. Name matching is
loose, so the port queries the **full filename**.

The port is implemented on that basis:

* `run_video` accepts `Mode.R2V`; `migrated_can_serve` routes it when references are
  present (character entities still stay on labs).
* Local `--ref` files upload through the picker's `Upload media` chooser, with the
  two-stage wait the timing measurements demanded.
* Each reference is attached as its own `@` mention, verified chip-by-chip.
* `SUBMIT_RPCS` covers `MZZa6b` alongside `YhhmEf`.
* A run whose references have not ALL attached is refused **before** submit — the whole
  point of the exercise, since the failure mode is a clip that generates and bills without
  them.
* `client.generate_video` creates a project over REST when a moved account passes none,
  since the migrated editor cannot create one. Labs is untouched.

**Live, the reported command exactly** — two local refs, `--model veo-lite-lp`, no
`--project`:

```
migrated.project_autocreated   e9a9b344-…
migrated.reference_uploaded    me.jpg                      settle 14.0s
migrated.reference_uploaded    ref_00ca69a3-….png          settle  8.0s
migrated.references_attached   count=2
migrated.references_ready      requested=2 attached=2 kinds=['media']
migrated.submit_observed       rpc=MZZa6b
migrated.result                done=true bytes=3599828
Saved: manual-test/79ad1fb7-….mp4            exit 0
```

`ffprobe`: 8.000000 s, 720x1280, h264 + aac, 3 599 828 bytes.

**Run twice, deliberately.** Almost every gesture in this spike worked once and then did
not, so a single green run proves little. The second run reproduced it end to end —
uploads settling in 16 s / 10 s (vs 14 s / 8 s), 2/2 chips, `MZZa6b`, downloaded — which
is what makes the upload waits and the chip-by-chip verification look like the right
shape rather than a lucky ordering.

## Semantic verification — both references bind

The account owner watched the output on 2026-09-06: the **presenter is the person from
`me.jpg`** and the **product is the one from the second reference**. Both references are
therefore genuinely *bound*, not merely accepted — the check no log can make, and the one
that on labs caught a silently dropped end frame (v0.64.0).

That closes the question this whole spike was built around. Ids on the wire and chips in
the DOM only ever proved Flow took the references; only the render proves it used them,
and it used both, in the order given.

## Round 8 — SETTLED: r2v exists only at the base duration

Reported from a real run on 2026-09-06: two references attached (chip count verified
twice), and the submit still went out on `MZZa6b` carrying
`veo_3_1_t2v_lite_4s_low_priority`. Settled at **$0** by
`scripts/dev/capture_migrated_r2v_production_submit.py`, which drives the *production*
path — `apply_video_settings` → `attach_references` → `send_prompt` — and blocks
`batchexecute` inside the page before the click. Three runs, same account, model,
reference files and gestures; **only the duration changed**:

| duration | model key on `MZZa6b` | prompt segments |
|---|---|---|
| 4s (inherited) | `veo_3_1_t2v_lite_4s_low_priority` | **flattened to plain text** |
| 6s | `veo_3_1_t2v_lite_6s_low_priority` | **flattened to plain text** |
| 8s | `veo_3_1_r2v_lite_low_priority` | mention nodes intact |

At 8s the prompt carries structured mentions — `[null, [["047e1f1d-…", "me.jpg"]]]`,
the Round 6 shape. At 4s and 6s the same two chips serialise as one flat string,
`"me.jpg  ref_….png  the presenter…"`: the app **degrades an ingredients run to
text-to-video and types the file names into the prompt.** It does not refuse, and it does
not warn.

Two details that matter for the port:

1. **The media slot is populated in all three.** `[[null,"<id>"],[null,"<id>"]]` carries
   both ids even in the degraded submits, so "are the ids in the body?" is **not** a
   sufficient assertion — it passes on a run that would bill a clip with none of the
   references on it. The model key is the load-bearing signal, which is why
   `_r2v_body_problem` checks it first. (This is the accepted-vs-bound distinction again,
   one layer lower than the render.)
2. **8s is the base tier: its key drops the duration segment entirely.** That explains why
   a cohort rendering no duration row at all — the maintainer's — has always submitted r2v
   correctly. It was never on a degrading tier because it cannot select one.

The editor **remembers** the last duration, so an r2v run passing no `--duration` inherits
whatever the previous run left. `apply_video_settings` therefore pins `R2V_DURATION_S`
best-effort when the pane offers durations, and refuses an explicit degrading duration
with exit 11 before any submit — the same reasoning as #125 one axis over.

**Not settled:** whether other models (`veo_3_1_lite`, `veo_3_1_fast`, `omni_flash`) share
the boundary, and whether `omni_flash`'s 10s tier does. Only
`veo_3_1_lite_lower_priority` was measured, on one cohort. The pin and the refusal are
written to be safe rather than precise: over-restricting costs a length nobody has shown
exists, under-restricting bills a clip with no references on it.

## What this does NOT settle

> Bullets that stood here — "Multiple references", "Local files", and the response shape —
> were **settled by Rounds 6-7 in this same document** and by the live run recorded in
> `tests/e2e/test_migrated_host_e2e.py`: two local `--ref` files were uploaded through the
> editor's own toolbar path and both bound. They are removed rather than left standing,
> because a superseded open question in a doc an agent reads as ground truth is how a
> settled surface gets re-mined. "More than two references, and the per-model caps" below
> is the part of them that genuinely remains open.

- **The avatar wire slot.** A `likeness` chip was produced but never submitted, so which
  slot carries it is unknown; only `media` has been exercised end to end.
- **Character entities on this host.** The attach works (a chip with a real `entity_id`),
  but `migrated_can_serve` still routes `--reference-entity` to labs, and no entity run
  has been submitted.
- **More than two references, and the per-model caps.** Two is the most ever attached, and
  two is what Round 7 and the live run both drove; three or more, and the caps themselves
  (omni 7, veo lite/fast/lite_lp 3), remain untested on this host.
- ~~**`i2v`.**~~ **Recon'd separately.** Frames are a different sub-mode with its own
  slots, captured in `2026-09-05-migrated-frames-attach.md` and shipped as slice 1.
- **Selecting a SPECIFIC asset.** Partly settled: Round 7 measured that matching is
  **loose** — querying `"me"` matched the avatar named *Me*, not the uploaded `me.jpg` —
  so the port queries the full filename and then verifies chip-by-chip rather than
  trusting the match. What the picker matches *on* (caption? filename? fuzzy?) is still
  untested, which is why exit 32 `ReferenceNotFoundError` is the guard rather than a
  cleverer query.
- ~~**The response shape.**~~ **Settled in Round 7.** Written while every submit was still
  route-aborted, so no `MZZa6b` reply had been seen. The live runs have since parsed real
  ones — `migrated.submit_observed rpc=MZZa6b` through to `migrated.result` — so the
  backstop is written against a captured response, as
  [[credit-free-route-abort-verification]] demands, not inferred from the request.
- **Search vs Recent.** Only a Recent list was observed. Whether the search box is required
  for an asset outside it, and how it matches (caption? filename? — labs indexes a short
  auto-caption, not the prompt, per exit 32 `ReferenceNotFoundError`), is untested.

- **Ordering and caps.** `MAX_REFERENCE_IMAGES`, and whether the picker enforces the same
  per-model reference caps as labs (omni 7, veo lite/fast/lite_lp 3, quality none), is
  unknown.
- **Whether Ingredients is even required** when attaching by `@`. The sub-mode was selected
  before probing, so the picker has not been observed in Frames mode; it may be
  sub-mode-independent, in which case the port needs no sub-mode step at all.

## Credit-safety note for the next probe

Playwright request **interception is not usable on this page**: `page.route()` never
returned within 20 s, whether installed on an idle editor or a busy one (four runs died
there). The working substitute is `BrowserContext.set_offline(True)` flipped immediately
before the submit click, with a passive `page.on("request")` listener recording the
payload — the request is attempted, recorded, and fails locally without reaching Google.
`scripts/dev/capture_migrated_r2v_submit_payload.py` does it that way, and confirmed $0
across every run (`submits: 0`).

## Consequence for the port

The attach mechanism is a **prompt-level mention**, not a slot. That is a different shape
from the labs driver's `_attach_r2v_references` and should not be ported by analogy — the
r2v path here is closer to "compose the prompt with mentions, then submit" than to "fill
slots, then submit". Establishing the confirm semantics above is the next $0 step, and it
gates everything else.
