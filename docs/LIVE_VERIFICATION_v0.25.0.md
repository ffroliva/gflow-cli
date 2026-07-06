# Live Verification — v0.25.0

Release date: 2026-07-06. Headline change: **remote image UUIDs in
`gflow_generate_video`** (#237), plus the `$GFLOW_CLI_HOME/.env` dotenv fallback
(#240 — the minor-bump reason) and the shadowed-duplicate `Settings.daemon_token`
removal (#243). Live verification of the #237 attach found — and this cycle fixed —
that the originally-merged mechanism could not work, and surfaced two pre-existing
silent-failure guards that are now fixed too.

All live runs: 2026-07-06, headed Playwright, profile `ffroliva`, against real Flow.

## Scope

| Change | Surface | Verdict |
|---|---|---|
| **#237** — UUID image refs in `gflow_generate_video` | `mcp/tools.py`, `paths.py` | ✅ **Live e2e GREEN** after the attach was reworked (below) |
| **#240** — `$GFLOW_CLI_HOME/.env` fallback | `config.py` | ✅ Live CLI precedence matrix + pinning tests |
| **#243** — duplicate `daemon_token` removed | `config.py` | ✅ Automated (`tests/test_config.py`) — internal fix, no live surface |
| Hardening — video-as-image download guard | `api/client.py`, `paths.py` | ✅ Unit-verified; root-caused live |
| Hardening — rejected-upload fail-loud | `ui_automation_video.py` | ✅ Unit-verified; root-caused live |

## #237 — remote image UUIDs → video (the rework)

**The originally-merged mechanism was broken.** It resolved the UUID to a *display
name* and searched Flow's resource picker for the tile. Instrumented live
diagnostics (picker aria-tree + screenshots) proved generated media never appear
in that picker: Flow's asset search does not index generation prompts, and
generated assets carry no display name (0 of 190 catalogued assets had one), so
the picker returned **"No results found"** and every attach timed out — in scratch
projects and in the asset's own project alike.

**Fix (this cycle):** the UUID is resolved to the image's on-disk local file
(already saved by the image generation) and attached through the same proven
file-upload path used for a local `--initial-frame`. No picker name-search.

### Live end-to-end result — GREEN

`gflow_generate_video(mode="i2v", initial_frame="<generated-image-UUID>", …)`
driven through the **real stdio MCP server** (`gflow mcp run`), start frame a
generated image (valid JPEG, `60dcb880…`). 5-layer ledger:

1. **File count**: exactly one MP4 written (`d7749780-…-2ae354.mp4`).
2. **Magic bytes**: `00 00 00 20 66 74 79 70 69 73 6f 6d` (`ftypisom`, valid ISO-BMFF MP4), 5.30 MB.
3. **Shape**: `ffprobe` → h264, **720×1280** (9:16 as requested), duration **8.0 s**.
4. **Structlog / wire invariants**: upload `status: 200`; generate route
   **`batchAsyncGenerateVideoStartImage`** (the true i2v route, **not** the T2V
   `batchAsyncGenerateVideoText` route); captured `startImage` bound; task
   `status: completed`.
5. **User-confirmable artifact**: a mid-clip frame shows the wooden **"v0.25.0"**
   sign *animating* (drifting dust, camera push-in, "Veo" watermark) — a genuine
   interpolation of the still start frame.

### Fail-fast paths (also live-verified, credit-free)

- Unknown UUID → RFC 9457 **`Reference Not Found`** in ~1.5 s via the real MCP
  server (no browser, no timeout).
- Catalogued asset with no on-disk file → **`Reference Not On Disk`** (re-generate
  or pass a local path; auto download-by-media-id is a planned follow-up).

## #240 — `$GFLOW_CLI_HOME/.env` fallback (live CLI matrix)

Marker `GFLOW_CLI_DB_PATH` observed via `gflow data list images` from a foreign
CWD: home-`.env` only loads ✅; CWD `.env` beats home ✅; process env beats both ✅;
set-but-empty `GFLOW_CLI_HOME` treated as unset (default-home `.env` still loads)
✅. Pinned by `tests/test_config.py`.

## Hardening — two silent-failure guards (root-caused during this verification)

The first e2e attempts failed at the frame upload with a confusing i2v→T2V (#125)
fallback. Root cause was **not** the pipeline: the "image" fed as the start frame
(`f94bdd3f…_1.png`) was actually an **MP4 video** — its magic bytes are `ftypisom`.

1. **`gflow_generate_image` produced a video-as-`.png`.** The agentic image path
   has no explicit image-mode toggle (`switch_to_image_mode` is a no-op) — Flow's
   conversational agent infers image-vs-video from the prompt and produced a video,
   whose tile `await_images` scraped as an image. The MP4 bytes were saved `.png`
   (`extension_from_magic` only knows image formats) and catalogued as an image.
   **Fix:** `download_image` now rejects video magic bytes (`looks_like_video`:
   ISO-BMFF / WebM) with a `WireFormatError` instead of writing the corrupt file.
2. **Rejected uploads were treated as success.** `_upload_via_open_dialog` matched
   the `uploadImage` response by URL only and ignored its status, so a Flow **400**
   (Flow correctly rejecting the non-image bytes) committed an empty slot → T2V
   fallback. **Fix:** the upload status is checked (`_upload_rejection_message`) and
   a 4xx aborts loudly.

Both are pre-existing (present in 0.24.0) and unit-tested. The underlying "agentic
agent produces a video for an image request" is tracked as a separate follow-up;
these guards convert the resulting silent corruption into a clear, actionable error.

## Automated coverage

- `pyright src`: 0 errors. `ruff check` / `ruff format --check`: clean.
  `check_doc_links.py`, `check_repo_hygiene.py`: clean.
- New unit tests: UUID→local-path resolution + fail-fast cases (`tests/mcp`),
  `looks_like_video` + `download_image` video rejection (`tests/test_paths.py`,
  `tests/api/test_client_image.py`), `_upload_rejection_message`
  (`tests/api/transports/test_ui_automation_video.py`).
