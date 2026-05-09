"""generate_image() — body assembly + reCAPTCHA + response parsing.

Mirrors the mocking style of `tests/api/test_client_generate_video.py`.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flow_cli.api.client import FlowApiClient, FlowApiError
from flow_cli.api.dto import GeneratedImage
from flow_cli.api.image import Aspect, GenerateImageRequest

# Realistic mock response distilled from samples/captured/06_batchGenerateImages.json
_FAKE_FIFE_URL = "https://flow-content.google/image/abc-123?Expires=1778380305&Signature=XYZ"
_FAKE_RESPONSE: dict = {
    "media": [
        {
            "name": "media-uuid-1",
            "workflowId": "wf-uuid-1",
            "image": {
                "generatedImage": {
                    "seed": 646428,
                    "prompt": "a warrior zelda in a dangeon. cinematic.",
                    "modelNameType": "NARWHAL",
                    "aspectRatio": "IMAGE_ASPECT_RATIO_PORTRAIT",
                    "fifeUrl": _FAKE_FIFE_URL,
                    "workflowId": "wf-uuid-1",
                },
                "dimensions": {"width": 768, "height": 1376},
            },
        }
    ],
    "workflows": [{"name": "wf-uuid-1", "projectId": "proj-1"}],
}


@pytest.fixture
def client(tmp_path: Path) -> FlowApiClient:
    c = FlowApiClient(profile_dir=tmp_path / "prof")
    # Provide a fake page so self.page doesn't raise outside async-with.
    # TokenMinter is patched in every test so the page object is never used.
    c._page = MagicMock()
    return c


def _make_req() -> GenerateImageRequest:
    return GenerateImageRequest(prompt="a warrior", aspect=Aspect.PORTRAIT)


class TestGenerateImage:
    async def test_generate_image_posts_to_correct_url(self, client: FlowApiClient) -> None:
        captured: dict = {}

        async def fake_post_json(url, body, **kwargs):
            captured["url"] = url
            captured["body"] = body
            return _FAKE_RESPONSE

        with (
            patch.object(client, "_post_json", side_effect=fake_post_json),
            patch("flow_cli.api.client.TokenMinter") as minter_cls,
        ):
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            await client.generate_image(project_id="proj-1", req=_make_req(), seed=42)

        assert "/projects/proj-1/flowMedia:batchGenerateImages" in captured["url"]

    async def test_generate_image_uses_text_plain_content_type(self, client: FlowApiClient) -> None:
        # `_post_json` defaults to text/plain;charset=UTF-8 — assert that
        # generate_image() goes through that helper without overriding it.
        seen_request: dict = {}

        async def fake_request_post(url, *, data, headers):
            seen_request["headers"] = headers
            resp = MagicMock()
            resp.status = 200
            resp.text = AsyncMock(return_value=json.dumps(_FAKE_RESPONSE))
            return resp

        # Bypass _post_json's mock-out so we exercise the real header path.
        client._page.request.post = AsyncMock(side_effect=fake_request_post)

        with patch("flow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            # Use a valid single-item response so the call completes normally
            # and we can isolate the header assertion.
            await client.generate_image(project_id="proj-1", req=_make_req(), seed=1)

        assert seen_request["headers"]["content-type"] == "text/plain;charset=UTF-8"

    async def test_generate_image_raises_flow_api_error_on_empty_media(
        self, client: FlowApiClient
    ) -> None:
        """200 OK with empty media[] (e.g. content-policy rejection) must
        surface as FlowApiError, not a bare IndexError."""

        async def fake_request_post(url, *, data, headers):
            resp = MagicMock()
            resp.status = 200
            resp.text = AsyncMock(return_value='{"media":[],"workflows":[]}')
            return resp

        client._page.request.post = AsyncMock(side_effect=fake_request_post)

        with patch("flow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            with pytest.raises(FlowApiError) as exc_info:
                await client.generate_image(project_id="proj-1", req=_make_req(), seed=1)

        assert exc_info.value.status == 200
        assert "/projects/proj-1/flowMedia:batchGenerateImages" in exc_info.value.route

    async def test_generate_image_mints_recaptcha_token(self, client: FlowApiClient) -> None:
        async def fake_post_json(url, body, **kwargs):
            return _FAKE_RESPONSE

        with (
            patch.object(client, "_post_json", side_effect=fake_post_json),
            patch("flow_cli.api.client.TokenMinter") as minter_cls,
        ):
            mint_mock = AsyncMock(return_value="TOK-Z")
            minter_cls.return_value.mint = mint_mock
            await client.generate_image(project_id="proj-1", req=_make_req(), seed=42)

        mint_mock.assert_awaited_once_with("imageGeneration")

    async def test_generate_image_returns_generated_image(self, client: FlowApiClient) -> None:
        async def fake_post_json(url, body, **kwargs):
            return _FAKE_RESPONSE

        with (
            patch.object(client, "_post_json", side_effect=fake_post_json),
            patch("flow_cli.api.client.TokenMinter") as minter_cls,
        ):
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            result = await client.generate_image(project_id="proj-1", req=_make_req(), seed=42)

        assert isinstance(result, GeneratedImage)
        assert result.fife_url == _FAKE_FIFE_URL
        assert result.media_name == "media-uuid-1"
        assert result.seed == 646428
        assert result.dimensions == (768, 1376)

    async def test_generate_image_propagates_flow_api_error_on_4xx(
        self, client: FlowApiClient
    ) -> None:
        async def fake_request_post(url, *, data, headers):
            resp = MagicMock()
            resp.status = 400
            resp.text = AsyncMock(return_value="bad request")
            return resp

        client._page.request.post = AsyncMock(side_effect=fake_request_post)

        with patch("flow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            with pytest.raises(FlowApiError) as exc_info:
                await client.generate_image(project_id="proj-1", req=_make_req(), seed=1)

        assert "/projects/proj-1/flowMedia:batchGenerateImages" in exc_info.value.route
        assert exc_info.value.status == 400

    async def test_generate_image_idempotent_body_modulo_recaptcha(
        self, client: FlowApiClient
    ) -> None:
        """Same seed → identical body except for the recaptcha token."""
        captured_bodies: list[dict] = []

        async def fake_post_json(url, body, **kwargs):
            captured_bodies.append(body)
            return _FAKE_RESPONSE

        with (
            patch.object(client, "_post_json", side_effect=fake_post_json),
            patch("flow_cli.api.client.TokenMinter") as minter_cls,
        ):
            minter_cls.return_value.mint = AsyncMock(side_effect=["TOK-A", "TOK-B"])
            await client.generate_image(
                project_id="proj-1", req=_make_req(), seed=42, batch_id="batch-1"
            )
            await client.generate_image(
                project_id="proj-1", req=_make_req(), seed=42, batch_id="batch-1"
            )

        b0, b1 = captured_bodies

        # Recaptcha token differs.
        t0 = b0["clientContext"]["recaptchaContext"]["token"]
        t1 = b1["clientContext"]["recaptchaContext"]["token"]
        assert t0 != t1

        # Strip recaptcha tokens from both bodies — the rest must be identical.
        def _strip_tokens(b: dict) -> dict:
            d = copy.deepcopy(b)
            d["clientContext"]["recaptchaContext"]["token"] = "X"
            d["requests"][0]["clientContext"]["recaptchaContext"]["token"] = "X"
            return d

        assert _strip_tokens(b0) == _strip_tokens(b1)


def _fake_response_with_seed(seed: int, media_id: str = "media-uuid-x") -> dict:
    """Build a fake response distinct per call so we can verify ordering."""
    return {
        "media": [
            {
                "name": media_id,
                "workflowId": f"wf-{seed}",
                "image": {
                    "generatedImage": {
                        "seed": seed,
                        "prompt": "a warrior",
                        "modelNameType": "NARWHAL",
                        "aspectRatio": "IMAGE_ASPECT_RATIO_PORTRAIT",
                        "fifeUrl": f"https://flow-content.google/image/{seed}",
                        "workflowId": f"wf-{seed}",
                    },
                    "dimensions": {"width": 768, "height": 1376},
                },
            }
        ],
        "workflows": [{"name": f"wf-{seed}", "projectId": "proj-1"}],
    }


class TestGenerateImagesBatch:
    async def test_batch_fan_out_uses_shared_batch_id(self, client: FlowApiClient) -> None:
        """All N parallel POSTs must share one batchId."""
        captured_bodies: list[dict] = []

        async def fake_post_json(url, body, **kwargs):
            captured_bodies.append(body)
            seed = body["requests"][0]["seed"]
            return _fake_response_with_seed(seed)

        with (
            patch.object(client, "_post_json", side_effect=fake_post_json),
            patch("flow_cli.api.client.TokenMinter") as minter_cls,
        ):
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=3)

        assert len(captured_bodies) == 3
        batch_ids = {b["mediaGenerationContext"]["batchId"] for b in captured_bodies}
        assert len(batch_ids) == 1, f"expected one shared batchId, got {batch_ids}"

    async def test_batch_fan_out_uses_distinct_seeds(self, client: FlowApiClient) -> None:
        """When seeds=None, the N bodies have N different seeds."""
        captured_bodies: list[dict] = []

        async def fake_post_json(url, body, **kwargs):
            captured_bodies.append(body)
            seed = body["requests"][0]["seed"]
            return _fake_response_with_seed(seed)

        with (
            patch.object(client, "_post_json", side_effect=fake_post_json),
            patch("flow_cli.api.client.TokenMinter") as minter_cls,
        ):
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=3)

        seeds = {b["requests"][0]["seed"] for b in captured_bodies}
        assert len(seeds) == 3, f"expected 3 distinct seeds, got {seeds}"

    async def test_batch_fan_out_calls_token_minter_n_times(self, client: FlowApiClient) -> None:
        """Single-use reCAPTCHA tokens — mint() called once per request."""

        async def fake_post_json(url, body, **kwargs):
            seed = body["requests"][0]["seed"]
            return _fake_response_with_seed(seed)

        with (
            patch.object(client, "_post_json", side_effect=fake_post_json),
            patch("flow_cli.api.client.TokenMinter") as minter_cls,
        ):
            mint_mock = AsyncMock(side_effect=["TOK-1", "TOK-2", "TOK-3"])
            minter_cls.return_value.mint = mint_mock
            await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=3)

        assert mint_mock.await_count == 3

    async def test_batch_fan_out_returns_in_input_order(self, client: FlowApiClient) -> None:
        """asyncio.gather may complete out of order — return list must match input seed order."""
        import asyncio

        # Make later calls finish FIRST by sleeping inversely proportional to seed.
        async def fake_post_json(url, body, **kwargs):
            seed = body["requests"][0]["seed"]
            # seed=100 sleeps longest, seed=300 finishes first
            delay = 0.03 if seed == 100 else (0.01 if seed == 200 else 0.0)
            await asyncio.sleep(delay)
            return _fake_response_with_seed(seed, media_id=f"media-{seed}")

        with (
            patch.object(client, "_post_json", side_effect=fake_post_json),
            patch("flow_cli.api.client.TokenMinter") as minter_cls,
        ):
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            results = await client.generate_images_batch(
                project_id="proj-1",
                req=_make_req(),
                count=3,
                seeds=[100, 200, 300],
            )

        assert [r.seed for r in results] == [100, 200, 300]
        assert [r.media_name for r in results] == ["media-100", "media-200", "media-300"]

    async def test_batch_fan_out_partial_failure_propagates(self, client: FlowApiClient) -> None:
        """One sibling raising FlowApiError -> whole batch raises."""
        call_count = {"n": 0}

        async def fake_post_json(url, body, **kwargs):
            call_count["n"] += 1
            seed = body["requests"][0]["seed"]
            if seed == 200:
                raise FlowApiError(429, "rate limited", route=url)
            return _fake_response_with_seed(seed)

        with (
            patch.object(client, "_post_json", side_effect=fake_post_json),
            patch("flow_cli.api.client.TokenMinter") as minter_cls,
        ):
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            with pytest.raises(FlowApiError) as exc_info:
                await client.generate_images_batch(
                    project_id="proj-1",
                    req=_make_req(),
                    count=3,
                    seeds=[100, 200, 300],
                )

        assert exc_info.value.status == 429

    async def test_batch_fan_out_count_must_be_1_to_4(self, client: FlowApiClient) -> None:
        """count=0 and count=5 must raise ValueError. Matches Flow UI."""
        with patch("flow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            with pytest.raises(ValueError):
                await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=0)
            with pytest.raises(ValueError):
                await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=5)
