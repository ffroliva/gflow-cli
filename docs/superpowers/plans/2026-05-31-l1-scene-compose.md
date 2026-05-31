# L1 `gflow scene` Compose — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Google Flow's "Add Clip" as a new `gflow scene` command group (`create` / `add-clip` / `show`) that composes ordered, trimmable video clips into a Scene via the credit-free aisandbox-pa REST surface, with local persistence.

**Architecture:** Additive on the existing `FlowApiClient` REST helpers. L0 already made aisandbox **POST/PATCH** auth transparent (`_post_json`/`_patch_json` auto-inject `Authorization: Bearer ya29` + 401-refresh-retry via `_run_with_aisandbox_retry`). This plan adds the one missing piece — an aisandbox-aware **GET** helper (`_get_json`) — plus 4 client methods, frozen-dataclass domain models in `api/scene.py`, a new `gflow scene` CLI group, and a persistence layer (migration `0003` + two tables + recorder method). Scene ops carry **no reCAPTCHA and spend no credits**, so the whole flow is e2e-testable for free. This is class ① of the REST-path capability matrix.

**Tech Stack:** Python 3.12, Click, Playwright (`page.request`), httpx (none new), SQLite (stdlib `sqlite3`), pytest (`asyncio_mode=auto`), structlog, Rich.

**Source of truth for wire shapes:** `~/Downloads/labs.google15.har` (full Add Clip + crop capture). Task 0 extracts the exact request/response JSON into `samples/captured/` so every parser is written against ground truth, never a guess. Protocol summary lives in the `flow-add-clip-scene-protocol` memory and spec `docs/superpowers/specs/2026-05-30-add-clip-scene-timeline-design.md`.

