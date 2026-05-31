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
    c._page = page
    c._page_queue = None
    c._context = None
    c._access_token = "ya29.test"
    c._access_token_exp = 9_999_999_999
    return c


async def test_get_json_attaches_bearer_for_aisandbox():
    page = _FakePage(_FakeResp(200, '{"sceneWorkflows": []}'))
    c = _client_with(page)
    data = await c._get_json(
        "https://aisandbox-pa.googleapis.com/v1/flow/scene/s1/workflows",
        route_name="getSceneWorkflows",
    )
    assert data == {"sceneWorkflows": []}
    _, _, kw = page.request.calls[-1]
    assert kw["headers"]["authorization"] == "Bearer ya29.test"


async def test_commit_workflow_sends_primary_media_id_patch():
    import json as _json

    page = _FakePage(_FakeResp(200, "{}"))
    c = _client_with(page)
    await c.commit_workflow("wf-1", project_id="proj-1", primary_media_id="media-9")
    method, url, kw = page.request.calls[-1]
    assert method == "PATCH"
    assert url.endswith("/v1/flowWorkflows/wf-1")
    body = _json.loads(kw["data"])
    assert body["updateMask"] == "metadata.primaryMediaId"
    assert body["workflow"]["metadata"]["primaryMediaId"] == "media-9"
    assert body["workflow"]["projectId"] == "proj-1"


async def test_create_scene_posts_ordered_workflow_ids():
    import json as _json

    resp_text = (
        '{"scene": {"sceneId": "scene-x"}, "sceneWorkflows": ['
        '{"workflow": {"name": "inst-1"}, "sceneWorkflowMetadata": '
        '{"position": 0, "startTime": "0s", "endTime": "8s", "totalDuration": "8s"}}]}'
    )
    page = _FakePage(_FakeResp(200, resp_text))
    c = _client_with(page)
    scene = await c.create_scene(project_id="proj-1", workflow_ids=["wf-a", "wf-a", "wf-b"])
    method, url, kw = page.request.calls[-1]
    assert method == "POST" and url.endswith("/projects/proj-1/scenes")
    assert _json.loads(kw["data"])["workflowIds"] == ["wf-a", "wf-a", "wf-b"]
    assert scene.scene_id == "scene-x"
    assert scene.project_id == "proj-1"


async def test_update_scene_workflows_sends_trims_and_order():
    import json as _json

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
    body = _json.loads(kw["data"])
    assert body["sceneId"] == "scene-x" and body["projectId"] == "proj-1"
    assert body["sceneWorkflows"][1]["sceneWorkflowMetadata"]["startTime"] == "3.200000000s"
    assert body["sceneWorkflows"][1]["workflow"]["name"] == "inst-2"


async def test_get_scene_workflows_reads_back_sorted():
    resp_text = (
        '{"sceneWorkflows": ['
        '{"workflow": {"name": "inst-2"}, "sceneWorkflowMetadata": '
        '{"position": 1, "startTime": "3.2s", "endTime": "5.2s", "totalDuration": "8s"}},'
        '{"workflow": {"name": "inst-1"}, "sceneWorkflowMetadata": '
        '{"startTime": "0s", "endTime": "8s", "totalDuration": "8s"}}]}'
    )
    page = _FakePage(_FakeResp(200, resp_text))
    c = _client_with(page)
    scene = await c.get_scene_workflows("scene-x", project_id="proj-1")
    method, url, _ = page.request.calls[-1]
    # read-back requires BOTH sceneId + projectId query params (else Flow returns {})
    assert method == "GET"
    assert url.endswith("/scene/scene-x/workflows?sceneId=scene-x&projectId=proj-1")
    assert [w.workflow_id for w in scene.workflows] == ["inst-1", "inst-2"]
    assert scene.workflows[1].metadata.start_time == 3.2


# --- concatenate_scene (server-side extended-video render) ------------------

