# Spike — how the migrated flow.google.com editor takes a start frame (i2v), $0

**Date:** 2026-09-05 · **Profile:** `ffroliva` (moved to flow.google.com) · **Project:** `c5550ed7…`
(3 catalogued images) and `300f5260…` (empty) · **Script:** `scripts/dev/spike_migrated_frames_attach.py`
· **Evidence:** `scripts/dev/_spike_out/migrated_frames_attach_ffroliva_20260905_19{01,03,07,09,11}*.json`
+ screenshots · **Credits spent:** 0 (every submit aborted in-browser by the route-abort marker pattern).

This answers the one blocker every persona of the i2v predict (`tmp/predict/00-verdict.md`) shared:
how an image gets INTO a migrated project and onto a frame slot. Both paths are now observed.

## Findings

| # | Question | Answer (measured) |
|---|---|---|
| 1 | Where is the frame affordance? | Selecting the Frames submode (`[role='radio']:has(mat-icon:text-is('crop_free'))` in the settings pane) makes the composer render two text chips `button.empty-chip` ("Start", "End") around a `swap_horiz` icon-button. No ligature on the chips; no `input[type=file]` anywhere on the page. |
| 2 | What does the Start chip open? | The **"Select a frame image" picker**: `.cdk-overlay-pane` > `flow-add-menu-popover-content` with a project `mat-select`, a text `input` (aria-label "Search assets" — localized, do not anchor), a sort `mat-select` ("Recent"), `flow-add-menu-asset-list` (`cdk-virtual-scroll-viewport`) of `flow-add-menu-asset-item` = `button.asset-item[role='option']`, a `flow-add-menu-detail-pane` with an "Add to prompt" button, and a `flow-media-upload` component. **Library-only**: no upload entry in the picker menu. |
| 3 | Do tiles expose a media UUID? | **No.** `button.asset-item` carries only the display name as text and an `lh3.googleusercontent.com` thumbnail. Display name = prompt title for generated images ("Blue sphere on table"), **file name for uploads** ("01-pre-submit.png"; older labs-driver uploads show as `<uuid>_1.jpg`). |
| 4 | Does the search box filter? | Yes, server-side via rpc **`UpteDb`** (200, ~5–6 KB, carries the project list + asset entries with uuids). Typing "sphere" → 1 tile; "01-pre-submit" → the uploaded file(s). |
| 5 | Does clicking an option bind the frame? | Yes, immediately: the Start chip becomes `button.chip-container` holding `img[src*='flow-content.google']` (alt "Ingredient image") plus a `cancel` ligature; "End" stays an `empty-chip`. |
| 6 | What does an i2v submit put on the wire? | rpcid **`eb1hJf`** (NOT `YhhmEf`), body key **`veo_3_1_i2v_lite`** (Veo 3.1 Lite) and the **bound media id** (`e73d80e6…` / `14a663ea…` observed) + project id + 3 session uuids. An unbound Frames submit (chips empty) went out as `YhhmEf` with the t2v key `veo_3_1_t2v_lite` — the labs #125 shape on the new host. After the aborted submit, `WuwhI` (~7.6 KB, carries the prompt) fired twice. |
| 7 | How does a local file get in? | Toolbar `+` (`button:has(mat-icon:text-is('add'))` outside `flow-prompt-box`) → `[role='menuitem']` with ligature `upload` (also `folder` New collection, `account_circle` Create character, `play_movies` New scene) → **`filechooser` fires** (multiple=true) → `set_files` → in-page reCAPTCHA `enterprise/reload` → rpc **`maseQ`** POST (~120 KB, the image inline) → **200** with `[<new media id>, <project id>, <second uuid>, "CAE", …]` → optimistic `flow-image-tile` at 0.24 s, real thumbnail later. |
| 8 | Can the uploaded file be bound? | Yes: picker search by its file name → first option under "Recent" → bound → the aborted `eb1hJf` body carried `14a663ea…`, the id `maseQ` returned. **Upload → pick → submit-body proven end to end at $0.** |
| 9 | Image mode | Mode radio `image` → aspect radios 16:9 / 4:3 / 1:1 / 3:4 / 9:16; model button "🍌 Nano Banana Pro". (t2i keys not captured this session — no image submit was attempted.) |
| 10 | Changelog modal | Not shown on this account this session (already dismissed once); a "high demand" info banner was present, non-blocking. |

## What this settles for the port

- **Slice 1 = local file** (`--initial-frame <path>`): every step is UI-driven and observed — upload via the
  toolbar menu + file chooser, bind via the picker by file name, submit via `eb1hJf`. The media id is known
  from the app's own `maseQ` reply, so the `eb1hJf` body can be asserted to carry it (the migrated twin of
  `_assert_i2v_route`). Duplicate file names are possible (two uploads of the same file both list as
  "01-pre-submit.png"); "Recent" sort puts ours first, and the body assertion catches a wrong pick.
- **Slice 2 = in-project UUID** (`--initial-frame <uuid>`, #287): the picker has no UUID anchor. Options:
  map uuid → display name through the local catalog (`gflow data media <id>` knows the prompt) or through
  the `UpteDb` reply the picker itself fetches, then search by name and assert the id in the `eb1hJf` body.
- **End frame**: same chip, same picker — untested but structurally identical (`button.empty-chip` "End").
- **Status after `eb1hJf`**: not observed (no real submit). Assumed `jwpduf`/`as29s` as for t2v; the first
  billed run of the live-verify is where that is confirmed.

## Anchors for the driver (all structural / ligature)

```
frames submode     [role='radio']:has(mat-icon:text-is('crop_free'))          (settings pane)
start / end chip   flow-prompt-box button.empty-chip  (nth 0 = Start, nth 1 = End); bound = button.chip-container:has(img)
picker             .cdk-overlay-pane:has(flow-add-menu-popover-content)
picker search      <picker> input[type='text']
picker option      <picker> flow-add-menu-asset-item button.asset-item[role='option']   (text = display name)
toolbar add        button:has(mat-icon:text-is('add')) not inside flow-prompt-box
upload menu item   .cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('upload'))
upload rpc         maseQ  (200 → [media_id, project_id, …])
submit rpc (i2v)   eb1hJf (body: model key *_i2v_*, bound media id)      t2v stays YhhmEf
asset search rpc   UpteDb
```

## Not observed (recorded, not omitted)

- A real i2v generation on the new host (status rpcs after `eb1hJf`, the result record, the download).
- Binding the End frame; binding on the `denon82` (pt) account; the picker on a project with >1 page of assets
  (virtual scroll).
- `maseQ` for a JPEG-with-EXIF file (KNOWN_ISSUES records a labs 400 for that class).
- The image-mode submit keys.