**Cross-cutting invariants (apply throughout):**
- **Never log** `Authorization` / `Bearer` / tokens (L0 already redacts; new code must not regress this).
- Scene ops are **pre-credit metadata** — persistence failures must **warn + continue**, never abort (mirror `_warn_persistence_failed_after_success` in `cli_image.py`). A `DataStoreError` from the recorder is caught at the call site.
- **Validate trim ranges before any network call**: `0 ≤ start < end ≤ total_duration`.
- aisandbox 401 already maps to a **distinct** error: `AisandboxAuthError` (exit 3) exists from L0 — no `EXIT_CODE_MAP` change needed.
- **Migration number is `0003`** — `0002_add_cloud_storage.sql` already exists. (The spec's "0002" is stale.)

---

## File Structure

**New files:**
- `samples/captured/12_create_scene.json`, `13_sceneWorkflows_update.json`, `14_get_scene_workflows.json`, `15_commit_flowWorkflow.json` — ground-truth wire captures (Task 0).
- `src/gflow_cli/api/scene.py` — domain models (`SceneWorkflowMetadata`, `SceneWorkflow`, `Scene`) + parsers.
- `src/gflow_cli/data/migrations/0003_add_scene_tables.sql` — `scenes` + `scene_clips` tables.
- `src/gflow_cli/cli_scene.py` — `gflow scene` group (`create` / `add-clip` / `show`).
- Tests: `tests/api/test_scene_models.py`, `tests/api/test_routes_scene.py`, `tests/api/test_client_scene.py`, `tests/data/test_scene_persistence.py`, `tests/cli/test_cli_scene.py`, `tests/e2e/test_scene_compose_live.py`.

**Modified files:**
- `src/gflow_cli/api/routes.py` — `SCENES`, `SCENE_WORKFLOWS_UPDATE`, `scene_workflows_url()`, `flow_workflow_url()`.
- `src/gflow_cli/api/client.py` — `_get_json()` helper + `commit_workflow`/`create_scene`/`update_scene_workflows`/`get_scene_workflows`.
- `src/gflow_cli/data/models.py` — `SceneRecord`, `SceneClipRecord`, `OperationKind.SCENE_CREATE`/`SCENE_ADD_CLIP`, `AssetKind` unchanged.
- `src/gflow_cli/data/repository.py` — `upsert_scene`, `replace_scene_clips`, `get_scene_by_flow_scene_id`, `get_scene_clips`.
- `src/gflow_cli/data/recorder.py` — `record_scene()`.
- `src/gflow_cli/cli.py` — register the `scene` group.
- `pyproject.toml` — add `e2e_scene` marker.
- `tests/test_marker_registry.py` — add `e2e_scene` to the required-sub-marker set.

---

## Task 0: Capture ground-truth scene wire shapes from the HAR

**Files:**
- Create: `samples/captured/12_create_scene.json`, `13_sceneWorkflows_update.json`, `14_get_scene_workflows.json`, `15_commit_flowWorkflow.json`

- [ ] **Step 1: Extract the four scene exchanges from the HAR**

Run this in the context-mode sandbox (the HAR is 80 MB — do NOT read it into the chat). It pulls request+response bodies for each scene endpoint and writes trimmed JSON fixtures.

```python
# ctx_execute language=python
import json, pathlib
har = json.loads(pathlib.Path.home().joinpath("Downloads","labs.google15.har").read_text(encoding="utf-8"))
out = pathlib.Path("samples/captured")
def dump(match, fname, want_request=True):
    for e in har["log"]["entries"]:
        url = e["request"]["url"]
        if match(url, e["request"]["method"]):
            rec = {
                "url": url,
                "method": e["request"]["method"],
                "request_body": (e["request"].get("postData") or {}).get("text"),
                "response_body": (e["response"].get("content") or {}).get("text"),
            }
            out.joinpath(fname).write_text(json.dumps(rec, indent=2), encoding="utf-8")
            return True
    return False
print("create_scene", dump(lambda u,m: u.endswith("/scenes") and m=="POST", "12_create_scene.json"))
print("update", dump(lambda u,m: "sceneWorkflows:update" in u and m=="POST", "13_sceneWorkflows_update.json"))
print("get", dump(lambda u,m: "/scene/" in u and u.endswith("/workflows") and m=="GET", "14_get_scene_workflows.json"))
print("commit", dump(lambda u,m: "/flowWorkflows/" in u and m=="PATCH", "15_commit_flowWorkflow.json"))
```

- [ ] **Step 2: Confirm all four printed `True`**, then inspect each file's `response_body` to record the exact field paths (`sceneId`, the per-clip instance id location, `sceneWorkflowMetadata` keys, `media[]` shape). These paths are referenced by Tasks 2 and 7. If any printed `False`, widen the matcher (the path segment may differ) and re-run.

- [ ] **Step 3: Redact before committing.** Confirm no `Authorization`, cookie, or signed-URL token leaked into the fixtures:

```bash
grep -iE 'bearer|authorization|sapisid|ya29|Signature=|__Secure' samples/captured/12_create_scene.json samples/captured/13_sceneWorkflows_update.json samples/captured/14_get_scene_workflows.json samples/captured/15_commit_flowWorkflow.json || echo "CLEAN"
```
Expected: `CLEAN`. If anything matches, hand-redact the value to `<redacted>` before committing.

- [ ] **Step 4: Commit**

```bash
git add samples/captured/12_create_scene.json samples/captured/13_sceneWorkflows_update.json samples/captured/14_get_scene_workflows.json samples/captured/15_commit_flowWorkflow.json
git commit -m "test(scene): capture ground-truth Add Clip wire shapes from labs.google15.har"
```

---

## Task 1: Scene routes

**Files:**
- Modify: `src/gflow_cli/api/routes.py`
- Test: `tests/api/test_routes_scene.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_routes_scene.py
import pytest
from gflow_cli.api import routes

def test_scenes_url_interpolates_validated_project_id():
    url = routes.scenes_url("proj-123")
    assert url == "https://aisandbox-pa.googleapis.com/v1/flow/projects/proj-123/scenes"

def test_scenes_url_rejects_injection():
    with pytest.raises(ValueError):
        routes.scenes_url("../evil")

def test_scene_workflows_url():
    url = routes.scene_workflows_url("scene-abc")
    assert url == "https://aisandbox-pa.googleapis.com/v1/flow/scene/scene-abc/workflows"

def test_scene_workflows_update_url_is_constant():
    assert routes.SCENE_WORKFLOWS_UPDATE.endswith("/v1/flow/scene/sceneWorkflows:update")

def test_flow_workflow_url():
    assert routes.flow_workflow_url("wf-1") == "https://aisandbox-pa.googleapis.com/v1/flowWorkflows/wf-1"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_routes_scene.py -q`
Expected: FAIL — `AttributeError: module 'gflow_cli.api.routes' has no attribute 'scenes_url'`.

- [ ] **Step 3: Implement in `routes.py`** (append after the existing `batch_generate_images_url`; reuse the existing `_PROJECT_ID_RE` guard and the same validation for the scene id path segment)

```python
# Scene / Add Clip (aisandbox-pa) ------------------------------------------
SCENE_WORKFLOWS_UPDATE = f"{FLOW_API_BASE}/flow/scene/sceneWorkflows:update"

# Reuse the project-id allowlist shape for scene ids (UUID-like, path-interpolated).
_SCENE_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,128}$")


def scenes_url(project_id: str) -> str:
    """POST target that composes a scene from ordered workflowIds."""
    if not _PROJECT_ID_RE.fullmatch(project_id):
        msg = f"Invalid project_id: {project_id!r}"
        raise ValueError(msg)
    return f"{FLOW_API_BASE}/flow/projects/{project_id}/scenes"


def scene_workflows_url(scene_id: str) -> str:
    """GET target for scene read-back (order + trims + media)."""
    if not _SCENE_ID_RE.fullmatch(scene_id):
        msg = f"Invalid scene_id: {scene_id!r}"
        raise ValueError(msg)
    return f"{FLOW_API_BASE}/flow/scene/{scene_id}/workflows"


def flow_workflow_url(workflow_id: str) -> str:
    """PATCH target to commit a workflow's primaryMediaId before placement."""
    if not _SCENE_ID_RE.fullmatch(workflow_id):
        msg = f"Invalid workflow_id: {workflow_id!r}"
        raise ValueError(msg)
    return f"{ARCHIVE_WORKFLOW_BASE}/{workflow_id}"
```

- [ ] **Step 4: Run to confirm pass**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_routes_scene.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/routes.py tests/api/test_routes_scene.py
git commit -m "feat(scene): add aisandbox scene route builders with id allowlist"
```

---

## Task 2: Scene domain models (`api/scene.py`)

**Files:**
- Create: `src/gflow_cli/api/scene.py`
- Test: `tests/api/test_scene_models.py`

> Wire shape reference (verify against `samples/captured/12`/`14` from Task 0 before finalizing the parsers): the `sceneWorkflows:update` body is `{sceneId, projectId, sceneWorkflows:[{sceneId, workflow:{name:<instanceId>}, sceneWorkflowMetadata:{startTime, endTime, position, totalDuration}}]}`. Times serialize as a protobuf-duration string: `"8s"`, `"3.226666870s"`.

- [ ] **Step 1: Write the failing test for `to_wire` formatting + round-trip**

```python
# tests/api/test_scene_models.py
import json, pathlib
from gflow_cli.api.scene import Scene, SceneWorkflow, SceneWorkflowMetadata

def test_duration_to_wire_whole_seconds():
    m = SceneWorkflowMetadata(position=0, start_time=0.0, end_time=8.0, total_duration=8.0)
    w = m.to_wire()
    assert w["startTime"] == "0s"
    assert w["endTime"] == "8s"
    assert w["position"] == 0
    assert w["totalDuration"] == "8s"

def test_duration_to_wire_fractional_keeps_nano_precision():
    m = SceneWorkflowMetadata(position=1, start_time=3.22666687, end_time=5.0, total_duration=8.0)
    assert m.to_wire()["startTime"] == "3.226666870s"

def test_scene_workflow_to_wire_nests_instance_id():
    sw = SceneWorkflow(
        workflow_id="inst-1",
        metadata=SceneWorkflowMetadata(position=0, start_time=0.0, end_time=8.0, total_duration=8.0),
    )
    wire = sw.to_wire(scene_id="scene-x")
    assert wire["sceneId"] == "scene-x"
    assert wire["workflow"]["name"] == "inst-1"
    assert wire["sceneWorkflowMetadata"]["endTime"] == "8s"

def test_scene_from_create_response_parses_sceneid_and_instances():
    raw = json.loads(pathlib.Path("samples/captured/12_create_scene.json").read_text())
    data = json.loads(raw["response_body"])
    scene = Scene.from_create_response(data, project_id="proj-1")
    assert scene.scene_id
    assert scene.project_id == "proj-1"
    assert len(scene.workflows) >= 1
    assert all(w.workflow_id for w in scene.workflows)

def test_scene_from_get_response_parses_order_and_trims():
    raw = json.loads(pathlib.Path("samples/captured/14_get_scene_workflows.json").read_text())
    data = json.loads(raw["response_body"])
    scene = Scene.from_get_response(data, scene_id="scene-x", project_id="proj-1")
    positions = [w.metadata.position for w in scene.workflows]
    assert positions == sorted(positions)
```

- [ ] **Step 2: Run to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_scene_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gflow_cli.api.scene'`.

- [ ] **Step 3: Implement `api/scene.py`** (frozen dataclasses; parsers raise `ValueError` on malformed input, matching `dto.py`. **Confirm the `from_*` field paths against the Task 0 fixtures and adjust the `["..."]` accessors to match the real JSON** — the accessors below encode the protocol's documented shape as the starting point.)

```python
"""Domain models for Flow Scenes (Add Clip). Frozen + immutable, like dto.py.

Wire times are protobuf-duration strings ("8s", "3.226666870s"). Trim lives in
sceneWorkflowMetadata.startTime/endTime (NOT updateVideoOffset). position = order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _seconds_to_duration(value: float) -> str:
    """Serialize seconds as a protobuf Duration string.

    Whole seconds -> "8s"; fractional -> 9 fractional digits "3.226666870s"
    (matches Flow's wire form). Trailing-zero nanos are preserved at 9 digits.
    """
    if value == int(value):
        return f"{int(value)}s"
    return f"{value:.9f}s"


def _duration_to_seconds(text: str) -> float:
    """Parse a protobuf Duration string ("8s", "3.2s") back to float seconds."""
    return float(text.rstrip("s")) if text else 0.0


@dataclass(frozen=True)
class SceneWorkflowMetadata:
    position: int
    start_time: float
    end_time: float
    total_duration: float

    def to_wire(self) -> dict[str, Any]:
        return {
            "startTime": _seconds_to_duration(self.start_time),
            "endTime": _seconds_to_duration(self.end_time),
            "position": self.position,
            "totalDuration": _seconds_to_duration(self.total_duration),
        }


@dataclass(frozen=True)
class SceneWorkflow:
    workflow_id: str  # the scene-INSTANCE id (clone), not the source clip id
    metadata: SceneWorkflowMetadata

    def to_wire(self, *, scene_id: str) -> dict[str, Any]:
        return {
            "sceneId": scene_id,
            "workflow": {"name": self.workflow_id},
            "sceneWorkflowMetadata": self.metadata.to_wire(),
        }


@dataclass(frozen=True)
class Scene:
    scene_id: str
    project_id: str
    workflows: tuple[SceneWorkflow, ...]

    @staticmethod
    def _parse_workflows(entries: list[dict[str, Any]]) -> tuple[SceneWorkflow, ...]:
        out: list[SceneWorkflow] = []
        for i, e in enumerate(entries):
            meta = e.get("sceneWorkflowMetadata", {}) or {}
            out.append(
                SceneWorkflow(
                    workflow_id=str(e.get("workflow", {}).get("name", "")),
                    metadata=SceneWorkflowMetadata(
                        position=int(meta.get("position", i)),
                        start_time=_duration_to_seconds(str(meta.get("startTime", "0s"))),
                        end_time=_duration_to_seconds(str(meta.get("endTime", "0s"))),
                        total_duration=_duration_to_seconds(str(meta.get("totalDuration", "0s"))),
                    ),
                )
            )
        out.sort(key=lambda w: w.metadata.position)
        return tuple(out)

    @classmethod
    def from_create_response(cls, data: dict[str, Any], *, project_id: str) -> Scene:
        try:
            scene = data["scene"] if "scene" in data else data
            return cls(
                scene_id=str(scene["sceneId"]),
                project_id=project_id,
                workflows=cls._parse_workflows(scene.get("sceneWorkflows", []) or []),
            )
        except (KeyError, TypeError) as e:
            msg = f"unexpected create-scene response shape: {e}"
            raise ValueError(msg) from e

    @classmethod
    def from_get_response(cls, data: dict[str, Any], *, scene_id: str, project_id: str) -> Scene:
        try:
            return cls(
                scene_id=scene_id,
                project_id=project_id,
                workflows=cls._parse_workflows(data.get("sceneWorkflows", []) or []),
            )
        except (KeyError, TypeError) as e:
            msg = f"unexpected get-scene response shape: {e}"
            raise ValueError(msg) from e
```

- [ ] **Step 4: Run to confirm pass**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_scene_models.py -q`
Expected: PASS (5 tests). If `from_*` tests fail on a `KeyError`, adjust the accessors to the real paths in the Task 0 fixtures.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/scene.py tests/api/test_scene_models.py
git commit -m "feat(scene): add Scene domain models with protobuf-duration wire formatting"
```

---

## Task 3: aisandbox-aware `_get_json` helper

**Files:**
- Modify: `src/gflow_cli/api/client.py` (add after `_patch_json`, around line 581)
- Test: `tests/api/test_client_scene.py`

> This is the one real auth gap: existing GETs (`download`) call raw `page.request.get` with no Bearer. `get_scene_workflows` needs the same Bearer + 401-retry treatment `_post_json`/`_patch_json` have. Mirror `_post_json`'s structure: build `attempt()`, gate on `_is_aisandbox_url`, run through `_run_with_aisandbox_retry`, classify non-retryable, JSON-decode.

- [ ] **Step 1: Write the failing test** (fake `page.request` so no browser is needed; mirror existing client unit tests' transport-faking approach)

```python
# tests/api/test_client_scene.py
import pytest
from gflow_cli.api.client import FlowApiClient

class _FakeResp:
    def __init__(self, status, text):
        self.status = status
        self._text = text
    async def text(self):
        return self._text

class _FakeRequest:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []
    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._resp
    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._resp
    async def patch(self, url, **kw):
        self.calls.append(("PATCH", url, kw))
        return self._resp

class _FakePage:
    def __init__(self, resp):
        self.request = _FakeRequest(resp)

def _client_with(page):
    c = FlowApiClient.__new__(FlowApiClient)
    c._page = page                       # injected mock Page (bypasses pool checkout)
    c._page_queue = None                 # checkout/checkin become no-ops
    c._context = None
    c._access_token = "ya29.test"        # pre-seed so no /auth/session fetch fires
    c._access_token_exp = 9_999_999_999
    return c

async def test_get_json_attaches_bearer_for_aisandbox():
    page = _FakePage(_FakeResp(200, '{"sceneWorkflows": []}'))
    c = _client_with(page)
    data = await c._get_json("https://aisandbox-pa.googleapis.com/v1/flow/scene/s1/workflows",
                             route_name="getSceneWorkflows")
    assert data == {"sceneWorkflows": []}
    _, _, kw = page.request.calls[-1]
    assert kw["headers"]["authorization"] == "Bearer ya29.test"
```

- [ ] **Step 2: Run to confirm it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_client_scene.py::test_get_json_attaches_bearer_for_aisandbox -q`
Expected: FAIL — `AttributeError: 'FlowApiClient' object has no attribute '_get_json'`.

- [ ] **Step 3: Implement `_get_json` in `client.py`**

```python
    async def _get_json(
        self,
        url: str,
        *,
        route_name: str | None = None,
    ) -> Any:
        """GET a JSON body with retry + aisandbox Bearer auth + typed errors.

        Mirrors ``_post_json`` for the read side: aisandbox-pa GETs require the
        Bearer token (raw ``page.request.get`` in ``download`` only works because
        signed CDN URLs need no auth). 401 → single token-refresh-retry.
        """
        logger.debug("get_json", url=url)
        route = route_name or url
        is_aisandbox = self._is_aisandbox_url(url)

        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                headers: dict[str, str] = {}
                if is_aisandbox:
                    headers.update(await self._aisandbox_auth_headers())
                return await page.request.get(url, headers=headers, timeout=30_000)
            finally:
                self._checkin_page(page)

        resp = await self._run_with_aisandbox_retry(attempt, route=route, is_aisandbox=is_aisandbox)
        text = await resp.text()
        _raise_for_non_retryable(resp, text, route=route)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise WireFormatError(
                detail=f"non-JSON response: {text[:200]}",
                status=resp.status,
                instance=_make_instance(),
                route=route,
                discovery=_build_wire_format_discovery(resp, text, route),
            ) from e
```

- [ ] **Step 4: Run to confirm pass**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_client_scene.py::test_get_json_attaches_bearer_for_aisandbox -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_client_scene.py
git commit -m "feat(scene): add aisandbox-aware _get_json helper (Bearer + 401 retry)"
```

---

## Task 4: `commit_workflow`

**Files:**
- Modify: `src/gflow_cli/api/client.py` (add near `archive_workflow`, ~line 793)
- Test: `tests/api/test_client_scene.py`

> Commit sets `metadata.primaryMediaId` so the workflow becomes placeable. Same PATCH shape as `archive_workflow`, different `updateMask`. Auth is free (PATCH → L0). Verify body against `samples/captured/15` from Task 0.

- [ ] **Step 1: Write the failing test**

```python
async def test_commit_workflow_sends_primary_media_id_patch():
    page = _FakePage(_FakeResp(200, "{}"))
    c = _client_with(page)
    await c.commit_workflow("wf-1", project_id="proj-1", primary_media_id="media-9")
    method, url, kw = page.request.calls[-1]
    assert method == "PATCH"
    assert url.endswith("/v1/flowWorkflows/wf-1")
    import json as _json
    body = _json.loads(kw["data"])
    assert body["updateMask"] == "metadata.primaryMediaId"
    assert body["workflow"]["metadata"]["primaryMediaId"] == "media-9"
    assert body["workflow"]["projectId"] == "proj-1"
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: ... 'commit_workflow'`).
Run: `.venv/Scripts/python.exe -m pytest tests/api/test_client_scene.py::test_commit_workflow_sends_primary_media_id_patch -q`

- [ ] **Step 3: Implement**

```python
    async def commit_workflow(
        self, workflow_id: str, *, project_id: str, primary_media_id: str
    ) -> None:
        """Commit a workflow's primaryMediaId so it can be placed in a scene.

        Maps to `PATCH /v1/flowWorkflows/{id}` with updateMask
        `metadata.primaryMediaId`. Auth handled by L0 (_patch_json Bearer path).
        """
        body = {
            "workflow": {
                "name": workflow_id,
                "projectId": project_id,
                "metadata": {"primaryMediaId": primary_media_id},
            },
            "updateMask": "metadata.primaryMediaId",
        }
        await self._patch_json(
            routes.flow_workflow_url(workflow_id), body, route_name="commitWorkflow"
        )
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_client_scene.py
git commit -m "feat(scene): add commit_workflow (primaryMediaId PATCH)"
```

---

## Task 5: `create_scene`

**Files:**
- Modify: `src/gflow_cli/api/client.py`
- Test: `tests/api/test_client_scene.py`

> `POST /v1/flow/projects/{pid}/scenes` with `{workflowIds:[...]}` (ordered; repeat to duplicate). Returns a `Scene`. Auth free (POST → L0). Verify request/response against `samples/captured/12`.

- [ ] **Step 1: Write the failing test**

```python
async def test_create_scene_posts_ordered_workflow_ids(monkeypatch):
    resp_text = '{"scene": {"sceneId": "scene-x", "sceneWorkflows": [{"workflow": {"name": "inst-1"}, "sceneWorkflowMetadata": {"position": 0, "startTime": "0s", "endTime": "8s", "totalDuration": "8s"}}]}}'
    page = _FakePage(_FakeResp(200, resp_text))
    c = _client_with(page)
    scene = await c.create_scene(project_id="proj-1", workflow_ids=["wf-a", "wf-a", "wf-b"])
    method, url, kw = page.request.calls[-1]
    assert method == "POST" and url.endswith("/projects/proj-1/scenes")
    import json as _json
    assert _json.loads(kw["data"])["workflowIds"] == ["wf-a", "wf-a", "wf-b"]
    assert scene.scene_id == "scene-x"
    assert scene.project_id == "proj-1"
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement**

```python
    async def create_scene(self, *, project_id: str, workflow_ids: list[str]) -> Scene:
        """Compose a scene from an ordered list of source workflowIds.

        Maps to `POST /v1/flow/projects/{pid}/scenes`. Repeat an id to clone a
        clip (faithful to Flow). Returns the parsed Scene (sceneId + instances).
        """
        data = await self._post_json(
            routes.scenes_url(project_id),
            {"workflowIds": list(workflow_ids)},
            route_name="createScene",
        )
        return Scene.from_create_response(data, project_id=project_id)
```
Add `from gflow_cli.api.scene import Scene` to `client.py` imports.

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_client_scene.py
git commit -m "feat(scene): add create_scene (ordered workflowIds -> Scene)"
```

---

## Task 6: `update_scene_workflows`

**Files:**
- Modify: `src/gflow_cli/api/client.py`
- Test: `tests/api/test_client_scene.py`

> `POST /v1/flow/scene/sceneWorkflows:update` with `{sceneId, projectId, sceneWorkflows:[<each.to_wire()>]}`. Sets order + trims. Verify against `samples/captured/13`.

- [ ] **Step 1: Write the failing test**

```python
async def test_update_scene_workflows_sends_trims_and_order():
    from gflow_cli.api.scene import SceneWorkflow, SceneWorkflowMetadata
    page = _FakePage(_FakeResp(200, "{}"))
    c = _client_with(page)
    wfs = [
        SceneWorkflow("inst-1", SceneWorkflowMetadata(0, 0.0, 8.0, 8.0)),
        SceneWorkflow("inst-2", SceneWorkflowMetadata(1, 3.2, 5.2, 8.0)),
    ]
    await c.update_scene_workflows(scene_id="scene-x", project_id="proj-1", workflows=wfs)
    method, url, kw = page.request.calls[-1]
    assert method == "POST" and url.endswith("/scene/sceneWorkflows:update")
    import json as _json
    body = _json.loads(kw["data"])
    assert body["sceneId"] == "scene-x" and body["projectId"] == "proj-1"
    assert body["sceneWorkflows"][1]["sceneWorkflowMetadata"]["startTime"] == "3.200000000s"
    assert body["sceneWorkflows"][1]["workflow"]["name"] == "inst-2"
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement**

```python
    async def update_scene_workflows(
        self, *, scene_id: str, project_id: str, workflows: list[SceneWorkflow]
    ) -> None:
        """Set per-clip order + trim for a scene.

        Maps to `POST /v1/flow/scene/sceneWorkflows:update`.
        """
        body = {
            "sceneId": scene_id,
            "projectId": project_id,
            "sceneWorkflows": [w.to_wire(scene_id=scene_id) for w in workflows],
        }
        await self._post_json(
            routes.SCENE_WORKFLOWS_UPDATE, body, route_name="updateSceneWorkflows"
        )
```
Add `SceneWorkflow` to the `from gflow_cli.api.scene import ...` line.

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_client_scene.py
git commit -m "feat(scene): add update_scene_workflows (order + trim)"
```

---

## Task 7: `get_scene_workflows`

**Files:**
- Modify: `src/gflow_cli/api/client.py`
- Test: `tests/api/test_client_scene.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_get_scene_workflows_reads_back_via_get_json():
    resp_text = '{"sceneWorkflows": [{"workflow": {"name": "inst-2"}, "sceneWorkflowMetadata": {"position": 1, "startTime": "3.2s", "endTime": "5.2s", "totalDuration": "8s"}}, {"workflow": {"name": "inst-1"}, "sceneWorkflowMetadata": {"position": 0, "startTime": "0s", "endTime": "8s", "totalDuration": "8s"}}]}'
    page = _FakePage(_FakeResp(200, resp_text))
    c = _client_with(page)
    scene = await c.get_scene_workflows("scene-x", project_id="proj-1")
    method, url, _ = page.request.calls[-1]
    assert method == "GET" and url.endswith("/scene/scene-x/workflows")
    # sorted by position
    assert [w.workflow_id for w in scene.workflows] == ["inst-1", "inst-2"]
    assert scene.workflows[1].metadata.start_time == 3.2
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement**

```python
    async def get_scene_workflows(self, scene_id: str, *, project_id: str) -> Scene:
        """Read back a scene's clips (order + trims).

        Maps to `GET /v1/flow/scene/{sceneId}/workflows` via the aisandbox-aware
        _get_json helper.
        """
        data = await self._get_json(
            routes.scene_workflows_url(scene_id), route_name="getSceneWorkflows"
        )
        return Scene.from_get_response(data, scene_id=scene_id, project_id=project_id)
```

- [ ] **Step 4: Run — expect PASS. Then run the whole scene client suite:**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_client_scene.py -q`
Expected: PASS (all scene client tests).

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_client_scene.py
git commit -m "feat(scene): add get_scene_workflows read-back"
```

---

## Task 8: Persistence — migration `0003` + models

**Files:**
- Create: `src/gflow_cli/data/migrations/0003_add_scene_tables.sql`
- Modify: `src/gflow_cli/data/models.py`
- Test: `tests/data/test_scene_persistence.py`

> Scenes need their own tables because trim metadata (start/end/total per clip) has no home in `operation_assets` (role+position only). `scenes` is the aggregate; `scene_clips` holds the ordered, trimmed instances. The migration runner discovers `*.sql` by filename order (it already applied `0001`/`0002`), so the file just needs the next number + valid DDL.

- [ ] **Step 1: Write the failing test (migration applies + tables exist)**

```python
# tests/data/test_scene_persistence.py
from gflow_cli.data.store import DataStore

def test_migration_0003_creates_scene_tables(tmp_path):
    store = DataStore.open(tmp_path / "t.db")
    try:
        cur = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('scenes','scene_clips')"
        )
        names = {r[0] for r in cur.fetchall()}
        assert names == {"scenes", "scene_clips"}
    finally:
        store.close()
```
> If `DataStore` exposes the connection under a different attribute than `.conn`, adjust the accessor — check `data/store.py` for the public handle (e.g. `store._conn` or a `cursor()` method) and use the public one.

- [ ] **Step 2: Run — expect FAIL** (tables missing).
Run: `.venv/Scripts/python.exe -m pytest tests/data/test_scene_persistence.py::test_migration_0003_creates_scene_tables -q`

- [ ] **Step 3: Create `0003_add_scene_tables.sql`**

```sql
CREATE TABLE scenes (
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  flow_project_id TEXT NOT NULL,
  flow_scene_id TEXT NOT NULL,
  total_duration REAL,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(profile_name) REFERENCES profiles(name),
  UNIQUE(profile_name, flow_scene_id)
);

CREATE TABLE scene_clips (
  id TEXT PRIMARY KEY,
  scene_id TEXT NOT NULL,
  position INTEGER NOT NULL,
  flow_instance_workflow_id TEXT NOT NULL,
  flow_source_workflow_id TEXT,
  flow_media_id TEXT,
  start_time REAL NOT NULL,
  end_time REAL NOT NULL,
  total_duration REAL NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(scene_id) REFERENCES scenes(id),
  UNIQUE(scene_id, position)
);

CREATE INDEX idx_scenes_profile_flow ON scenes(profile_name, flow_scene_id);
CREATE INDEX idx_scene_clips_scene ON scene_clips(scene_id, position);
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Add models to `data/models.py`** (after `LocalFileRecord`; extend `OperationKind`)

```python
class OperationKind(StrEnum):
    UPLOAD_IMAGE = "upload_image"
    T2I = "t2i"
    I2I = "i2i"
    T2V = "t2v"
    I2V = "i2v"
    R2V = "r2v"
    SCENE_CREATE = "scene_create"
    SCENE_ADD_CLIP = "scene_add_clip"


@dataclass(frozen=True)
class SceneRecord:
    id: str
    profile_name: str
    flow_project_id: str
    flow_scene_id: str
    total_duration: float | None
    source: str
    created_at: str | None = None


@dataclass(frozen=True)
class SceneClipRecord:
    id: str
    scene_id: str
    position: int
    flow_instance_workflow_id: str
    flow_source_workflow_id: str | None
    flow_media_id: str | None
    start_time: float
    end_time: float
    total_duration: float
    created_at: str | None = None
```

- [ ] **Step 6: Run the full data suite to confirm no enum/ordering regression**

Run: `.venv/Scripts/python.exe -m pytest tests/data -q`
Expected: PASS (existing + new). If `test_exit_code_map_ordering_invariant` or an `OperationKind` round-trip test exists, confirm green (new enum values are additive).

- [ ] **Step 7: Commit**

```bash
git add src/gflow_cli/data/migrations/0003_add_scene_tables.sql src/gflow_cli/data/models.py tests/data/test_scene_persistence.py
git commit -m "feat(scene): add 0003 scene tables + Scene/SceneClip records + OperationKind values"
```

---

## Task 9: Repository methods

**Files:**
- Modify: `src/gflow_cli/data/repository.py`
- Test: `tests/data/test_scene_persistence.py`

> Mirror the existing `upsert_*` style (parametrized SQL, `_now_utc_iso()` default for `created_at`, `INSERT ... ON CONFLICT`). `replace_scene_clips` deletes then re-inserts the clip rows for a scene so re-running `update_scene_workflows` is idempotent.

- [ ] **Step 1: Write the failing test**

```python
import uuid
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.data.models import SceneRecord, SceneClipRecord

def _repo(tmp_path):
    repo = DataRepository(DataStore.open(tmp_path / "t.db"))
    repo.upsert_profile("p", tmp_path)
    return repo

def test_upsert_scene_and_replace_clips_roundtrip(tmp_path):
    repo = _repo(tmp_path)
    sid = str(uuid.uuid4())
    repo.upsert_scene(SceneRecord(id=sid, profile_name="p", flow_project_id="proj-1",
                                  flow_scene_id="scene-x", total_duration=13.2, source="composed"))
    repo.replace_scene_clips(sid, [
        SceneClipRecord(id=str(uuid.uuid4()), scene_id=sid, position=0,
                        flow_instance_workflow_id="inst-1", flow_source_workflow_id="wf-a",
                        flow_media_id="m1", start_time=0.0, end_time=8.0, total_duration=8.0),
        SceneClipRecord(id=str(uuid.uuid4()), scene_id=sid, position=1,
                        flow_instance_workflow_id="inst-2", flow_source_workflow_id="wf-b",
                        flow_media_id="m2", start_time=3.2, end_time=5.2, total_duration=8.0),
    ])
    got = repo.get_scene_by_flow_scene_id("p", "scene-x")
    assert got is not None and got.id == sid
    clips = repo.get_scene_clips(sid)
    assert [c.position for c in clips] == [0, 1]
    # replace is idempotent — second call doesn't duplicate
    repo.replace_scene_clips(sid, clips)
    assert len(repo.get_scene_clips(sid)) == 2
    repo.store.close()
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: ... 'upsert_scene'`).

