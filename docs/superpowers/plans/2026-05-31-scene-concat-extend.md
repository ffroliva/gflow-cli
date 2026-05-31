# Plan — `gflow scene` server-side concat (extended video) + read-back fix

**Status:** PLAN (pre-council). **Branch:** `bugfix/scene-readback-empty` → rename to `feature/scene-concat-extend` on implement.
**Date:** 2026-05-31. **Author:** Claude (Opus 4.8). **Reviewer gate:** LLM council (`/gflow:predict`) BEFORE implement.

## 1. Goal

Make `gflow scene` produce a **real combined/extended video file** using **Flow's own server-side concatenation** — credit-free, no reCAPTCHA, **no ffmpeg dependency**. This is what users expect from "Add Clip" (a longer video), which the merged L1 did NOT deliver (it only wrote timeline metadata; its read-back was broken).

Confirmed live on denon82 (2026-05-31): a 2×8s scene → **16.000s / 720×1280 / h264+aac** MP4, **0 credits** (1674→1674). Protocol in memory `[[flow-add-clip-scene-protocol]]` §CONCAT.

## 2. Reverse-engineered pipeline (ground truth)

All on `aisandbox-pa.googleapis.com`, Bearer + `text/plain`, credit-free:

1. `POST /v1/flow/projects/{pid}/scenes` `{workflowIds:[…]}` → scene + per-clip cloned media. Each clip's `primaryMediaId` is in the response (already parsed into `SceneWorkflow.media_id`).
2. `POST /v1:runVideoFxConcatenation` (top-level `/v1:` method, NOT `/v1/flow/…`):
   `{"inputVideos":[{"mediaGenerationId":<clip.media_id>,"length":<ns>,"startTimeOffset":"<s>","endTimeOffset":"<s>"} …]}`
   → `{"operation":{"operation":{"name":"projects/{n}/locations/{region}/jobs/{uuid}"}}}` (region varies).
3. `POST /v1:runVideoFxCheckConcatenationStatus` `{"operation":{"operation":{"name":…}}}` (echo op) → poll ~3s:
   `{"status","outputUri","mediaGenerationId","inputsCount","encodedVideo"}`. `ACTIVE` → `SUCCESSFUL` (also handle `FAILED`/`MEDIA_GENERATION_STATUS_UNSPECIFIED`).
4. On `SUCCESSFUL`: **combined MP4 is inline base64 in `encodedVideo`** (~20MB b64 → ~15MB MP4 for 16s). `outputUri`/`mediaGenerationId` stay EMPTY — decode `encodedVideo` directly; do not wait for a media id.

Read-back fix: `GET /v1/flow/scene/{id}/workflows?sceneId={id}` — the `?sceneId=` param is REQUIRED (without it → `{}`; that was the L1 bug).

## 3. Changes

### 3.1 `api/routes.py`
- `CONCATENATION_URL = ".../v1:runVideoFxConcatenation"`, `CONCATENATION_STATUS_URL = ".../v1:runVideoFxCheckConcatenationStatus"` (constants — no path params).
- `scene_workflows_url(scene_id)` → append `?sceneId={scene_id}` (URL-encode; keep `_SCENE_ID_RE` validation).

### 3.2 `api/scene.py`
- `ConcatInput` frozen dataclass: `media_id, length_seconds, start_offset, end_offset` + `to_wire()` (length→ns string via `int(seconds*1e9)`, offsets via existing `_seconds_to_duration`).
- `Scene.to_concat_inputs() -> tuple[ConcatInput, …]`: maps each `SceneWorkflow` (sorted by position) → `ConcatInput(media_id, total_duration, start_time, end_time)`. Raise `ValueError` if any `media_id` is None.

