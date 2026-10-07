# Live verification — v0.83.0

> Evidence for this release, gathered during development on 2026-10-06 and 2026-10-07 (PRs
> #952, #922, #953). Where a check could not be run, the blocker is named. Each row is a run
> someone watched, not an inference.

## Environment

| | |
|---|---|
| Profile | `ffroliva` (real-browser Chrome strategy); `denon82` for one locale read |
| Host Flow served | `flow.google.com` (classic composer) |
| Transport | `ui_automation` → migrated composer |
| OS | Windows 11 |
| Veo credits spent | 8 × video runs during development (spikes + e2e, standing e2e grant); upscale $0 |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| A video record with `null` where `"CAE"` was is still found (#948, PR #952) | t2v `YhhmEf`, i2v `eb1hJf`, r2v `MZZa6b` submits + `jwpduf`/`as29s` status | ✅ e2e t2v, i2v, r2v **all passed** after the fix; reproduced before it (exit 7 on a billed run) | 1 clip each |
| A run picks its own record from a multi-record reply (PR #952) | status listener, `gflow data download` | ✅ t2v e2e passed after the change; `test_data_download_e2e.py` **2 passed** | t2v: 1 clip; data download $0 |
| `data download` reads only `as29s` (PR #952) | clip route | ✅ failed with HTTP 302 before (probe: the `Zzl0ze` listing's unsigned lh3 URL was taken), passed after | $0 |
| A missing record says the run may be billed, not "retry" (PR #952) | error remediation | offline (unit test asserts the hint); no Flow surface | $0 |
| `gflow image upscale` on flow.google.com (PR #922) | image download menu → `SPrCad` | ✅ e2e 2K **passed**; CLI and MCP `gflow_upscale_image` wrote the same 2,729,819-byte JPEG (`ffd8ff`) | $0 |
| `gflow video upscale` + MCP `gflow_upscale_video` (PR #922) | video download menu → export | ✅ e2e 1080p **passed**; MCP twin wrote a 1920×1080 MP4 (`ftyp`, 22,162,649 B) | 0 credits (measured) |
| `gflow video upscale --scale 720p` (PR #922) | CLI | ✅ exit 0, 1280×720 MP4 (`ftyp`), 8.68 MB | $0 |
| `gflow video upscale --scale 270p` (PR #922, fixed in PR #954) | CLI + e2e | ✅ after the fix: `test_migrated_video_export_270p_gif_e2e` passed (`GIF8`); CLI run exit 0, 8.2 MB `GIF89a`. Before: 1 of 2 CLI runs timed out at the 120 s budget (exit 9) | $0 |
| 4K image fail-fast (PR #922) | CLI | ✅ `--scale 4k` exit 22 `UpscaleUnavailableError` on an account showing 4K disabled | $0 |
| Upscale refactor for the Sonar gate (PR #953) | both transports | ✅ upscale e2e re-run on the refactored tree: **2 passed** (38 s) | $0 |

## #948 — the generation record lost its `"CAE"` marker

Spike `scripts/dev/spike_948_submit_envelope.py`
([findings](superpowers/spikes/2026-10-06-948-record-slot3-null.md)):

- **Before the fix:** one veo-lite t2v reproduced the report — `WireFormatError … no generation
  record` (exit 7) on a submit Flow accepted and billed. The `YhhmEf` reply still carried the
  full record; only slot 3 changed (`"CAE"` → `null`), and `jwpduf` the same.
- **After the fix**, timeline of one run: `YhhmEf` status 6 at 12.3 s → `jwpduf` 2 every 5 s →
  `jwpduf` 3 at 53.3 s → `as29s` 3 with the video URL at 55.8 s; run succeeded.
- **e2e:** `test_e2e_t2v_runs_on_flow_google_com_by_default` passed (47 s, and 59 s after the
  multi-record change); `test_e2e_i2v_from_a_local_start_frame_runs_on_flow_google_com` passed
  (58 s, asserts `submit_observed rpc == "eb1hJf"`); `test_e2e_r2v_binds_local_references_on_the_migrated_host`
  passed (90 s). Each wrote an mp4 with `ftyp` at offset 4.
- **Recover path:** `test_data_download_e2e.py` failed with HTTP 302 after records were selected
  by media id. A probe on the same clip showed the PR tree took the `Zzl0ze` listing's unsigned
  `lh3.googleusercontent.com` URL and develop took `as29s`'s signed `flow-content.google` URL.
  `_collect` now reads only `as29s`; the e2e then passed 2/2.
- **Not measured:** one t2v e2e timed out at 600 s before the multi-record change, with the clip
  later done on Flow's side; its log was not kept. If it recurs, run the spike above.

## #922 — upscale on flow.google.com

Spike `scripts/dev/spike_upscale_menu_anchors.py` (2026-10-07):

- **Menu structure (en):** every item is `button[role=menuitem]` with no per-option attribute
  or icon. Image: 1K, 2K, 4K (disabled). Video: 270p, 720p, 1080p, 4K (disabled). The
  2026-09-30 pt capture had a different video order and translated words around the same
  tokens → items are anchored on the resolution token (AGENTS.md records the exception).
- **Export cost:** balance 700 → 1080p export (e2e passed, 62.8 s) → 690 after a 10-credit
  veo-lite control clip ⇒ the export spent **0 credits** (one observation).
- **e2e at the merged code:** `test_migrated_image_upscale_2k_e2e` and
  `test_migrated_video_upscale_1080p_e2e` passed (46 s), asserting a larger 2K output, a
  `tkhd` short side ≥ 1080 and the migrated transport's log events; again on the PR #953
  refactor (38 s).
- **Both doors:** CLI `gflow image upscale` (exit 0) and MCP `gflow_upscale_image` wrote the
  same 2,729,819-byte JPEG; MCP `gflow_upscale_video` wrote a 1920×1080 MP4.
- **270p GIF (doc-review run, then PR #954):** the doc-review auditor required a live run of
  the 720p/270p/4K claims. 720p and the 4K exit 22 passed. 270p timed out once at 120 s
  (exit 9). `scripts/dev/spike_gif_export_delivery.py` showed the delivery is the expected
  `image/gif` blob (8.25 MB) via `createObjectURL`, arriving 40 s after the click; a second
  CLI run succeeded in 104 s. Flow renders the GIF client-side with a variable delay, so PR
  #954 gives 270p a 300 s budget and a timeout hint that says a re-run is free; its new e2e
  arm passed with the 2K and 1080p arms (3 passed, 86 s).
- **Not measured:** a 4K upscale that succeeds (only an Ultra account would show it enabled); a non-English account's menu was
  read from the 2026-09-30 capture, not re-run (the `denon82` download button was disabled on
  its only catalogued image).

## Not in this release

PR #824 (agent-only composer) is not merged: its e2e needs an account Flow serves the
agent-only composer, and none of ours is (`denon82`, `ci-probe`, `ffroliva` all get the
classic composer). Requested from the contributor.

## Pre-tag gates

| Gate | Result |
|---|---|
| `/gflow:changelog` | `[Unreleased]` → `[0.83.0] — 2026-10-07`: Added (upscale port, `gflow video upscale`, MCP tools), Changed (4K tier detection), Fixed (#948 ×2) |
| `/gflow:check` | ruff, format, pyright 0, hygiene, doc links, PII, mirror, council memory green; pytest 4910 passed, 24 skipped, coverage 93% |
| SonarCloud (develop @ 33bfbf3c) | gate OK after PR #953 cleared the #922 merge's 5 issues |
| `/gflow:doc-review` | council: Auditor 1 RED (720p/270p/4K unrun) → all three run, 270p fixed in #954; Auditor 2 YELLOW (#948 overclaim, README/USAGE drift) → fixed in this commit; Auditor 3 GREEN. Reports local at `tmp/council/` |

## Post-tag evidence

| Evidence | Result |
|---|---|
| Signed tag | `v0.83.0` (SSH signature) on `5d6f46c4` |
| Release workflow | [run 37603775449](https://github.com/ffroliva/gflow-cli/actions/runs/37603775449): `build-and-publish` success, `mcp-registry / publish` success |
| GitHub Release | https://github.com/ffroliva/gflow-cli/releases/tag/v0.83.0 (not a prerelease) |
| PyPI | `gflow-cli 0.83.0` served by pypi.org/pypi/gflow-cli/json |
| Clean install | `uvx --refresh --from gflow-cli==0.83.0 gflow --version` → `gflow, version 0.83.0` |
| Release PR | #955 merged into `main` (`9d8bebbc`, merge commit); back-merged into `develop` (`52574004`) |
