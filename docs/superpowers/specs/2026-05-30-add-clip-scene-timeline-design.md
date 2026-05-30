# Add Clip / Scenes Timeline — Design Spec

> Status: **approved (design)** · Date: 2026-05-30 · Author: brainstorming session
> Source evidence: `C:\Users\ffrol\Downloads\labs.google13.har` (Extend/interpolation), `labs.google15.har` (full Add Clip + crop)

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

**aisandbox** `aisandbox-pa.googleapis.com/v1/flow/…` (Bearer, `content-type: text/plain`, **no recaptcha, no credits**):
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
`create_project`, `upload_image`, `archive_workflow`).

**New module `api/scene.py`** — domain models (frozen dataclasses):
- `SceneWorkflowMetadata(position: int, start_time: float, end_time: float, total_duration: float)`
  with a `to_wire()` formatting seconds as `"3.226666870s"` / `"8s"`.
- `SceneWorkflow(workflow_id: str, metadata: SceneWorkflowMetadata)`
- `Scene(scene_id: str, project_id: str, workflows: list[SceneWorkflow])` with
  `from_create_response()` / `from_get_response()`.

**New `client.py` methods** (no recaptcha, no credits):
- `commit_workflow(workflow_id, project_id, primary_media_id)` → PATCH (twin of `archive_workflow`)
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

## 6. Scope

**In:** create scene; ordering; per-clip trim; commit step; read-back; `gflow scene` group.

**Backlog (out):** Extend (interpolation generation, `batchAsyncGenerateVideoStartAndEndImage` +
`veo_3_1_interpolation_lite`, start+end keyframes); `updateVideoOffset` parity; scene
edit/remove-clip/reorder; local ffmpeg stitch; library video upload (unless needed for e2e).

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