- [ ] **Step 3: Implement in `repository.py`** (use the module's existing `_now_iso`/timestamp helper — check the top of `repository.py` for its name; the snippet below assumes `_now_iso()`, rename to match)

```python
    def upsert_scene(self, record: SceneRecord) -> SceneRecord:
        created = record.created_at or _now_iso()
        self.store.conn.execute(
            """
            INSERT INTO scenes (id, profile_name, flow_project_id, flow_scene_id,
                                total_duration, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_name, flow_scene_id) DO UPDATE SET
                total_duration=excluded.total_duration, source=excluded.source
            """,
            (record.id, record.profile_name, record.flow_project_id, record.flow_scene_id,
             record.total_duration, record.source, created),
        )
        self.store.conn.commit()
        return record

    def replace_scene_clips(self, scene_id: str, clips: list[SceneClipRecord]) -> None:
        conn = self.store.conn
        conn.execute("DELETE FROM scene_clips WHERE scene_id = ?", (scene_id,))
        conn.executemany(
            """
            INSERT INTO scene_clips (id, scene_id, position, flow_instance_workflow_id,
                flow_source_workflow_id, flow_media_id, start_time, end_time,
                total_duration, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(c.id, scene_id, c.position, c.flow_instance_workflow_id, c.flow_source_workflow_id,
              c.flow_media_id, c.start_time, c.end_time, c.total_duration, c.created_at or _now_iso())
             for c in clips],
        )
        conn.commit()

    def get_scene_by_flow_scene_id(self, profile_name: str, flow_scene_id: str) -> SceneRecord | None:
        row = self.store.conn.execute(
            "SELECT id, profile_name, flow_project_id, flow_scene_id, total_duration, source, created_at "
            "FROM scenes WHERE profile_name = ? AND flow_scene_id = ?",
            (profile_name, flow_scene_id),
        ).fetchone()
        if row is None:
            return None
        return SceneRecord(id=row[0], profile_name=row[1], flow_project_id=row[2],
                           flow_scene_id=row[3], total_duration=row[4], source=row[5], created_at=row[6])

    def get_scene_clips(self, scene_id: str) -> list[SceneClipRecord]:
        rows = self.store.conn.execute(
            "SELECT id, scene_id, position, flow_instance_workflow_id, flow_source_workflow_id, "
            "flow_media_id, start_time, end_time, total_duration, created_at "
            "FROM scene_clips WHERE scene_id = ? ORDER BY position",
            (scene_id,),
        ).fetchall()
        return [SceneClipRecord(*r) for r in rows]
```
Add `SceneRecord, SceneClipRecord` to the `from gflow_cli.data.models import ...` line in `repository.py`.

- [ ] **Step 4: Run — expect PASS.**
Run: `.venv/Scripts/python.exe -m pytest tests/data/test_scene_persistence.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/data/repository.py tests/data/test_scene_persistence.py
git commit -m "feat(scene): add scene/scene_clips repository methods"
```

---

## Task 10: Recorder — `record_scene`

**Files:**
- Modify: `src/gflow_cli/data/recorder.py`
- Test: `tests/data/test_scene_persistence.py`

> Records a scene compose as: profile + project upsert, a `scenes` row, its `scene_clips`, and an `operations` row (mode = `SCENE_CREATE` or `SCENE_ADD_CLIP`) so it shows in `gflow data`. Accepts the parsed `Scene` + the op kind. No prompt fields (scenes have no prompt).

- [ ] **Step 1: Write the failing test**

```python
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.data.redaction import PromptMode
from gflow_cli.data.models import OperationKind
from gflow_cli.api.scene import Scene, SceneWorkflow, SceneWorkflowMetadata

def test_record_scene_persists_scene_clips_and_operation(tmp_path):
    rec = OperationRecorder(DataRepository(DataStore.open(tmp_path / "t.db")), prompt_mode=PromptMode.HASH)
    scene = Scene(scene_id="scene-x", project_id="proj-1", workflows=(
        SceneWorkflow("inst-1", SceneWorkflowMetadata(0, 0.0, 8.0, 8.0)),
        SceneWorkflow("inst-2", SceneWorkflowMetadata(1, 3.2, 5.2, 8.0)),
    ))
    rec.record_scene(profile_name="p", profile_dir=tmp_path, scene=scene,
                     operation_kind=OperationKind.SCENE_CREATE)
    repo = rec.repository
    got = repo.get_scene_by_flow_scene_id("p", "scene-x")
    assert got is not None
    assert len(repo.get_scene_clips(got.id)) == 2
    rec.close()
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: ... 'record_scene'`).

