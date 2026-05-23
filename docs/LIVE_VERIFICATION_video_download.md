# Live verification — video download + `gflow video t2v` CLI (#29)

> Hand-run on `feature/video-download-cli` against the real Flow API. Each
> row below is a credit-spending live test that succeeded end-to-end
> (prompt → generate response captured → status terminal SUCCESSFUL → mp4
> downloaded via `media.getMediaUrlRedirect` → bytes written to disk).
> This document is the evidence that the new
> `UiAutomationTransport.generate_video(..., download=True)` path,
> `_download_video` helper, and `gflow video t2v` CLI work end-to-end on
> production Flow.

## Environment

| | |
|---|---|
| Branch | `feature/video-download-cli` |
| PR | [#36](https://github.com/ffroliva/gflow-cli/pull/36) |
| Local version | `0.6.0a6` (post-v0.7.0; pre-next-bump) |
| Profile | `ffroliva` (real-browser Chrome strategy — required per project memory `real-browser-auth-mandatory.md`) |
| Profile dir | `C:\Users\ffrol\AppData\Local\ffroliva\gflow-cli\profile_ffroliva` |
| Date | 2026-05-21 |
| Transport | `ui_automation` |
| Mode | `T2V` |
| Count | 1 (forced via `_set_output_count_one` — Flow default is x2) |
| Out dir | `tmp/` |

## How to reproduce

```pwsh
$env:PYTHONUTF8=1
uv run gflow video t2v "a calm forest at dawn" --aspect 9:16 --profile ffroliva --out-dir tmp/
uv run gflow video t2v "a calm forest at dawn" --aspect 16:9 --profile ffroliva --out-dir tmp/
```

`PYTHONUTF8=1` is required on Windows because Rich's status-list bullet
glyph (`●`) is not encodable in the default cp1252 console codepage.

## What was tested

`gflow video t2v` was run against both production-supported aspect
ratios. "Captured" means the `batchAsyncGenerateVideoText` HTTP response
was observed by the `ui_automation_video.generate_captured` listener
log; "Terminal" means a `MEDIA_GENERATION_STATUS_SUCCESSFUL` was parsed
out of the status-poll stream; "Saved" means
`media.getMediaUrlRedirect` was followed and the mp4 body was written
to `tmp/<media_id>.mp4`. All file magic bytes confirm valid ISO Base
Media MP4 (`ftypisom`).

| # | Aspect | Result | Submit → captured | Submit → terminal | File size | Saved as |
|---|---|---|---|---|---|---|
| 1 | `9:16` (portrait) | OK | ~4 s | ~68 s | 13,598,272 B (12.97 MiB) | `f6ae0022-fb77-44ac-8a44-7c68f3a7c985.mp4` |
| 2 | `16:9` (landscape) | OK | ~3 s | ~67 s | 19,258,158 B (18.37 MiB) | `63297e21-70b5-49f9-ae35-a511b3c321ae.mp4` |

Portrait (9:16) closes the Phase A coverage gap — Phase A's live
verification only exercised landscape. Landscape is re-verified here on
the new download-enabled path.

## Wire-format invariants confirmed

- The `batchAsyncGenerateVideoText` response carries `media[0].name` as
  the generation UUID — both runs returned `media_name == media_id`
  on the subsequent status poll.
- The status-poll route is `batchCheckAsyncVideoGenerationStatus`. The
  `_attach_status_response_listener` captured the Flow SPA's own
  polling traffic — `_poll_video_status` reached terminal without
  needing the stall-nudge fallback in either run.
- `media.getMediaUrlRedirect?name=<media_id>` 302s to a signed GCS URL.
  `page.request.get(url, max_redirects=5)` followed the redirect
  transparently and returned the mp4 body without any explicit
  cookie or token plumbing.

## Correlation IDs (for log triage)

| Aspect | Correlation ID | Media ID |
|---|---|---|
| `9:16` | `65831bf8-22ac-4a86-b79f-f45f8c5dff24` | `f6ae0022-fb77-44ac-8a44-7c68f3a7c985` |
| `16:9` | `52bd2b4a-d0d8-43a5-a6ad-c55778589c4a` | `63297e21-70b5-49f9-ae35-a511b3c321ae` |

## What was NOT verified

- `--aspect 1:1` (SQUARE) — the transport raises `ValueError` for SQUARE
  on the video path (Flow doesn't offer it); the CLI only accepts
  `9:16` / `16:9`.
- I2V (image-to-video) and R2V (reference-to-video) modes — still raise
  `NotImplementedError` on `UiAutomationTransport`; tracked under
  Phase B follow-ups.
- `--download=False` opt-out — covered by unit tests
  (`tests/api/transports/test_ui_automation_video.py`); not exercised
  live because the goal of this verification is the download path.
- Long prompts / NSFW / content-policy rejection — happy-path only.

## Outputs

Both mp4s are local-only (not committed) and live under `tmp/` per the
project's "All script and test runtime output goes to `tmp/`" rule.
The CI hygiene gate (`scripts/ci/check_repo_hygiene.py`) blocks
committing anything under `tmp/`.
