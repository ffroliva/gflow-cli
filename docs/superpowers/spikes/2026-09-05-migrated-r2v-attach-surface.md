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

## Consequence for the port

The attach mechanism is a **prompt-level mention**, not a slot. That is a different shape
from the labs driver's `_attach_r2v_references` and should not be ported by analogy — the
r2v path here is closer to "compose the prompt with mentions, then submit" than to "fill
slots, then submit". Establishing the confirm semantics above is the next $0 step, and it
gates everything else.
