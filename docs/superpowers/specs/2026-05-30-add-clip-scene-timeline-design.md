# Add Clip / Scenes Timeline — Design Spec

> Status: **approved (design), hardened by /gflow:predict (CAUTION 5/10) + /gflow:scenario** · Date: 2026-05-30 · Author: brainstorming session
> Source evidence: `labs.google13.har` (Extend/interpolation), `labs.google15.har` (full Add Clip + crop), `labs.google16.har` (video upload)
>
> ⚠️ **Read §8 first.** The naive "all REST via `page.request`" premise is **wrong for `aisandbox-pa`**: those POSTs 401 without an `Authorization: Bearer ya29` token (Issue #15). **SAPISIDHASH mentions in the body below are a disproven hypothesis — the live-verified mechanism is the Bearer token; see §8.** The work is gated on an **L0** auth fix — see §6 (roadmap) and §8 (analysis).

## 1. Goal

Bring Google Flow's **"Add Clip"** (compose multiple video clips into an ordered, trimmable
**Scene**) to gflow-cli as a faithful **API-parity** feature. gflow replicates Flow's
timeline-metadata calls exactly; it does **not** render a single stitched MP4. The objective is
to *replicate Google Flow*, not to reimplement video editing (local ffmpeg concat is trivially
done elsewhere and is explicitly out of scope).

## 2. What "Add Clip" actually is (from the wire)

The `+` → *Add Clip* menu composes a **Scene** (`sceneId`) — Flow's first-class noun, its own
nav tab. A scene holds an ordered list of `sceneWorkflows` (clips). Two API surfaces are involved:

**BFF** `labs.google/fx/api/trpc/…` (session/cookie auth, `application/json`):
- `project.createProject` → `{json:{projectTitle, toolName:"PINHOLE"}}` → `projectId`
- `videoFx.updateVideoOffset` → `{json:{mediaId,startOffset,endOffset}}` — **secondary/redundant** (stayed constant `0–8s` while the real trim moved). Skipped in v1; parity-optional backlog.
- `media.getMediaUrlRedirect?name={mediaId}` → signed CDN URL (download/read-back only)

**aisandbox** `aisandbox-pa.googleapis.com/v1/flow/…` (`content-type: text/plain`, **no recaptcha, no credits**, but **requires `Authorization: SAPISIDHASH` — see §8/L0; `page.request` 401s without it**):
- `PATCH /v1/flowWorkflows/{workflowId}` — **commit**: `{workflow:{name,projectId,metadata:{primaryMediaId}},updateMask:"metadata.primaryMediaId"}`. Required before a workflow is placeable.
- `POST /v1/flow/projects/{projectId}/scenes` → `{workflowIds:[...]}` (ordered; **repeat an id to duplicate a clip**) → `scene{sceneId, sceneWorkflows[]}`
- `POST /v1/flow/scene/sceneWorkflows:update` → `{sceneId,projectId,sceneWorkflows:[{sceneId,workflow:{name:instanceId},sceneWorkflowMetadata:{startTime,endTime,position,totalDuration}}]}`
- `GET /v1/flow/scene/{sceneId}/workflows` → `{sceneWorkflows[], media[]}` (read-back)

### Lifecycle
`createProject` → generate clip (`batchAsyncGenerateVideo*`, **the only credit/recaptcha step**) →
`PATCH flowWorkflows` commit `primaryMediaId` → `POST /scenes` (ordered `workflowIds`) →
`sceneWorkflows:update` (order + trim) → `GET scene/{id}/workflows`.

### Load-bearing semantics
1. **Clips are CLONED on add.** Listing the same source `workflowId` twice mints two fresh
   scene-instance workflow ids, each with its own copied `primaryMediaId`. "Add" = append a
   `workflowId` to the ordered list.
2. **Trim lives in `sceneWorkflowMetadata.startTime/endTime`** — in/out points within the clip's
   own `0–totalDuration` (source = 8s). Visible length = `end − start`. `position` = order index.
   `updateVideoOffset` is NOT the trim.
3. **No server-side render/stitch/export endpoint exists.** "Done" calls nothing; the scene is
   finalized as a multi-clip library entry, played client-side as sequential clips. A single MP4
   would require local concat — out of scope.

## 3. CLI surface

A new **`gflow scene`** command group (NOT a `video` subcommand — see §7 rationale).

```
gflow scene create  --project <pid> <clipRef> [<clipRef> ...]   # compose from scratch
gflow scene add-clip --scene <sid> <clipRef>[:<start>-<end>]    # append to an existing scene
gflow scene show     --scene <sid>                              # read-back (order + trims)
```