### 3.3 `api/client.py`
- `concatenate_scene(self, inputs: list[ConcatInput], *, poll_interval=3.0, timeout_s=180) -> bytes`:
  POST concat → poll status until `SUCCESSFUL` (return `base64.b64decode(encodedVideo)`), `FAILED` (raise `SceneConcatError`), or timeout (raise). Uses `_post_json` (Bearer). Reuses op dict verbatim in poll body.
- `get_scene_workflows` — pass the `?sceneId=` URL (via routes change). No signature change.

### 3.4 `errors.py`
- `SceneConcatError(FlowApiError)` — concat job failed/timed out. Map to an exit code (reuse a generic transport/API exit; NOT a new exit unless council insists).

### 3.5 `cli_scene.py`
- **DECISION FOR COUNCIL:** add `--output PATH` to `scene create`. If given → after compose, run `concatenate_scene` and write the extended MP4 to PATH (+ correct extension). If absent → current behavior (compose metadata + render from create response, NOT the dead GET).
  - Alternative considered: a separate `gflow scene export --scene <id> --output`. Rejected for v1: export needs the per-clip `media_id`s, which only the create response reliably gives (read-back works with `?sceneId=` but the source-of-truth for a fresh compose is create). Revisit if council prefers.
- Fix `_apply_trims` to return the scene from the **create/update response**, never the dead GET (the L1 render-empty bug).
- `_render`: print scene + clips + "Wrote extended video: <path> (<dur>s)" when `--output`.

### 3.6 `data/recorder.py` / `models.py`
- Add nullable `output_path` + `duration_seconds` to the `scenes` record when `--output` used (additive; reuse migration 0003 columns if present, else a tiny migration `0004`). Non-blocking (existing recorder pattern). **Council: confirm whether to persist or keep v1 stateless.**

## 4. Tests

- **Unit (`tests/api`, `tests/cli`, `tests/data`):**
  - `routes`: concat URLs constant; `scene_workflows_url` includes `?sceneId=`.
  - `scene`: `to_concat_inputs` mapping (ns conversion, offsets, position sort, None media_id → ValueError).
  - `client` (mocked `_post_json`): concat happy path decodes `encodedVideo`; `FAILED`→`SceneConcatError`; timeout; status state machine (ACTIVE→SUCCESSFUL). Feed a tiny known base64 → assert exact bytes.
  - `cli`: `scene create --output` writes a file (mock client returns fixed bytes); usage errors (bad clipRef → exit 2, already fixed).
- **Live `e2e_scene` (opt-in, credit-free):** rewrite `test_scene_compose_live.py` to run the FULL pipeline (create → concat → decode), assert: output exists, magic bytes MP4, **duration == sum(trimmed clip lengths)** (ffprobe optional; else size>source), and **credits unchanged + zero `batchAsyncGenerate`**. This is the completion gate.
- Add a redacted HAR fixture `samples/captured/16_concat.json` + `17_concat_status.json` (base64 truncated/synthetic) for parser tests.

## 5. Risks / edge cases (for council)

1. **Large inline base64** — `_post_json` must return a ~20MB+ `text/plain` body intact (no truncation/streaming issue). Longer scenes scale linearly; note a soft cap / memory warning. Verify transport handles it (live probe already round-tripped 20MB OK).
2. **`inputsCount: 3` anomaly** — status reported 3 for 2 inputs; cosmetic? Investigate during impl; don't gate on it.
3. **Polling**: region in job name varies (us-east1/us-west1) — opaque, just echo the op dict. Need FAILED + timeout handling; pick sane defaults (3s/180s) configurable.
4. **Credit-free invariant** — must assert credits before==after AND no `batchAsyncGenerate*`. (Confirmed free in probe, but lock it in tests.)
5. **No ffmpeg** — decode is pure base64; ffprobe only in tests (skip assert if absent).
6. **Trims** — `length` = clip totalDuration in ns; offsets from `sceneWorkflowMetadata`. Validate offsets ≤ totalDuration (existing `_validate_trim`).
7. **Auth/transport** — reuses proven Bearer `_post_json`; concat is a new `/v1:` path on same host — confirm no host-routing special-case needed.