import base64 as _base64
from types import SimpleNamespace

import pytest

from gflow_cli.api.scene import ConcatInput
from gflow_cli.errors import SceneConcatError, TransportTimeoutError

# Minimal valid MP4 head: bytes[4:8] == b"ftyp".
_FAKE_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00mp42"
_FAKE_MP4_B64 = _base64.b64encode(_FAKE_MP4).decode()


class _SeqRequest:
    """Returns queued responses in order (concat -> poll(s) -> success)."""

    def __init__(self, resps):
        self._resps = list(resps)
        self.calls = []

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._resps.pop(0)


class _SeqPage:
    def __init__(self, resps):
        self.request = _SeqRequest(resps)


def _client_seq(resps, tmp_path):
    c = FlowApiClient.__new__(FlowApiClient)
    c._page = _SeqPage(resps)
    c._page_queue = None
    c._context = None
    c._access_token = "ya29.test"
    c._access_token_exp = 9_999_999_999
    c.settings = SimpleNamespace(storage_uri=None, output_dir=tmp_path)
    return c


_INPUTS = [ConcatInput(media_id="m1", length=8.0, start=0.0, end=8.0)]


async def test_concatenate_scene_writes_extended_mp4(tmp_path):
    resps = [
        _FakeResp(200, '{"operation": {"operation": {"name": "jobs/j1"}}}'),
        _FakeResp(200, '{"status": "MEDIA_GENERATION_STATUS_ACTIVE"}'),
        _FakeResp(200, f'{{"status": "MEDIA_GENERATION_STATUS_SUCCESSFUL", "encodedVideo": "{_FAKE_MP4_B64}"}}'),
    ]
    c = _client_seq(resps, tmp_path)
    out = tmp_path / "extended.mp4"
    target = await c.concatenate_scene(_INPUTS, out_path=out, poll_interval=0)
    assert str(target) == str(out)
    assert out.read_bytes() == _FAKE_MP4
    # concat POST first, then 2 status polls
    urls = [u for _, u, _ in c._page.request.calls]
    assert urls[0].endswith(":runVideoFxConcatenation")
    assert all(u.endswith(":runVideoFxCheckConcatenationStatus") for u in urls[1:])


async def test_concatenate_scene_raises_on_failed_status(tmp_path):
    resps = [
        _FakeResp(200, '{"operation": {"operation": {"name": "jobs/j1"}}}'),
        _FakeResp(200, '{"status": "MEDIA_GENERATION_STATUS_FAILED"}'),
    ]
    c = _client_seq(resps, tmp_path)
    with pytest.raises(SceneConcatError):
        await c.concatenate_scene(_INPUTS, out_path=tmp_path / "x.mp4", poll_interval=0)


async def test_concatenate_scene_times_out(tmp_path):
    resps = [
        _FakeResp(200, '{"operation": {"operation": {"name": "jobs/j1"}}}'),
        _FakeResp(200, '{"status": "MEDIA_GENERATION_STATUS_ACTIVE"}'),
    ]
    c = _client_seq(resps, tmp_path)
    with pytest.raises(TransportTimeoutError):
        await c.concatenate_scene(_INPUTS, out_path=tmp_path / "x.mp4", poll_interval=0, timeout_s=0)


async def test_concatenate_scene_rejects_non_mp4(tmp_path):
    not_mp4 = _base64.b64encode(b"\x00\x00\x00\x18NOPEnope1234").decode()
    resps = [
        _FakeResp(200, '{"operation": {"operation": {"name": "jobs/j1"}}}'),
        _FakeResp(200, f'{{"status": "MEDIA_GENERATION_STATUS_SUCCESSFUL", "encodedVideo": "{not_mp4}"}}'),
    ]
    c = _client_seq(resps, tmp_path)
    with pytest.raises(SceneConcatError):
        await c.concatenate_scene(_INPUTS, out_path=tmp_path / "x.mp4", poll_interval=0)