- [ ] **Step 3: Implement `record_scene` in `OperationRecorder`**

```python
    def record_scene(
        self,
        *,
        profile_name: str,
        profile_dir: Path,
        scene: Scene,
        operation_kind: OperationKind,
        source: str = "composed",
    ) -> None:
        from gflow_cli.api.scene import Scene as _Scene  # noqa: F401 (type clarity)

        repo = self.repository
        repo.upsert_profile(profile_name, profile_dir)
        repo.upsert_project(
            ProjectRecord(
                id=_new_id(),
                profile_name=profile_name,
                flow_project_id=scene.project_id,
                title=None,
                source="generated",
            ),
        )
        total = max((w.metadata.end_time - w.metadata.start_time for w in scene.workflows), default=0.0)
        # actual composed duration = sum of visible clip lengths
        total = sum((w.metadata.end_time - w.metadata.start_time) for w in scene.workflows)
        scene_row_id = _new_id()
        repo.upsert_scene(
            SceneRecord(
                id=scene_row_id,
                profile_name=profile_name,
                flow_project_id=scene.project_id,
                flow_scene_id=scene.scene_id,
                total_duration=total,
                source=source,
            ),
        )
        repo.replace_scene_clips(
            scene_row_id,
            [
                SceneClipRecord(
                    id=_new_id(),
                    scene_id=scene_row_id,
                    position=w.metadata.position,
                    flow_instance_workflow_id=w.workflow_id,
                    flow_source_workflow_id=None,
                    flow_media_id=None,
                    start_time=w.metadata.start_time,
                    end_time=w.metadata.end_time,
                    total_duration=w.metadata.total_duration,
                )
                for w in scene.workflows
            ],
        )
        op_id = _new_id()
        repo.insert_operation(
            OperationRecord(
                id=op_id,
                profile_name=profile_name,
                flow_project_id=scene.project_id,
                command="scene create" if operation_kind is OperationKind.SCENE_CREATE else "scene add-clip",
                mode=operation_kind,
                status=OperationStatus.SUCCEEDED,
                flow_operation_id=None,
                flow_batch_id=None,
                prompt=None,
                prompt_hash=None,
                prompt_redacted=False,
                model=None,
                aspect_ratio=None,
                error_type=None,
                error_detail=None,
            ),
        )
        repo.update_operation_status(op_id, OperationStatus.SUCCEEDED, _now_utc_iso(), None, None)
```
Add imports to `recorder.py`: `SceneRecord, SceneClipRecord` to the `from gflow_cli.data.models import (...)` block, and under `TYPE_CHECKING` add `from gflow_cli.api.scene import Scene`.

