# A billed clip stranded by exit 7 is recoverable at $0 — `as29s` mints the signed URL

**Date:** 2026-09-17 · **Issues:** [#865](https://github.com/ffroliva/gflow-cli/issues/865), [#871](https://github.com/ffroliva/gflow-cli/issues/871) · **Cost:** $0 (navigation, one click per clip; nothing generated)
**Probe:** [`scripts/dev/spike_recover_stranded_clip.py`](../../../scripts/dev/spike_recover_stranded_clip.py)
**Captures:** `scripts/dev/_spike_out/` (gitignored — the frames carry signed CDN URLs)

## Question

#865 and #871 are the same ask: a generation that finished and billed, then failed at
download with

```
WireFormatError exit 7 — migrated host: the generation finished but no signed media URL
was observed within the 20s grace
```

leaves a paid asset with `local_path: null, copy_count: 0` and no CLI verb to fetch it.
#871 states the blocker up front: **"Flow's DOM does not carry the media id"**, and
proposes falling back to prompt text plus `--index`.

Both issues were filed against the same two orphans. **Both clips are now recovered**,
byte-exact, and neither fallback is needed.

## What was observed

Profile `ffroliva`, project `339f65ee-…`, the two orphans named in #865.

### 1. The listing binds media id → workflow id, and the DOM binds structurally

`Zzl0ze` on project load carries 183 records. The one for a stranded clip is complete:

```
[0]        workflow_id   d8e72026-…
[1]        project_id    339f65ee-…
[2]        media_id      23620c34-…
[5][8][0]  status        3 (done)
[5][13]    size_bytes    2410295
[5][6][1][0][0]  model   abra_r2v_8s
```

The grid renders each clip as a **`flow-video-tile`** custom element whose `img.thumbnail`
is an `/asb/<token>` URL — the *same token* the record carries. Matching tokens bound
**13/13 tiles** to their media ids on the first attempt.

> **#871's blocker does not exist.** The id is absent from the DOM as *text*, but the tile
> and the record share an opaque token, so the binding is exact and needs no display
> string. The `--index` fallback can be dropped from the design.

### 2. The record's own URL is a poster, and its renditions are transcodes

All three URL slots — `[5][5]`, `[5][10]`, `[7][0][8]` — hold one
`lh3.googleusercontent.com/asb/<token>`. Fetched bare it 302s to a **47 751 B JPEG**.
Twenty-five suffixes were measured:

| suffix | result |
|---|---|
| `=m18` | HTTP 200, valid `ftyp`, **356 946 B** (360p transcode) |
| `=m22` (and `-d`, `-nu`, `-rw`) | HTTP 200, valid `ftyp`, **1 209 675 B** (720p transcode) |
| `=dv`, `=dv-m22`, `=m22-dv`, `=d-dv`, `=nu-dv`, `=no-dv` | HTTP 500 |
| `=dm`, `=dg` | HTTP 400 |
| `=m0`, `=m34`–`=m299`, `=m37`, `=m71` | HTTP 404 |

**None returns the record's own `size_bytes`.** Two of them return a valid MP4 that is the
wrong file — shipping either would be the #281 class of silent corruption, and an `ftyp`
check alone does not catch it. `size_bytes` is the discriminator.

### 3. `as29s` mints the signed URL, and a tile click is enough to provoke it

Flow's own **Download → 720p Original size** issues exactly two things: an `as29s` call,
then

```
GET flow-content.google/video/<workflow_id>?Expires=…&KeyName=…&Signature=…
```

The path is derivable from the listing; **the signature is not** — a bare GET of
`/video/<workflow_id>` returns **HTTP 403**. Staged cheapest-first, the trigger turned out
to be far below the menu:

| stage | `as29s` calls | signed URLs |
|---|---|---|
| project load | 0 | 0 |
| **tile click** | **31** | **7** |

So the context menu is not on the path at all. `as29s` is already in gflow's
`STATUS_RPCS`, and `generation_record()` already reads the signed URL from slot
`(7,0,8)` — the same decoder the in-run download uses.

### 4. The tile is not needed at all — there is a per-clip route

Clicking a tile navigates to:

```
https://flow.google.com/project/<project_id>/edit/<media_id>
```

**Both ids are already in the catalog**, so the route is derivable without touching the
DOM. Opening it directly provokes the same `as29s` call and yields the same signed URL.

This matters beyond elegance. The grid is virtualized — **13 rendered tiles against 183
records**, recycling to 0 on scroll — so a tile-based implementation reaches only the
newest page and cannot recover an older clip at all. The two orphans here happened to be
recent, which would have made a tile-based version look like it worked.

The token binding in §1 is therefore *evidence*, not the shipped mechanism: it proved the
grid could be bound before the cheaper route was found. The implementation uses the route
and touches no DOM, so nothing on this path can drift with Flow's markup or the account
locale.

### 5. End to end, twice

```
tiles=13  bound=183  target_tile=11  expected_bytes=2410295
signed url for target: YES
GET -> HTTP 200  2410295 B  ftyp=True  size_match=True
```

| media id | recovered | `size_bytes` | match |
|---|---|---|---|
| `23620c34-…` | 2 410 295 B | 2 410 295 | ✅ |
| `9ad33c78-…` | 2 702 168 B | 2 702 168 | ✅ |

`9ad33c78-…` also matches, to the byte, the file #871 recovered by hand through the UI.

## Consequences for the implementation

The whole path is existing code plus a join:

| need | already exists |
|---|---|
| `media_id` → `project_id` | `repo.find_assets_by_flow_media_id` (`cli_data.py`) |
| `media_id` + `project_id` → the clip route | derived — no DOM, no listing scan |
| the route → signed URL + `size_bytes` | **`as29s`** + `generation_record()` |
| signed URL → verified bytes | `_fetch_mp4`'s host allowlist + `ftyp`, **plus `size_bytes` equality** |
| clear `copy_count: 0` | `repo.upsert_local_file` |

Claims in the two issues that this retires:

- *"reuse `client.download_video`"* (#865) — labs `media.getMediaUrlRedirect` 404s for a
  migrated media id.
- *"resolve the media from the catalog row"* (#865) — the row gives `project_id` only;
  signed URLs are deliberately stripped by `redact_metadata` (`recorder.py:1210`) because
  they expire. The command must mint a fresh one each run, never cache.
- *"DOM does not carry the media id → `--index`"* (#871) — moot: the media id is in the
  clip's own URL, and no tile is involved. (The `/asb/` token does bind tile to record,
  measured 13/13, but the shipped path never needs it.)
- *"reuse `_fetch_mp4` unchanged"* (#871) — correct for the signed `flow-content.google`
  URL, and `flow-content.google` is already allowlisted. It is **not** safe against the
  `lh3` poster token, whose renditions pass `ftyp`.

## What was NOT measured

- **Anything about the grid**, now that the route replaced it: the virtual-scroll
  behaviour above was measured only far enough to rule the tile approach out.
- **Images.** Only video clips (`abra_r2v_8s`) were exercised. An image asset is a
  shorter record with no status arm; `gflow data download` on an image id is unproven.
- **Clip count beyond one grid page.** 13 tiles, no scrolling. A project whose orphan sits
  below the virtual-scroll fold was not tested, and `virtual-scroll-container` in the
  ancestor chain says tiles are recycled.
- **The labs arm.** Every account here is served flow.google.com.
- **Why `flow_workflow_id` is `None` in the catalog** for both orphans, although
  `on_started` carries `flow_operation_id`. If it were persisted, the project visit could
  be skipped entirely on a later run. Worth its own issue.
