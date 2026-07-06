# Live Verification — v0.26.0

Release date: 2026-07-06. Headline: **reference a generated image in `image i2i`
by its Flow UUID**, attached by **selecting the already-existing asset** in Flow's
reference picker rather than uploading a duplicate copy (the founder principle:
avoid duplication). Plus generated images now record their Flow **display name**
(the searchable picker label), extracted from the response's `workflows[]` array.

Live run: 2026-07-06, headed Playwright, profile `ffroliva`, real Flow.

## Scope

| Change | Surface | Verdict |
|---|---|---|
| Image i2i UUID ref → **select existing asset** (no duplicate) | `mcp/tools.py`, `worker/daemon.py`, `api/image.py`, `api/transports/ui_automation*.py` | ✅ **Live e2e GREEN** |
| Generated-image `display_name` from `workflows[]` | `api/dto.py`, `data/recorder.py` | ✅ Unit-tested (real captured sample); credited @C1ph3r404 |

## Mechanism

An `image i2i` `reference_images` entry that is a Flow media UUID is resolved at
enqueue time (best-effort, never errors — an uncatalogued UUID still attaches by
media id, PR #245) to the asset's `display_name` + on-disk `local_path`. The
transport then **prefers selecting the existing asset in place**:

1. Locate the tile by the **media UUID in its thumbnail URL** (`img[src*=<uuid>]`)
   — robust to display-name collisions, needs no search term.
2. If not already visible, **search the display name** to surface it, then re-locate
   by UUID.
3. Attach in place. (The image picker attaches on tile-click and auto-closes; the
   code also handles the video-style explicit "Add to Prompt" include.)
4. **Fallback — local upload** only when the asset can't be located (e.g. it lives
   in a different project's picker) and a local file exists.

## Live end-to-end result — GREEN

`gflow_generate_image(prompt="the same rustic workshop wall, wider shot, more
tools", reference_images=["60dcb880-…" (a generated image's UUID)],
project="0f4e7eaa-…" (the asset's project), profile="ffroliva")` via the real
stdio MCP server. 5-layer ledger:

1. **Attach path**: structlog `ui_automation_video.image_ref_selected_existing`
   fired — and **no** `image_ref_upload_fallback` / `image_uploaded` event. The
   existing asset was selected; **no duplicate was uploaded**.
2. **Wire**: the generation ran the `batchGenerateImages` route carrying the
   reference `imageInput` — the ref reached the generation, not a plain t2i.
3. **File**: exactly one output written, `bee1e8a9-…_1.jpg`, 893 KB.
4. **Magic bytes**: `ff d8 ff e0` (valid JPEG); task `status: completed`.
5. **User-confirmable artifact**: the output shows the **same** rustic workshop
   wall and the "v0.25.0" sign as the referenced image, as a wider shot with more
   tools — the i2i reference visibly took.

### Pre-fix note

The first live attempt selected the asset correctly but errored looking for a
separate "Add to Prompt" button — the image picker attaches on tile-click and
auto-closes (one step), unlike the video r2v picker. Fixed to treat an
auto-closed dialog as success; the re-run above is GREEN. (A hung Chrome holding
the profile's `SingletonLock` also caused two transient `launch_persistent_context`
failures between runs — cleared by killing the process + removing the lock.)

## Automated coverage

- `_enrich_image_refs` unit tests (`tests/mcp/test_tools_helpers.py`): enriches a
  catalogued ref with display_name + local_path; partial meta when no on-disk file;
  uncatalogued UUID passes through with no error and no `ref_meta`.
- `display_name` extraction tests (`tests/api/test_image_dto.py`) against the real
  captured `06_batchGenerateImages.json` sample.
- Full suite green (1994 passed; the one deselected `test_packaging` case is a
  network-dependent sdist build, unrelated). `pyright src` 0 errors, ruff clean.