- [ ] **Step 4: Run — expect PASS.**
Run: `.venv/Scripts/python.exe -m pytest tests/data/test_scene_persistence.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/data/recorder.py tests/data/test_scene_persistence.py
git commit -m "feat(scene): add OperationRecorder.record_scene"
```

---

## Task 11: CLI — `gflow scene` group (`create` / `add-clip` / `show`)

**Files:**
- Create: `src/gflow_cli/cli_scene.py`
- Modify: `src/gflow_cli/cli.py`
- Test: `tests/cli/test_cli_scene.py`

> Three commands. `create` and `add-clip` parse `clipRef[:start-end]`, validate trims **before** any network call, run the full lifecycle (commit→create/append→update→read-back), render the result, then record (non-blocking). `show` is read-only. Model the command/`asyncio.run`/recorder wiring on `cli_image.py:_run_upload`. clipRef = raw `workflowId` (no data-layer resolution in v1).

- [ ] **Step 1: Write failing tests — clipRef parsing + trim validation (pure logic, no browser)**

```python
# tests/cli/test_cli_scene.py
import pytest
from gflow_cli.cli_scene import _parse_clip_ref, _validate_trim, ClipRef

def test_parse_clip_ref_no_trim():
    assert _parse_clip_ref("wf-123") == ClipRef("wf-123", None, None)

def test_parse_clip_ref_with_trim():
    assert _parse_clip_ref("wf-123:3.2-5.2") == ClipRef("wf-123", 3.2, 5.2)

def test_parse_clip_ref_bad_trim_raises():
    with pytest.raises(ValueError):
        _parse_clip_ref("wf-123:5-3")   # start >= end

def test_validate_trim_rejects_out_of_range():
    with pytest.raises(ValueError):
        _validate_trim(start=0.0, end=9.0, total=8.0)  # end > total

def test_validate_trim_accepts_valid():
    _validate_trim(start=0.0, end=8.0, total=8.0)  # no raise
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: gflow_cli.cli_scene`).

