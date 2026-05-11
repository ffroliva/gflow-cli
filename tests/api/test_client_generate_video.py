"""generate_video() — body assembly + reCAPTCHA + response parsing.

Post-fixup (spec C2 re-loop): generate_video owns its own retry+mint closure
rather than routing through ``_post_json`` — these tests intercept at the
``page.request.post`` layer so they exercise the real code path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.video import Aspect, GenerateVideoRequest


@pytest.fixture
def client(tmp_path: Path) -> FlowApiClient:
    c = FlowApiClient(profile_dir=tmp_path / "prof")
    # Provide a fake page so self.page doesn't raise outside async-with.
    # TokenMinter is patched in every test so the page object is never used.
    c._page = MagicMock()
    return c


def _ok_video_response(
    media: str = "media-1", op: str = "op-1", proj: str = "proj-1", wf: str = "wf-1"
) -> dict:
    return {
        "operations": [{"operation": {"name": op}, "status": "PENDING"}],
        "media": [{"name": media, "projectId": proj, "workflowId": wf}],
        "workflows": [{"name": wf, "projectId": proj}],
    }


def _intercepting_post(captured: dict, response: dict):
    """Build an ``async def`` mock for ``page.request.post`` that returns 200
    with ``response`` and captures the call's url + body."""
    payload = json.dumps(response)

    async def fake_request_post(url, *, data, headers):
        captured["url"] = url
        captured["body"] = json.loads(data)
        captured["headers"] = headers
        resp = MagicMock()
        resp.status = 200
        resp.headers = {"content-type": "application/json"}
        resp.text = AsyncMock(return_value=payload)
        return resp

    return fake_request_post


class TestGenerateVideo:
    async def test_t2v_posts_expected_body(self, client: FlowApiClient) -> None:
        captured: dict = {}
        client._page.request.post = AsyncMock(
            side_effect=_intercepting_post(captured, _ok_video_response())
        )

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="TOKEN-X")
            req = GenerateVideoRequest(prompt="cat", aspect=Aspect.PORTRAIT)
            op = await client.generate_video(project_id="proj-1", req=req, seed=42)

        assert "video:batchAsyncGenerateVideoText" in captured["url"]
        body = captured["body"]
        assert body["clientContext"]["projectId"] == "proj-1"
        assert body["clientContext"]["recaptchaContext"]["token"] == "TOKEN-X"
        assert body["requests"][0]["videoModelKey"] == "veo_3_1_t2v_fast_portrait"
        assert body["requests"][0]["seed"] == 42
        assert op.media_name == "media-1"
        assert op.project_id == "proj-1"
        assert op.operation_name == "op-1"
        assert op.workflow_id == "wf-1"

    async def test_i2v_includes_image_input(self, client: FlowApiClient) -> None:
        captured: dict = {}
        resp = _ok_video_response(media="m", op="op", proj="p", wf="w")
        client._page.request.post = AsyncMock(side_effect=_intercepting_post(captured, resp))

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            req = GenerateVideoRequest(
                prompt="push in", aspect=Aspect.PORTRAIT, start_asset_uuid="asset-9"
            )
            await client.generate_video(project_id="p", req=req, seed=1)

        assert captured["body"]["requests"][0]["videoModelKey"] == "veo_3_1_i2v_fast_portrait"
        assert captured["body"]["requests"][0]["imageInput"]["mediaId"] == "asset-9"