## 6. Out of scope (backlog)
- ffmpeg / local stitch (NOT needed — Flow concatenates server-side).
- Add-clip-to-EXISTING-scene (still no faithful wire path — `[[flow-add-clip-scene-protocol]]`).
- Extend/interpolation (`veo_3_1_interpolation_lite`, credit-spending).
- `scene show` read-back as a standalone verb (the `?sceneId=` fix makes GET work, but keep `show` minimal / behind the create flow for v1).

## 7. Definition of done (user-mandated)
Plan → **LLM council review** → implement → code review → fix lint + Sonar → **green live `e2e_scene`** (real 16s output, credit-free). **Only after the e2e is green is this complete.**

## 8. Council verdict (`/gflow:predict`, 2026-05-31) — CAUTION 7/10 → LOCKED mitigations

5 personas (Arch 8 GO · Sec 8 CAUTION · Perf 8 GO · CLI 8 CAUTION · Devil 7 CAUTION). No STOP. Mitigations now MANDATORY for EXECUTE:

1. **`concatenate_scene(inputs, *, out_path) -> Path`** — decode `encodedVideo` + write internally, reusing `download_video`'s write path (extension + cloud-storage `storage_uri`). NEVER return raw `bytes` to the CLI (Arch + Sec + CLI all converge here).
2. **Never log/raise the parsed status dict or `encodedVideo`.** Decode then `del`; build any error detail from `status`/`outputUri` only. `show_locals=False` already set (observability.py) — add a test asserting no 20MB body in captured logs.
3. **Soft size cap** before `b64decode` (e.g. reject > ~250MB decoded) with a clear error, not silent OOM.
4. **`--output` path hardening:** `.expanduser().resolve()` + parent-dir check; MP4 magic-byte (`ftyp` at offset 4) extension via a `correct_video_extension` (paths.py sniffer currently knows only JPEG/PNG/GIF/WebP); **overwrite guard** (`--force`, default refuse + remediation hint).
5. **Exit code:** add an explicit `EXIT_CODE_MAP` entry for `SceneConcatError` (timeout → reuse 9/`TransportTimeoutError`; failed → generic API code). Respect the ordering-invariant test ([[exit-code-map-ordering-invariant-test-pitfall]]).
6. **Poll loop:** per-iteration `_post_json`, `asyncio.sleep(interval)` **OUTSIDE** any checked-out Page (no nested Page checkout — `[[playwright-context-request-no-page-deadlock]]`); `time.monotonic()` deadline; `poll_interval=3.0`, `timeout_s=180` configurable.
7. **Progress UX:** rich spinner/status during the poll (suppressed when not a TTY) + structlog `scene.concat_started`/`_completed`/`_failed` (so e2e can assert the path executed).
8. **Sequencing (DONE):** `?sceneId=` read-back fix shipped as separate **PR #136**; extra probes run (below) BEFORE concat impl.

## 9. Probe findings (live, denon82, 2026-05-31) — de-risk complete

- `inputsCount` is **N+1** (2→3, 3→4, 5→6) — server quirk, **benign**: output durations are EXACT (3 clips→24.000s, 5 clips→40.000s). Do not gate on it.
- **Size scales linearly:** ~0.95 MB/s decoded, ~1.27 MB/s base64 (16s=15.4MB, 24s=22.8MB, 40s=38.0MB). → mitigation #3 soft cap (~250MB ≈ 4-min ceiling).
- **Credit-free CONFIRMED:** 2-clip and 5-clip runs = exactly 0 credit delta; the one −10 reading was concurrent user browser activity (user confirmed). create_scene + concat scale with clip count yet cost nothing.
- Status terminal values seen: `MEDIA_GENERATION_STATUS_ACTIVE` → `…_SUCCESSFUL` (handle `…_FAILED`/unspecified defensively).