- [ ] **Step 3: Implement `cli_scene.py`** (group + helpers + three commands)

```python
"""`gflow scene` — compose Flow Scenes (Add Clip). Credit-free REST."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click
import structlog
from rich.console import Console

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.scene import SceneWorkflow, SceneWorkflowMetadata
from gflow_cli.config import get_settings
from gflow_cli.data.models import OperationKind
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.errors import DataStoreError
# Reuse the shared profile/dir/error helpers used by the other cli_* modules.
from gflow_cli.cli_image import _make_provider_dir, _resolve_profile, run_with_handlers

console = Console()
log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ClipRef:
    workflow_id: str
    start: float | None
    end: float | None


def _validate_trim(*, start: float, end: float, total: float) -> None:
    if not (0.0 <= start < end <= total):
        msg = f"trim out of range: need 0 <= start < end <= total({total}); got start={start} end={end}"
        raise ValueError(msg)


def _parse_clip_ref(token: str) -> ClipRef:
    """Parse `workflowId[:start-end]` (seconds). start<end enforced; range vs
    total is validated later once totalDuration is known from read-back."""
    if ":" not in token:
        return ClipRef(token, None, None)
    wf, _, trim = token.partition(":")
    try:
        start_s, _, end_s = trim.partition("-")
        start, end = float(start_s), float(end_s)
    except ValueError as e:
        msg = f"bad trim in clipRef {token!r}: expected <start>-<end> in seconds"
        raise ValueError(msg) from e
    if not (start < end):
        msg = f"bad trim in clipRef {token!r}: start must be < end"
        raise ValueError(msg)
    return ClipRef(wf, start, end)


@click.group()
def scene() -> None:
    """Compose ordered, trimmable video clips into a Flow Scene (Add Clip)."""


@scene.command("create")
@click.option("--project", "project_id", required=True, help="Flow project id.")
@click.argument("clip_refs", nargs=-1, required=True)
@click.option("--profile", default=None, help="Profile name (overrides default).")
def create(project_id: str, clip_refs: tuple[str, ...], profile: str | None) -> None:
    """Compose a new scene from CLIP_REFS (each: workflowId[:start-end])."""
    refs = [_parse_clip_ref(t) for t in clip_refs]  # validates start<end pre-network
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_create(profile_name=profile_name, profile_dir=pdir,
                            headless=settings.headless, project_id=project_id, refs=refs),
        cli_command="scene create",
    )


@scene.command("add-clip")
@click.option("--scene", "scene_id", required=True, help="Existing scene id.")
@click.option("--project", "project_id", required=True, help="Flow project id.")
@click.argument("clip_ref", required=True)
@click.option("--profile", default=None)
def add_clip(scene_id: str, project_id: str, clip_ref: str, profile: str | None) -> None:
    """Append one CLIP_REF (workflowId[:start-end]) to an existing scene."""
    ref = _parse_clip_ref(clip_ref)
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_add_clip(profile_name=profile_name, profile_dir=pdir,
                              headless=settings.headless, scene_id=scene_id,
                              project_id=project_id, ref=ref),
        cli_command="scene add-clip",
    )


@scene.command("show")
@click.option("--scene", "scene_id", required=True)
@click.option("--project", "project_id", required=True)
@click.option("--profile", default=None)
def show(scene_id: str, project_id: str, profile: str | None) -> None:
    """Read back a scene's clip order and trims."""
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_show(profile_dir=pdir, headless=settings.headless,
                          scene_id=scene_id, project_id=project_id),
        cli_command="scene show",
    )


def _render(scene_obj) -> None:
    console.print(f"[bold green]Scene:[/bold green] [bold]{scene_obj.scene_id}[/bold]")
    composed = sum(w.metadata.end_time - w.metadata.start_time for w in scene_obj.workflows)
    for w in scene_obj.workflows:
        m = w.metadata
        console.print(f"  [{m.position}] {w.workflow_id}  trim {m.start_time:g}-{m.end_time:g}s "
                      f"(of {m.total_duration:g}s)")
    console.print(f"[dim]Composed duration:[/dim] {composed:g}s  [dim]Clips:[/dim] {len(scene_obj.workflows)}")


async def _run_create(*, profile_name, profile_dir: Path, headless, project_id, refs) -> None:
    recorder = OperationRecorder.open(get_settings())
    try:
        async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
            scene_obj = await client.create_scene(
                project_id=project_id, workflow_ids=[r.workflow_id for r in refs]
            )
            scene_obj = await _apply_trims(client, scene_obj, project_id, refs)
            _render(scene_obj)
            try:
                recorder.record_scene(profile_name=profile_name, profile_dir=profile_dir,
                                      scene=scene_obj, operation_kind=OperationKind.SCENE_CREATE)
            except DataStoreError as exc:
                log.warning("scene.persist_failed_after_success", error=str(exc), scene_id=scene_obj.scene_id)
                console.print(f"[yellow]Scene created but not recorded locally:[/yellow] {exc}")
    finally:
        recorder.close()


async def _apply_trims(client, scene_obj, project_id, refs):
    """Map trims from refs onto the created instances by position, validate vs
    the instance's totalDuration, then update + read back."""
    updated: list[SceneWorkflow] = []
    for w, ref in zip(scene_obj.workflows, refs, strict=False):
        total = w.metadata.total_duration
        start = ref.start if ref.start is not None else 0.0
        end = ref.end if ref.end is not None else total
        _validate_trim(start=start, end=end, total=total)
        updated.append(SceneWorkflow(w.workflow_id,
                       SceneWorkflowMetadata(w.metadata.position, start, end, total)))
    if any(r.start is not None for r in refs):
        await client.update_scene_workflows(scene_id=scene_obj.scene_id,
                                             project_id=project_id, workflows=updated)
    return await client.get_scene_workflows(scene_obj.scene_id, project_id=project_id)


async def _run_add_clip(*, profile_name, profile_dir: Path, headless, scene_id, project_id, ref) -> None:
    recorder = OperationRecorder.open(get_settings())
    try:
        async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
            current = await client.get_scene_workflows(scene_id, project_id=project_id)
            existing_sources = [w.workflow_id for w in current.workflows]
            await client.create_scene(project_id=project_id,
                                      workflow_ids=[*existing_sources, ref.workflow_id])
            scene_obj = await client.get_scene_workflows(scene_id, project_id=project_id)
            _render(scene_obj)
            try:
                recorder.record_scene(profile_name=profile_name, profile_dir=profile_dir,
                                      scene=scene_obj, operation_kind=OperationKind.SCENE_ADD_CLIP)
            except DataStoreError as exc:
                log.warning("scene.persist_failed_after_success", error=str(exc), scene_id=scene_id)
                console.print(f"[yellow]Clip added but not recorded locally:[/yellow] {exc}")
    finally:
        recorder.close()


async def _run_show(*, profile_dir: Path, headless, scene_id, project_id) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        _render(await client.get_scene_workflows(scene_id, project_id=project_id))
```

