"""generate_video() — body assembly + reCAPTCHA + response parsing."""

from __future__ import annotations

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


class TestGenerateVideo:
    async def test_t2v_posts_expected_body(self, client: FlowApiClient) -> None:
        captured: dict = {}

        async def fake_post_json(url, body, **kwargs):
            captured["url"] = url
            captured["body"] = body
            return {
                "operations": [{"operation": {"name": "op-1"}, "status": "PENDING"}],
                "media": [{"name": "media-1", "projectId": "proj-1", "workflowId": "wf-1"}],
                "workflows": [{"name": "wf-1", "projectId": "proj-1"}],
            }

        with (
            patch.object(client, "_post_json", side_effect=fake_post_json),
            patch("gflow_cli.api.client.TokenMinter") as minter_cls,
        ):
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

        async def fake_post_json(url, body, **kwargs):
            captured["body"] = body
            return {
                "operations": [{"operation": {"name": "op"}, "status": "PENDING"}],
                "media": [{"name": "m", "projectId": "p", "workflowId": "w"}],
                "workflows": [{"name": "w", "projectId": "p"}],
            }

        with (
            patch.object(client, "_post_json", side_effect=fake_post_json),
            patch("gflow_cli.api.client.TokenMinter") as minter_cls,
        ):
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            req = GenerateVideoRequest(
                prompt="push in", aspect=Aspect.PORTRAIT, start_asset_uuid="asset-9"
            )
            await client.generate_video(project_id="p", req=req, seed=1)

        assert captured["body"]["requests"][0]["videoModelKey"] == "veo_3_1_i2v_fast_portrait"
        assert captured["body"]["requests"][0]["imageInput"]["mediaId"] == "asset-9"
