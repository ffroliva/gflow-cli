"""generate_image() — body assembly + reCAPTCHA + response parsing.

Mirrors the mocking style of `tests/api/test_client_generate_video.py`.
"""

from __future__ import annotations

import asyncio
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

        # Strip recaptcha tokens AND sessionId (millisecond-based) from both bodies
        # — the rest must be identical.
        def _strip_tokens(b: dict) -> dict:
            d = copy.deepcopy(b)
            d["clientContext"]["recaptchaContext"]["token"] = "X"
            d["clientContext"]["sessionId"] = "S"
            d["requests"][0]["clientContext"]["recaptchaContext"]["token"] = "X"
            d["requests"][0]["clientContext"]["sessionId"] = "S"
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
        """asyncio.gather may complete out of order — return list must match input seed order.

        Two assertions together prove the contract:

        1. ``completion_log`` is NOT in submission order (seed=300 finishes first
           because seed=100 sleeps longest) — proves real interleaving happened
           and a sequential ``for`` loop wouldn't pass.
        2. ``results`` IS in submission order — proves ``asyncio.gather``
           preserves input order despite out-of-order completion.
        """
        completion_log: list[int] = []

        # Make later calls finish FIRST by sleeping inversely proportional to seed.
        async def fake_post_json(url, body, **kwargs):
            seed = body["requests"][0]["seed"]
            # seed=100 sleeps longest, seed=300 finishes first
            delay = 0.03 if seed == 100 else (0.01 if seed == 200 else 0.0)
            await asyncio.sleep(delay)
            completion_log.append(seed)
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

        # Out-of-order completion actually occurred — a sequential for-loop
        # implementation would log [100, 200, 300] and fail this assertion.
        submission_order = [100, 200, 300]
        assert completion_log != submission_order, (
            f"expected interleaved completion, got submission-order log {completion_log}"
        )
        # gather() preserves input order despite the out-of-order completion above.
        assert [r.seed for r in results] == submission_order
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
            with pytest.raises(ValueError, match="count must be between 1 and 4"):
                await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=0)
            with pytest.raises(ValueError, match="count must be between 1 and 4"):
                await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=5)

    @pytest.mark.parametrize(
        "seeds,count",
        [
            ([1, 2], 3),
            ([1, 2, 3, 4], 2),
        ],
    )
    async def test_batch_seeds_count_mismatch_raises(
        self, client: FlowApiClient, seeds: list[int], count: int
    ) -> None:
        """If caller supplies ``seeds``, its length must equal ``count``."""
        with patch("flow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            with pytest.raises(ValueError, match="does not match count"):
                await client.generate_images_batch(
                    project_id="proj-1",
                    req=_make_req(),
                    count=count,
                    seeds=seeds,
                )


def _make_image(fife_url: str = _FAKE_FIFE_URL) -> GeneratedImage:
    """Build a minimal GeneratedImage for download_image tests."""
    return GeneratedImage(
        media_name="media-uuid-1",
        workflow_id="wf-uuid-1",
        seed=42,
        prompt="a warrior",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url=fife_url,
        dimensions=(768, 1376),
    )


class TestDownloadImage:
    async def test_download_image_writes_bytes_to_path(
        self, client: FlowApiClient, tmp_path: Path
    ) -> None:
        """Bytes returned by page.request.get land on disk at out_path."""
        payload = b"\x89PNG\r\n\x1a\nfake-image-bytes"

        async def fake_request_get(url, **kwargs):
            resp = MagicMock()
            resp.status = 200
            resp.body = AsyncMock(return_value=payload)
            return resp

        client._page.request.get = AsyncMock(side_effect=fake_request_get)

        out_path = tmp_path / "out.png"
        result = await client.download_image(_make_image(), out_path)

        assert result == out_path
        assert out_path.read_bytes() == payload

    async def test_download_image_creates_parent_dirs(
        self, client: FlowApiClient, tmp_path: Path
    ) -> None:
        """When out_path.parent doesn't exist, it's created."""
        payload = b"image-bytes"

        async def fake_request_get(url, **kwargs):
            resp = MagicMock()
            resp.status = 200
            resp.body = AsyncMock(return_value=payload)
            return resp

        client._page.request.get = AsyncMock(side_effect=fake_request_get)

        out_path = tmp_path / "deep" / "nested" / "dir" / "out.png"
        assert not out_path.parent.exists()

        result = await client.download_image(_make_image(), out_path)

        assert result == out_path
        assert out_path.parent.is_dir()
        assert out_path.read_bytes() == payload

    async def test_download_image_does_not_use_redirect_helper(
        self, client: FlowApiClient, tmp_path: Path
    ) -> None:
        """The URL passed to page.request.get is the raw fife_url, NOT
        the labs.google redirect helper. fife_url is already a fully
        signed CDN URL."""
        captured: dict = {}

        async def fake_request_get(url, **kwargs):
            captured["url"] = url
            resp = MagicMock()
            resp.status = 200
            resp.body = AsyncMock(return_value=b"x")
            return resp

        client._page.request.get = AsyncMock(side_effect=fake_request_get)

        signed = "https://flow-content.google/image/abc-123?Expires=1778380305&Signature=XYZ"
        image = _make_image(fife_url=signed)

        with patch("flow_cli.api.client.routes") as mock_routes:
            await client.download_image(image, tmp_path / "out.png")
            # The redirect helper must NOT be involved.
            mock_routes.media_download_url.assert_not_called()

        assert captured["url"] == signed

    async def test_download_image_raises_on_4xx(
        self, client: FlowApiClient, tmp_path: Path
    ) -> None:
        """A 4xx response surfaces as FlowApiError, like the existing
        download() method does."""

        async def fake_request_get(url, **kwargs):
            resp = MagicMock()
            resp.status = 403
            resp.text = AsyncMock(return_value="signed url expired")
            resp.body = AsyncMock(return_value=b"")
            return resp

        client._page.request.get = AsyncMock(side_effect=fake_request_get)

        with pytest.raises(FlowApiError) as exc_info:
            await client.download_image(_make_image(), tmp_path / "out.png")

        assert exc_info.value.status == 403

    async def test_download_image_error_route_redacts_signed_url_query(
        self, client: FlowApiClient, tmp_path: Path
    ) -> None:
        """The bearer-style ``Signature=...`` token in fife_url MUST NOT
        leak into FlowApiError.route or str(exc). See docs/SECURITY.md."""

        async def fake_request_get(url, **kwargs):
            resp = MagicMock()
            resp.status = 403
            resp.text = AsyncMock(return_value="forbidden")
            resp.body = AsyncMock(return_value=b"")
            return resp

        client._page.request.get = AsyncMock(side_effect=fake_request_get)

        signed = (
            "https://flow-content.google/image/abc-123"
            "?Expires=1778380305&Signature=DEADBEEFSECRET&KeyPair=KP"
        )
        with pytest.raises(FlowApiError) as exc_info:
            await client.download_image(_make_image(fife_url=signed), tmp_path / "out.png")

        # The exception's structured route must not carry sensitive query bits.
        assert "Signature=" not in exc_info.value.route
        assert "Expires=" not in exc_info.value.route
        assert "DEADBEEFSECRET" not in exc_info.value.route
        # Same applies to its rendered string form (used by logging).
        rendered = str(exc_info.value)
        assert "Signature=" not in rendered
        assert "DEADBEEFSECRET" not in rendered
        # Sanity: the path is preserved so debugging is still possible.
        assert "/image/abc-123" in exc_info.value.route

    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://flow-content.google/image/abc",  # http scheme
            "ftp://flow-content.google/image/abc",  # non-http scheme
            "https://localhost/image/abc",  # localhost
            "https://127.0.0.1/image/abc",  # loopback v4
            "https://169.254.169.254/latest/meta-data/",  # AWS IMDS
            "https://evil.com/image/abc",  # arbitrary host
            "https://flow-content.google.evil.com/image/abc",  # confusable suffix
        ],
    )
    async def test_download_image_rejects_untrusted_url(
        self, client: FlowApiClient, tmp_path: Path, bad_url: str
    ) -> None:
        """SSRF guard: any URL that is not HTTPS-on-Google must raise
        ValueError BEFORE any network call is attempted."""
        get_mock = AsyncMock()
        client._page.request.get = get_mock

        with pytest.raises(ValueError):
            await client.download_image(_make_image(fife_url=bad_url), tmp_path / "out.png")

        # Crucially, no request was issued — the guard fires pre-flight.
        get_mock.assert_not_called()

    @pytest.mark.parametrize(
        "good_url",
        [
            "https://flow-content.google/image/abc-123?Expires=1&Signature=Z",
            "https://cdn.flow-content.google/image/abc",  # subdomain of .google
            "https://lh3.googleusercontent.google/x",  # any *.google subdomain
        ],
    )
    async def test_download_image_accepts_google_https_hosts(
        self, client: FlowApiClient, tmp_path: Path, good_url: str
    ) -> None:
        """Happy path: HTTPS on flow-content.google or any *.google host
        is allowed through the SSRF guard."""

        async def fake_request_get(url, **kwargs):
            resp = MagicMock()
            resp.status = 200
            resp.body = AsyncMock(return_value=b"png")
            return resp

        client._page.request.get = AsyncMock(side_effect=fake_request_get)
        out = await client.download_image(_make_image(fife_url=good_url), tmp_path / "out.png")
        assert out.read_bytes() == b"png"