> **Implementation note for the executor:** confirm the exact names of the shared CLI helpers (`_resolve_profile`, `_make_provider_dir`, `run_with_handlers`) in `cli_image.py` before importing — if any is private/named differently, either import the real name or lift a tiny shared helper into a `cli_common.py`. Also re-confirm the `add-clip` semantics against the Task 0 `/scenes` capture: the protocol says scenes are composed by POSTing the full ordered `workflowIds` list, so append = re-create with the existing instances' SOURCE ids + the new one. If the HAR shows a dedicated append endpoint, prefer it.

- [ ] **Step 4: Run the pure-logic tests — expect PASS**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_scene.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Register the group in `cli.py`** (add import + `add_command`)

```python
from gflow_cli.cli_scene import scene as _scene_group
# ... after the other add_command calls:
main.add_command(_scene_group)
```

- [ ] **Step 6: Add a CLI-wiring test** (group registered + `--help` works via Click's `CliRunner`)

```python
from click.testing import CliRunner
from gflow_cli.cli import main

def test_scene_group_registered():
    res = CliRunner().invoke(main, ["scene", "--help"])
    assert res.exit_code == 0
    assert "create" in res.output and "add-clip" in res.output and "show" in res.output
```

- [ ] **Step 7: Run — expect PASS, then commit**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_scene.py -q`
```bash
git add src/gflow_cli/cli_scene.py src/gflow_cli/cli.py tests/cli/test_cli_scene.py
git commit -m "feat(scene): add gflow scene CLI group (create/add-clip/show)"
```

---

## Task 12: Credit-free e2e + marker

**Files:**
- Modify: `pyproject.toml`, `tests/test_marker_registry.py`
- Create: `tests/e2e/test_scene_compose_live.py`

> The whole flow is free (no `batchAsyncGenerate*`). The e2e composes a scene from an EXISTING `workflowId` (provided via env) and asserts the read-back order — and asserts **zero** generate calls fired by checking the `batchAsyncGenerate` route is never hit (the client only calls scene endpoints).

- [ ] **Step 1: Add the `e2e_scene` marker to `pyproject.toml`** (after `e2e_data`)

```toml
    "e2e_scene: scene/timeline compose (Add Clip) — zero credits, no reCAPTCHA",
```

- [ ] **Step 2: Add `e2e_scene` to the required sub-marker set in `tests/test_marker_registry.py`**

```python
    {"e2e_auth", "e2e_image", "e2e_video", "e2e_batch", "e2e_data", "e2e_scene"}
```

- [ ] **Step 3: Run the marker-registry test — expect PASS**

Run: `.venv/Scripts/python.exe -m pytest tests/test_marker_registry.py -q`

- [ ] **Step 4: Write the e2e test** (opt-in; models `test_aisandbox_auth_live.py`; needs `GFLOW_CLI_E2E_PROFILE` + `GFLOW_CLI_E2E_SCENE_WORKFLOW_ID` = an existing clip)

```python
# tests/e2e/test_scene_compose_live.py
"""Credit-free e2e for `gflow scene` — composes an existing clip into a scene.
Opt-in: -m e2e_scene + GFLOW_CLI_E2E_PROFILE + GFLOW_CLI_E2E_SCENE_WORKFLOW_ID.
Asserts zero batchAsyncGenerate* calls fired (scene ops cost nothing)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_scene]


async def test_scene_compose_is_credit_free(monkeypatch, tmp_path: Path) -> None:
    from gflow_cli.config import reset_settings
    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(tmp_path / "e2e.db"))
    reset_settings()

    name = os.environ.get("GFLOW_CLI_E2E_PROFILE", "")
    wf = os.environ.get("GFLOW_CLI_E2E_SCENE_WORKFLOW_ID", "")
    if not name or not wf:
        pytest.skip("set GFLOW_CLI_E2E_PROFILE + GFLOW_CLI_E2E_SCENE_WORKFLOW_ID, then -m e2e_scene")
    from gflow_cli.auth import profile_dir as _resolve_profile_dir
    profile = _resolve_profile_dir(name)
    if not profile.exists():
        pytest.skip(f"profile not found: {profile}")

    generate_calls: list[str] = []
    async with FlowApiClient(profile_dir=profile) as client:
        # Spy: flag if any generate route is ever requested.
        orig_post = client._post_json
        async def _spy(url, body, **kw):
            if "batchAsyncGenerate" in url or "batchGenerate" in url:
                generate_calls.append(url)
            return await orig_post(url, body, **kw)
        monkeypatch.setattr(client, "_post_json", _spy)

        project = await client.create_project(title="scene e2e")
        scene = await client.create_scene(project_id=project.project_id, workflow_ids=[wf, wf])
        read_back = await client.get_scene_workflows(scene.scene_id, project_id=project.project_id)

    assert scene.scene_id, "create_scene returned a sceneId"
    assert len(read_back.workflows) == 2, "two clip instances (duplicate of one source)"
    assert generate_calls == [], f"scene compose must spend ZERO credits; saw {generate_calls}"
```

- [ ] **Step 5: Commit** (this test is opt-in; CI's default `-m 'not e2e ...'` skips it)

```bash
git add pyproject.toml tests/test_marker_registry.py tests/e2e/test_scene_compose_live.py
git commit -m "test(scene): add e2e_scene marker + credit-free compose e2e"
```

---

## Task 13: Docs + full verification

**Files:**
- Modify: `docs/INDEX.md` (link the scene command), `README.md` (one line under commands), `CHANGELOG.md` (Unreleased → Added).
- Modify: `docs/superpowers/specs/2026-05-30-add-clip-scene-timeline-design.md` (mark L1 implemented).

- [ ] **Step 1: Add a CHANGELOG entry under `## [Unreleased]` → `### Added`**

```markdown
- `gflow scene` command group (Add Clip): `create`, `add-clip`, `show` — compose
  ordered, trimmable video clips into a Flow Scene via credit-free REST.
```

- [ ] **Step 2: Add a one-line command reference to `README.md` and a `docs/INDEX.md` pointer** (follow the existing command-list format in each file — match surrounding style).

- [ ] **Step 3: Run lint + type + the full changed-surface test sweep**

```bash
.venv/Scripts/python.exe -m ruff check src/gflow_cli/api/scene.py src/gflow_cli/api/client.py src/gflow_cli/api/routes.py src/gflow_cli/cli_scene.py src/gflow_cli/data
.venv/Scripts/python.exe -m ruff format --check src/gflow_cli
.venv/Scripts/python.exe -m pyright src/gflow_cli/api/scene.py src/gflow_cli/cli_scene.py src/gflow_cli/data/recorder.py
.venv/Scripts/python.exe -m pytest tests/api tests/data tests/cli -q
```
Expected: ruff clean, pyright clean, all unit/integration tests PASS. (Full suite OOMs locally — trust CI for the global sweep; scope to changed dirs here.)

- [ ] **Step 4: Run `/gflow:check`** and resolve anything it flags.

- [ ] **Step 5: Commit docs**

```bash
git add docs/ README.md CHANGELOG.md
git commit -m "docs(scene): document gflow scene command group + mark L1 implemented"
```

- [ ] **Step 6: Live verification (manual, credit-free)** — with a logged-in profile and a known existing clip workflowId:

```bash
GFLOW_CLI_E2E_PROFILE=ffroliva GFLOW_CLI_E2E_SCENE_WORKFLOW_ID=<wf> \
  .venv/Scripts/python.exe -m pytest tests/e2e/test_scene_compose_live.py -m e2e_scene -q
```
Expected: PASS — proves the scene composes and spends zero credits on the real API.

---

## Self-Review (completed during planning)

**Spec coverage:** §3 CLI surface → Tasks 1,2,11. §4 architecture (scene.py, 4 client methods, routes, cli) → Tasks 1–7,11. §4 `_get_json` gap → Task 3. §5 testing (unit + credit-free e2e + zero-generate assertion) → Tasks 2,7,12. §6 persistence (migration + non-blocking recorder) → Tasks 8,9,10 + non-blocking wiring in Task 11. §6 trim validation pre-network → Task 11 (`_validate_trim`). §6 distinct 401 error → already exists (`AisandboxAuthError`, L0) — no task needed, noted in invariants. §6 secret hygiene → invariants + Task 0 Step 3 redaction gate. All three commands → Task 11. Raw-workflowId-only → Task 11 (`_parse_clip_ref`).

**Deviations from spec, by design:** (1) migration is **0003** not 0002 (0002 taken). (2) No new `EXIT_CODE_MAP` entry — L0 already shipped `AisandboxAuthError`. (3) SSRF allowlist on read-back download: **not exercised by L1** — `get_scene_workflows` hits a fixed aisandbox host (not a user-supplied URL), and L1 ships no media-download command; deferred to L2 where uploads/downloads land. Flagged for council.

**Known uncertainty (council, please weigh):** exact create/get **response** field paths are pinned only at Task 0 (HAR extraction). Tasks 2 & 7 parsers encode the protocol-memory shape and are verified against the captured fixtures during implementation; if the HAR nesting differs, the `from_*` accessors adjust with no signature change.

**Placeholder scan:** none — every code step carries real code; the two "confirm helper name / response path" notes are verification instructions, not deferred work.

**Type consistency:** `Scene`/`SceneWorkflow`/`SceneWorkflowMetadata` field names consistent across Tasks 2,5,6,7,10,11. `SceneRecord`/`SceneClipRecord` consistent across Tasks 8,9,10. `OperationKind.SCENE_CREATE`/`SCENE_ADD_CLIP` consistent across Tasks 8,10,11.
