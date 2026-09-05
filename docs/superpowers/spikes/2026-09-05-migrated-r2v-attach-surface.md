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

**Lead worth following first:** `UpteDb` and `DTaVef` appear in the captured traffic and
are unaccounted for. If one of them fires on "Add to prompt", the attach is **server-side**
and the answer is already in the traffic — no submit needed. The probe currently discards
non-`YhhmEf` payloads; recording them is the cheapest next step.

## What this does NOT settle

- **The confirm semantics.** Whether "Add to prompt" inserts a token into the prompt text,
  attaches an entity out of band, or both, is unknown. That distinction decides whether the
  submit carries `referenceEntities` (which the labs backstop `_assert_entities_attached`
  checks) or just prompt text — and therefore whether a run can silently generate an
  unreferenced clip.
- **Search vs Recent.** Only a Recent list was observed. Whether the search box is required
  for an asset outside it, and how it matches (caption? filename? — labs indexes a short
  auto-caption, not the prompt, per exit 32 `ReferenceNotFoundError`), is untested.
- **Local files.** `--ref <path>` needs the library's `Upload` flow, which was seen but not
  driven. No `input[type=file]` exists until it is.
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