- `<clipRef>` = a source `workflowId` (v1). Optional trim suffix `:<start>-<end>` in **seconds**
  (e.g. `:3.2-5.2`). No suffix = full clip.
- Duplicates allowed (faithful): pass the same `workflowId` twice.
- Optional thin `gflow video add-clip` alias forwarding to `scene add-clip` (discoverability).
- Output: `sceneId`, ordered clip list with resolved trims, composed duration.

> Open ergonomics question deferred to planning: whether `<clipRef>` should *also* accept a gflow
> data-layer row id / prior-run `mediaId` and resolve it to a `workflowId`. v1 = raw `workflowId`.

## 4. Architecture

All additive; mirrors existing `client.py` patterns (`_post_json`, `_patch_json`, `_get`,
`create_project`, `upload_image`).

> **Transport binding & auth split (load-bearing).** REST helpers call `page.request.post/patch`
> on a checked-out *authenticated browser Page* — so calls ride the logged-in Flow session
> (cookies). BUT: **`labs.google` BFF** calls authenticate on cookies alone (`create_project`
> proves this); **`aisandbox-pa` POSTs return 401** without an `Authorization: SAPISIDHASH`
> header `_post_json` does not yet send (Issue #15; `ui_automation_video.py:9`). `archive_workflow`
> is *defined but never called* — unproven, do **not** treat it as proof the pattern works on
> aisandbox. Therefore `commit_workflow`/`create_scene`/`update_scene_workflows` (all aisandbox)
> are **gated on L0** (§6). Video upload (L2) is on the BFF → likely works without SAPISIDHASH.

**New module `api/scene.py`** — domain models (frozen dataclasses):
- `SceneWorkflowMetadata(position: int, start_time: float, end_time: float, total_duration: float)`
  with a `to_wire()` formatting seconds as `"3.226666870s"` / `"8s"`.
- `SceneWorkflow(workflow_id: str, metadata: SceneWorkflowMetadata)`
- `Scene(scene_id: str, project_id: str, workflows: list[SceneWorkflow])` with
  `from_create_response()` / `from_get_response()`.

**New `client.py` methods** (no recaptcha, no credits):
- `commit_workflow(workflow_id, project_id, primary_media_id)` → PATCH (same shape as `archive_workflow`, different `updateMask`; **needs L0 SAPISIDHASH**)
- `create_scene(project_id, workflow_ids)` → POST `/scenes` → `Scene`
- `update_scene_workflows(scene_id, project_id, workflows)` → POST `sceneWorkflows:update`
- `get_scene_workflows(scene_id)` → GET → `Scene`

**`routes.py`** — add `SCENES` (`/v1/flow/projects/{}/scenes`), `SCENE_WORKFLOWS_UPDATE`
(`/v1/flow/scene/sceneWorkflows:update`), `scene_workflows_url(scene_id)`.

**CLI** — new `cli_scene.py`, registered as a group in `cli.py`.

### Data flow
`cli_scene` parses clipRefs → resolves trims → `client.commit_workflow` (idempotent; skip if
already committed) → `client.create_scene` → `client.update_scene_workflows` → render result from
`client.get_scene_workflows`.

### Error handling
- Reuse existing transport error taxonomy. Scene ops are metadata, pre-Flow-credit — surface
  failures as ordinary errors (no exit-code-16 data-store coupling).
- Validate clipRef trim ranges (`0 ≤ start < end ≤ totalDuration`) before any network call.
- A missing/uncommitted source workflow → clear actionable error.

## 5. Testing (credit-free)

The whole flow is exercisable against the **real API for zero credits** (scene ops carry no
recaptcha and spend nothing — only `batchAsyncGenerate*` costs).

- **E2E (cost-free):** `create_project` → reuse an existing generated clip's `workflowId` (or a
  library-uploaded video) → `create_scene([wf, wf])` → `update_scene_workflows` with trims →
  `get_scene_workflows` asserts order + `startTime/endTime`. **Assert the invariant that zero
  `batchAsyncGenerate*` calls fired.** Add a cost-free `e2e_scene` (or `smoke`) marker tier.
- **Unit:** payload construction for each of the 4 client methods (mocked transport); trim
  second→`"…s"` formatting; duplicate-workflow ordering; trim-range validation.

> Research item for planning: confirm whether Flow exposes a **library video-upload** endpoint
> (HAR only showed `uploadImage`). If absent, the e2e reuses an existing `workflowId` — equally
> credit-free.

## 6. Scope — layered roadmap (re-sequenced after §8 analyses)

