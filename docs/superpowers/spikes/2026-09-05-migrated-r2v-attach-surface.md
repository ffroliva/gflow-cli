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

## What this does NOT settle

- **The confirm semantics.** Narrowed, not settled: it is **not** a server-side rpc
  (Round 3), so it must write into the prompt document — but no gesture has yet produced
  that write, so the resulting node shape, and whether the submit then carries reference
  ids, remain unobserved. This still decides whether a run can silently generate an
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
