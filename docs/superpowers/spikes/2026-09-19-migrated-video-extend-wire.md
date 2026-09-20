# Migrated-host video extend via batchexecute fZytfe

**Date:** 2026-09-19 · **Issues:** [#639](https://github.com/ffroliva/gflow-cli/issues/639) · **Cost:** credit-spending submit verified live
**Probe:** `scripts/dev/spike_migrated_video_extend.py` (live inspection via Playwright)
**Live proof:** `extend_live_proof_2026-09-19.json` · `wire_extend.mp4` (2 786 225 B, h264 720×1280, 7s)

## Question

Can `gflow video extend` be driven on the migrated host (`flow.google.com`) where the legacy aisandbox REST route `batchAsyncGenerateVideoExtendVideo` returns HTTP 401?

## What was observed

Flow's migrated SPA sends batchexecute RPC `fZytfe` when extending a clip from the scene editor timeline (`+` -> "Extend (Veo 3.1 - Lite)").

### 1. Wire format

POST `https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=fZytfe&source-path=/project/<pid>/scene/<scene_id>`

Envelope payload: `f.req=[[["fZytfe","<inner_json>",null,"generic"]]]&at=<wiz at>`

Inner positional payload structure:
- `inner[0]` = `[[[null, source_workflow_id, start_frame, end_frame], [null, null, [[[prompt]]]], model_key, aspect_idx, null, [scene_id, null, null, null, uuid1, uuid2]]]`
  - **Critical invariant:** the first media reference slot MUST carry the source clip's **workflow id**, NOT its media id. Passing a media id there is accepted by the server but never scheduled (echo record, no job, credits burned).
  - `aspect_idx`: 1 = 9:16 (portrait), 2 = 16:9 (landscape).
- `inner[1]` = `[null, 22, null, null, null, project_id, null, null, null, null, [recaptcha_token, 1]]` (tool enum 22 = PINHOLE).
- `inner[2]` = `[uuid3, 2, null, [scene_id, 2]]` (operation envelope).

### 2. Frame window calculation

The migrated SPA seeds the continuation from the **last second** of the source clip at 24 fps:
- Formula: `start_frame = duration * 24 - 24 + 1`, `end_frame = duration * 24`.
- For a 4s clip: `[73, 96]`.
- For an 8s clip: `[169, 192]`.
- Duration is derived from the source clip's model key in the project listing (e.g. `abra_t2v_8s` -> 8.0s).

### 3. Status polling and signed URL extraction

- **`as29s` polling:** `as29s` with body `["<workflow_id>"]` returns a bare string `"<workflow_id>"` while running. Once terminal, it returns the full generation record carrying the signed video URL in `record[7][0][8]`.
- **`jwpduf` polling:** `jwpduf` returns `details[8] = [3]` on success and byte size in `details[13]`, but no video URL.
- The poller listens on `as29s`, extracting the signed `https://flow-content.google/video/<workflow_id>?Expires=...` URL on completion.

### 4. Verification

Live verification was performed on 2026-09-19:
- Source clip extended with prompt `"continue the camera movement smoothly"`.
- Job scheduled workflow `2bb68ea1`, reached status `DONE`.
- Resulting MP4 downloaded (2 786 225 B, h264 720×1280, 7s).
- Boundary frame comparison (`extend_boundary_check.png`) confirmed pixel-continuous camera movement matching the manual UI extend.