- **L0 — GATING: wire SAPISIDHASH into `_post_json`/`_patch_json` for `aisandbox-pa` routes
  (Issue #15).** Without it, every scene/commit call 401s. Scaffolding exists
  (`api/transports/experimental/sapisidhash.py`, currently unwired). Complete Issue #15's 3
  investigation gates first. This is the real first ship — it also fixes the standing i2v
  upload 401. **Not optional.**
- **L1 — `gflow scene` compose** (this spec §3–§5): create scene, ordering, per-clip trim,
  commit, read-back. Credit-free once L0 lands.
- **L2 — `gflow video upload`** (BFF `upload-video`, §5 research item resolved): 2-phase resumable
  upload → `workflowServerId` directly placeable in a scene. Lower-risk than L1 (BFF auth proven).
  Makes L1's e2e self-contained.
- **L3 — backlog:** Extend (`batchAsyncGenerateVideoStartAndEndImage` + `veo_3_1_interpolation_lite`);
  `updateVideoOffset` parity; scene edit/remove-clip/reorder; local ffmpeg stitch.

### Cross-cutting must-haves (from §8 scenario — apply across L1/L2)
- **Persistence = additive migration `0002`** (scene/timeline tables; current schema has no scene
  tables) + a **non-blocking recorder** (a `DataStoreError` must never abort a free scene op —
  warn + return 0). Store `projectId/sceneId/workflowId/mediaId/dimensions/trims/prompt/source`
  for replay/retrieval (resolve bytes via `getMediaUrlRedirect?name={mediaId}`).
- **Resource scope, explicit:** scenes + uploads are **project-bound** (`projectId` in path /
  `x-upload-project-id`); media is **globally addressable** by `mediaId` (signed CDN URL). Surface
  the distinction to the user; don't leave it implicit.
- **Secret hygiene:** never log SAPISIDHASH / SAPISID / `Authorization`; `show_locals=False` on new
  error renderers; redact in `WireFormatError` discovery payloads.
- **SSRF allowlist** on replay/download host (`flow-content.google` / `*.googleapis.com`).
- **Validate before network:** trim ranges (`0 ≤ start < end ≤ totalDuration`); upload file
  magic-bytes + size cap (mirror `upload_image`). Map `aisandbox` 401 to a **distinct** error in
  `EXIT_CODE_MAP` (not bare `AuthExpiredError`).

## 7. Decision rationale — why `gflow scene`, not `video add-clip`

1. The wire's **aggregate root is the scene** (`sceneId`); a video/`workflowId` is a child it
   references. You cannot add-clip without a scene, and the API returns a `sceneId` needed for
   every subsequent op.
2. `add-clip` is **one verb in a multi-verb lifecycle** (create→add→trim→reorder→read-back);
   backlog ops are all scene-scoped. Under `video` they'd clutter the generation group.
3. **Flow models Scenes as a first-class nav tab**; gflow's convention maps one group per Flow tab.
4. Hiding it under `video` still forces surfacing `sceneId` anyway → the scene noun is unavoidable.

The ergonomic "add-clip" verb is preserved as a subcommand of `scene` (and optional `video`
alias) — correct noun, intuitive verb.

## 8. Pre-implementation analyses (2026-05-30)

**`/gflow:predict` → CAUTION (5/10).** Architecture fits the modular monolith and credit-free is
real, but the proposal's "all REST via `page.request`, works like `archive_workflow`" premise is
**false for `aisandbox-pa`** (401 / missing SAPISIDHASH — Issue #15, `ui_automation_video.py:9`,
unwired `archive_workflow`). L1 sits entirely on aisandbox → gated on L0. Counter-intuitively L2
(BFF) is lower-risk. Required mitigations: L0 SAPISIDHASH first; credit-free auth spike
(`create_scene` → 200); additive migration + non-blocking recorder; upload validation + distinct
401 error.

**`/gflow:scenario` → must-cover (Critical/High):** aisandbox 401 path + distinct error mapping;
secret redaction; `0002` migration + newer-schema `DataStoreError` guard; recorder never aborts a
free op; `WireFormatError` (not crash) on missing `sceneId`/`workflowServerId`; SSRF allowlist on
replay; trim-range + upload file-type/size validation. Skipped dimension worth noting: **D3
selector drift is N/A — this is REST-only, no DOM** (a durability win).

**L0 outcome (2026-05-31):** the live smoke disproved the SAPISIDHASH hypothesis — `aisandbox-pa`
authenticates with `Authorization: Bearer ya29.<oauth>` fetched from `GET /fx/api/auth/session`.
L0 pivoted to the Bearer mechanism (`docs/superpowers/plans/2026-05-31-l0-bearer-pivot.md`) and is
**live-verified credit-free** (REST `uploadImage` → 200). The SAPISIDHASH infra (header injection,
401 refresh-retry, `AisandboxAuthError`, deadlock-safe `BrowserContext` read) was reused intact.
