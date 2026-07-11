# Live verification — v0.32.0 (2026-07-11)

Scope: the two user-facing changes of this release, exercised against live Flow
on the `denon82` profile (Classic strategy, Chrome, Windows 11; Flow renders in
Portuguese for this profile — `/fx/pt/` redirect). Credits spent: **1 image +
1 video generation**; every failure-path check completed pre-submit at zero
credit cost.

## 1. `gflow video i2v --initial-frame <UUID>` — in-project asset selection (#287, PR #290)

The declared-riskiest surface: the frame-slot dialog carried a negative prior
(#237's name-search never surfaced generated media there — see
[LIVE_VERIFICATION_v0.25.0](LIVE_VERIFICATION_v0.25.0.md)). The thumbnail-URL
tile match succeeds where name search failed.

Run: seed `gflow image t2i` (asset `0bbcdf05-19e0-43c3-bbeb-ba0ff7246e54`,
project `dd9e498c-22b1-4d73-a748-0bc0eac14c8b`), then
`gflow video i2v --initial-frame 0bbcdf05-… "slow cinematic push-in toward the
mug, steam rising gently" --project dd9e498c-… --profile denon82 --json`
(correlation `92b99c8c-5d69-4e00-905f-734f8eb6dadc`).

5-layer ledger:

1. **File count:** exactly 1 mp4 in `--out-dir` (`b61bcce6-baca-4934-b6e5-5ca84e10b49a.mp4`, 3,031,564 bytes).
2. **Magic bytes:** `00 00 00 20 66 74 79 70 69 73 6f 6d` — valid `ftypisom` MP4 header.
3. **Dimensions/shape:** ffprobe `720×1280`, duration `8.000000` s (Flow default — no `--duration` passed, correct).
4. **Structlog invariants:**
   - `ui_automation_video.frame_ref_attached {slot: Start, media_id: 0bbcdf05…}` — UUID picker selection in the frame-slot dialog;
   - `ui_automation_video.generate_captured {url: …/video:batchAsyncGenerateVideoStartImage, image_inputs.startImage: "0bbcdf05"}` — the asset is bound in the actual wire request (no #125 T2V fallback);
   - **zero `image_uploaded` events** in the whole run — no duplicate upload (the founding goal of #287);
   - `poll_terminal {status: MEDIA_GENERATION_STATUS_SUCCESSFUL}` → `video_saved`.
5. **User-confirmable artifact:** video `b61bcce6-…` in Flow project `dd9e498c-…` (denon82 account) — a genuine motion interpolation of the seeded red-mug still.

Negative check (free): a foreign UUID (`00000000-0000-4000-8000-000000000001`)
in the same project → **exit 9** (`TransportTimeoutError`) pre-generation, the
message naming slot + UUID + the `--project` hint, with
`debug_frame_ref_miss_start.png` captured (valid PNG).

Not verified this cycle: a live `uploadImage` HTTP-400 → exit 27
(`MediaUploadRejectedError`) repro — the original Flow-rejected JPEG from the
2026-07-11 pilot no longer exists on disk (only thumbnails survive). The typed
raise is pinned by a unit test that drives the real `_upload_via_open_dialog`
listener with a 400 response (`tests/api/transports/test_ui_automation_video.py::TestUploadRejectionTypedError`).
The KNOWN_ISSUES entry on metadata-sensitive rejections tracks the live case.

## 2. `--duration` fail-fast (#288, PR #289)

Run: `gflow video i2v <local file> "slow pan across the mug" --duration 4
--profile denon82 --json` (correlation `b8e66830-58bf-4928-9afe-1eecbbfcdb60`).

- `duration_tab` probe missed (reproducing the pilot's 3/3) and the run
  **aborted pre-submit with exit 23** (`UiSelectorDriftError`) — the settings
  popover showed "A geração vai usar 10 créditos", so the fail-fast saved that
  spend instead of delivering a silent 8-second clip reported as success.
- `debug_no_duration_tab.png` captured (valid PNG, `89 50 4E 47` header) and
  **answers the #288 investigation: the duration control is absent from this
  cohort's Frames-submode settings popover** (rows rendered: Imagem/Vídeo →
  Frames/Elementos → 9:16/16:9 → 1x/x2/x3/x4 → model dropdown; no duration
  row). The council's locale hypothesis is refuted — the UI is Portuguese and
  the sibling text-matched count tabs match fine.
- Happy-path caveat, recorded per the honesty rule: "duration tab found →
  clicked" is unverifiable on this cohort because the control does not exist.
  If a cohort still renders duration tabs, the pre-existing selector cascade
  should match them unchanged; the fail-fast only fires on a miss.

## Incidental live findings (logged on their issues)

- Project `f6caf027-…` opens in the full-page media-library UI (#174 shape,
  "Agente" composer pill) where even `mode_switch_trigger` fails — fresh
  projects still get the classic composer and work. Posted to #288/#174 trail.
- Error-path browser teardown can leave Chrome holding the profile dir
  (subsequent runs die with `TargetClosedError` until the process is killed),
  and `_capture_debug_screenshot` can report a screenshot path it never wrote
  when capture fails. Both noted as #283-wave follow-ups.
