# Catalog display-name picker spike (#529)

**Verdict:** Flow's browser media picker is searched by the asset's Flow
`displayName`. The UUID is then used to identify the exact result tile. Scanning
the unfiltered virtualized grid and typing UUID/prompt fragments are unnecessary
for a catalog asset whose name was retained.

## Candidate corpus

The read-only candidate corpus came from populated private test projects. One
picker had a useful name-collision case. Stable aliases replace live account,
project, media, story, and caption identifiers:

| Flow display-name alias | Media alias |
|---|---|
| `SHARED_NAME` | `MEDIA_A` |
| `SHARED_NAME` | `MEDIA_B` |
| `UNIQUE_NAME` | `MEDIA_C` |

No incident headers, cookies, signed URLs, screenshots, raw network payloads,
or live identifiers were copied into this artifact.

## Live procedure and result

Date: 2026-08-15. Browser: headed system Chrome using a saved test profile. The
harness opened the existing project, switched to image mode, opened
the picker, aligned its project selector, and performed two searches:

1. `SHARED_NAME` returned both distinct media tiles.
2. `UNIQUE_NAME` returned its expected media tile.

For every result, the assertion used
`[role='option']:has(img[src*='<expected UUID>'])`. The duplicate-name search
therefore proved that display name is discovery while UUID remains identity.

Sanitized outcome:

```text
success: true
scroll_calls: 0
asset_clicks: 0
generation_requests: 0
elapsed_seconds: 19.78
```

## Implemented-path verification

After the change, a second headed-Chrome run invoked the branch's actual
`VideoGenerationMixin._select_existing_asset()` path for `SHARED_NAME` and
requested `MEDIA_A`. A sentinel replaced the unfiltered-grid scroll helper and
would have failed the run if called.

```text
success: true
resolved_by: display_name
scroll_calls: 0
asset_clicks: 1
generation_requests: 0
elapsed_seconds: 13.90
```

The exact UUID tile attached and the picker closed. No prompt was submitted and
no generation endpoint was requested.

## I2V frame-slot verification

A third headed-Chrome run exercised the branch's actual
`VideoGenerationMixin._attach_frame_by_media_id()` path through the I2V Start
frame slot. It used the same aliased `SHARED_NAME` / `MEDIA_A` pair and the same
sentinels against unfiltered-grid scrolling and generation endpoints.

```text
success: true
surface: i2v_start_frame
resolved_by: display_name
scroll_calls: 0
asset_clicks: 1
generation_requests: 0
elapsed_seconds: 17.59
```

The real frame-slot picker attached the exact UUID tile. No prompt was entered,
no submit action ran, and no credits were spent.

## Catalog root cause

`GeneratedImage.from_response_dict()` already extracts the searchable name from
the response's sibling `workflows[].metadata.displayName`. The live UI transport,
however, collected each `media[]` item with `from_response_item()`, losing that
sibling metadata before `OperationRecorder` could persist it. Local catalog rows
therefore had empty names even though Flow itself had named picker assets.

The implementation fixes that collection path, enriches UUID refs from catalog
metadata, passes separate names for I2V start/end UUIDs, and removes scroll,
UUID-stem, UUID, and prompt-derived search tiers from UUID asset selection.

## Superseded hypothesis

The earlier #287 guidance that prompt fragments should surface generated assets
was based on treating tile captions as generation prompts. Captured comparisons
show the captions differ from the recorded prompts, and this spike demonstrates
the direct source of truth: `workflows[].metadata.displayName`. Issue #541 records
the refuted prompt-hint hypothesis.
