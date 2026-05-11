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

from gflow_cli.api.client import FlowApiClient, FlowApiError
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import Aspect, GenerateImageRequest
from gflow_cli.errors import ContentPolicyError, WireFormatError

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


def _fake_ok_post(captured: dict | None = None, response: dict | None = None):
    """Build an ``async def`` mock for ``page.request.post`` that returns 200
    with ``response`` (or ``_FAKE_RESPONSE``) and captures the call kwargs."""
    payload = json.dumps(response if response is not None else _FAKE_RESPONSE)

    async def fake_request_post(url, *, data, headers):
        if captured is not None:
            captured["url"] = url
            captured["body"] = json.loads(data)
            captured["headers"] = headers
        resp = MagicMock()
        resp.status = 200
        resp.text = AsyncMock(return_value=payload)
        resp.headers = {"content-type": "application/json"}
        return resp

    return fake_request_post


class TestGenerateImage:
    async def test_generate_image_posts_to_correct_url(self, client: FlowApiClient) -> None:
        captured: dict = {}
        client._page.request.post = AsyncMock(side_effect=_fake_ok_post(captured))

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
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

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            # Use a valid single-item response so the call completes normally
            # and we can isolate the header assertion.
            await client.generate_image(project_id="proj-1", req=_make_req(), seed=1)

        assert seen_request["headers"]["content-type"] == "text/plain;charset=UTF-8"

    async def test_generate_image_raises_content_policy_on_empty_media(
        self, client: FlowApiClient
    ) -> None:
        """200 OK with empty media[] (silent content-policy rejection) must
        surface as ContentPolicyError, not a bare IndexError.

        Phase 4 T3 contract: ContentPolicyError extends FlowApiError (so
        ``except FlowApiError`` still catches it for back-compat) but its
        ``status`` is intentionally stripped per RFC 9457 (no 2xx on errors).
        The literal upstream 200 is recorded only via observability as
        ``upstream_status``.
        """

        async def fake_request_post(url, *, data, headers):
            resp = MagicMock()
            resp.status = 200
            resp.text = AsyncMock(return_value='{"media":[],"workflows":[]}')
            return resp

        client._page.request.post = AsyncMock(side_effect=fake_request_post)

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            with pytest.raises(ContentPolicyError) as exc_info:
                await client.generate_image(project_id="proj-1", req=_make_req(), seed=1)

        # ContentPolicyError IS a FlowApiError — back-compat preserved.
        assert isinstance(exc_info.value, FlowApiError)
        # RFC 9457: status not carried for 2xx-success-with-empty-media case.
        assert exc_info.value.to_problem_details().get("status") is None
        assert "/projects/proj-1/flowMedia:batchGenerateImages" in exc_info.value.route

    async def test_generate_image_mints_recaptcha_token(self, client: FlowApiClient) -> None:
        client._page.request.post = AsyncMock(side_effect=_fake_ok_post())

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
            mint_mock = AsyncMock(return_value="TOK-Z")
            minter_cls.return_value.mint = mint_mock
            await client.generate_image(project_id="proj-1", req=_make_req(), seed=42)

        mint_mock.assert_awaited_once_with("imageGeneration")

    async def test_generate_image_returns_generated_image(self, client: FlowApiClient) -> None:
        client._page.request.post = AsyncMock(side_effect=_fake_ok_post())

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
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

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
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
        payload = json.dumps(_FAKE_RESPONSE)

        async def fake_request_post(url, *, data, headers):
            captured_bodies.append(json.loads(data))
            resp = MagicMock()
            resp.status = 200
            resp.text = AsyncMock(return_value=payload)
            resp.headers = {"content-type": "application/json"}
            return resp

        client._page.request.post = AsyncMock(side_effect=fake_request_post)

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
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


def _seed_dispatch_post(
    captured_bodies: list[dict],
    delays: dict[int, float] | None = None,
    fail_on_seed: int | None = None,
):
    """Build an ``async def`` mock for ``page.request.post`` that responds with
    a per-seed fake response. Optional ``delays`` and ``fail_on_seed`` let
    individual tests assert interleaving / partial-failure behavior."""

    async def fake_request_post(url, *, data, headers):
        body = json.loads(data)
        captured_bodies.append(body)
        seed = body["requests"][0]["seed"]
        if delays and seed in delays:
            await asyncio.sleep(delays[seed])
        if fail_on_seed is not None and seed == fail_on_seed:
            resp = MagicMock()
            resp.status = 429
            resp.headers = {"content-type": "application/json"}
            resp.text = AsyncMock(return_value="rate limited")
            return resp
        media_id = f"media-{seed}"
        resp = MagicMock()
        resp.status = 200
        resp.headers = {"content-type": "application/json"}
        resp.text = AsyncMock(return_value=json.dumps(_fake_response_with_seed(seed, media_id)))
        return resp

    return fake_request_post


class TestGenerateImagesBatch:
    async def test_batch_fan_out_uses_shared_batch_id(self, client: FlowApiClient) -> None:
        """All N parallel POSTs must share one batchId."""
        captured_bodies: list[dict] = []
        client._page.request.post = AsyncMock(side_effect=_seed_dispatch_post(captured_bodies))

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=3)

        assert len(captured_bodies) == 3
        batch_ids = {b["mediaGenerationContext"]["batchId"] for b in captured_bodies}
        assert len(batch_ids) == 1, f"expected one shared batchId, got {batch_ids}"

    async def test_batch_fan_out_uses_distinct_seeds(self, client: FlowApiClient) -> None:
        """When seeds=None, the N bodies have N different seeds."""
        captured_bodies: list[dict] = []
        client._page.request.post = AsyncMock(side_effect=_seed_dispatch_post(captured_bodies))

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=3)

        seeds = {b["requests"][0]["seed"] for b in captured_bodies}
        assert len(seeds) == 3, f"expected 3 distinct seeds, got {seeds}"

    async def test_batch_fan_out_calls_token_minter_n_times(self, client: FlowApiClient) -> None:
        """Single-use reCAPTCHA tokens — mint() called once per request.

        Spec C2 post-fixup: with the retry+mint loop now inside each per-shot
        ``_drive_image_generation``, the count==N happy-path invariant still
        holds (one mint per successful attempt, no retries triggered here).
        """
        captured_bodies: list[dict] = []
        client._page.request.post = AsyncMock(side_effect=_seed_dispatch_post(captured_bodies))

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
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
        async def fake_request_post(url, *, data, headers):
            body = json.loads(data)
            seed = body["requests"][0]["seed"]
            delay = 0.03 if seed == 100 else (0.01 if seed == 200 else 0.0)
            await asyncio.sleep(delay)
            completion_log.append(seed)
            resp = MagicMock()
            resp.status = 200
            resp.headers = {"content-type": "application/json"}
            resp.text = AsyncMock(
                return_value=json.dumps(_fake_response_with_seed(seed, media_id=f"media-{seed}"))
            )
            return resp

        client._page.request.post = AsyncMock(side_effect=fake_request_post)

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
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
        """One sibling raising FlowApiError -> whole batch raises.

        With per-shot retry now in the loop, a sustained 429 attempt for
        seed=200 is retried 3x (all returning 429) before surfacing as
        :class:`gflow_cli.errors.RateLimitError` (which IS a FlowApiError).
        """
        captured_bodies: list[dict] = []
        client._page.request.post = AsyncMock(
            side_effect=_seed_dispatch_post(captured_bodies, fail_on_seed=200)
        )

        with (
            patch("gflow_cli.api.client.TokenMinter") as minter_cls,
            # Zero-wait so the 3x retry for the failing seed completes fast.
            patch("gflow_cli.api.client.post_with_retry") as patched_retry,
        ):
            from gflow_cli.api._retry import _make_retrying

            patched_retry.side_effect = lambda **_kw: _make_retrying(
                wait_seconds=lambda _: 0
            ).__aiter__()
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
        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
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
        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
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

        with patch("gflow_cli.api.client.routes") as mock_routes:
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


class TestSpecC2TokenReMint:
    """Spec C2: reCAPTCHA token is minted INSIDE the retry loop, EVERY attempt.

    These tests pin the behavior so a future refactor can't accidentally hoist
    the mint back outside the retry boundary (which is exactly the deviation
    the council audit caught in the T3 base commit).
    """

    async def test_recaptcha_token_re_minted_every_attempt(self, client: FlowApiClient) -> None:
        """503 → 503 → 200 across 3 attempts → 3 distinct mints with 3
        distinct tokens embedded in the 3 POST bodies."""
        captured_bodies: list[dict] = []
        responses_iter = iter(
            [
                # attempt 1: 503 (retryable)
                (503, "service unavailable"),
                # attempt 2: 503 (retryable)
                (503, "still unavailable"),
                # attempt 3: 200 with valid response
                (200, json.dumps(_FAKE_RESPONSE)),
            ]
        )

        async def fake_request_post(url, *, data, headers):
            captured_bodies.append(json.loads(data))
            status, text = next(responses_iter)
            resp = MagicMock()
            resp.status = status
            resp.text = AsyncMock(return_value=text)
            resp.headers = {"content-type": "application/json"}
            return resp

        client._page.request.post = AsyncMock(side_effect=fake_request_post)

        with (
            patch("gflow_cli.api.client.TokenMinter") as minter_cls,
            # Zero-wait so the test runs fast instead of incurring real
            # exponential backoff.
            patch("gflow_cli.api.client.post_with_retry") as patched_retry,
        ):
            from gflow_cli.api._retry import _make_retrying

            patched_retry.side_effect = lambda **_kw: _make_retrying(
                wait_seconds=lambda _: 0
            ).__aiter__()
            mint_mock = AsyncMock(side_effect=["T1", "T2", "T3"])
            minter_cls.return_value.mint = mint_mock
            await client.generate_image(project_id="proj-1", req=_make_req(), seed=42)

        # Three attempts, three mints, three distinct tokens in the wire bodies.
        assert mint_mock.await_count == 3
        tokens = [b["clientContext"]["recaptchaContext"]["token"] for b in captured_bodies]
        assert tokens == ["T1", "T2", "T3"], f"expected per-attempt fresh tokens, got {tokens}"

    async def test_generate_video_recaptcha_token_re_minted_every_attempt(
        self, client: FlowApiClient
    ) -> None:
        """Spec C2 for the video route too — symmetric to the image test above."""
        from gflow_cli.api.video import Aspect as VAspect
        from gflow_cli.api.video import GenerateVideoRequest

        captured_bodies: list[dict] = []
        fake_video_response = {
            "operations": [{"operation": {"name": "op-1"}, "status": "PENDING"}],
            "media": [{"name": "m1", "projectId": "proj-1", "workflowId": "w1"}],
            "workflows": [{"name": "w1", "projectId": "proj-1"}],
        }
        responses_iter = iter(
            [
                (503, "down"),
                (503, "still down"),
                (200, json.dumps(fake_video_response)),
            ]
        )

        async def fake_request_post(url, *, data, headers):
            captured_bodies.append(json.loads(data))
            status, text = next(responses_iter)
            resp = MagicMock()
            resp.status = status
            resp.text = AsyncMock(return_value=text)
            resp.headers = {"content-type": "application/json"}
            return resp

        client._page.request.post = AsyncMock(side_effect=fake_request_post)

        with (
            patch("gflow_cli.api.client.TokenMinter") as minter_cls,
            patch("gflow_cli.api.client.post_with_retry") as patched_retry,
        ):
            from gflow_cli.api._retry import _make_retrying

            patched_retry.side_effect = lambda **_kw: _make_retrying(
                wait_seconds=lambda _: 0
            ).__aiter__()
            mint_mock = AsyncMock(side_effect=["V1", "V2", "V3"])
            minter_cls.return_value.mint = mint_mock
            req = GenerateVideoRequest(prompt="cat", aspect=VAspect.PORTRAIT)
            await client.generate_video(project_id="proj-1", req=req, seed=1)

        assert mint_mock.await_count == 3
        tokens = [b["clientContext"]["recaptchaContext"]["token"] for b in captured_bodies]
        assert tokens == ["V1", "V2", "V3"]


class TestWireFormatDiscoveryAndRedaction:
    """Audit gaps #9 and #11: discovery payload completeness + redaction-before-prefix."""

    async def test_wire_format_error_full_discovery_on_4xx(self, client: FlowApiClient) -> None:
        """The 4xx fallthrough WireFormatError carries a complete RFC 9457
        ``discovery`` extension: route_name, http_status, content_type,
        top_level_keys (sorted), body_prefix_redacted."""

        async def fake_request_post(url, *, data, headers):
            resp = MagicMock()
            resp.status = 422
            resp.headers = {"content-type": "application/json"}
            resp.text = AsyncMock(
                return_value=json.dumps({"error": "bad_payload", "code": 422, "details": []})
            )
            return resp

        client._page.request.post = AsyncMock(side_effect=fake_request_post)

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="TOK")
            with pytest.raises(WireFormatError) as exc_info:
                await client.generate_image(project_id="proj-1", req=_make_req(), seed=1)

        discovery = exc_info.value.discovery
        assert isinstance(discovery, dict)
        assert discovery["http_status"] == 422
        assert discovery["content_type"] == "application/json"
        # SORTED top-level keys (json.dumps emits them in insertion order, but
        # _build_wire_format_discovery sorts before storing).
        assert discovery["top_level_keys"] == ["code", "details", "error"]
        assert "body_prefix_redacted" in discovery
        # The route_name comes through verbatim (no signed-CDN query to strip
        # for the batchGenerateImages route).
        assert "batchGenerateImages" in discovery["route_name"]

    async def test_body_prefix_redacted_excludes_tokens(self, client: FlowApiClient) -> None:
        """Audit gap #11: a 4xx response whose body echoes our request body
        (with the token in it) must NOT leak the token via the
        ``body_prefix_redacted`` discovery field. Redaction happens BEFORE
        the 200-char prefix is taken."""

        async def fake_request_post(url, *, data, headers):
            resp = MagicMock()
            resp.status = 400
            resp.headers = {"content-type": "application/json"}
            # Simulate the server echoing back our request body (with the
            # recaptcha token in it).
            resp.text = AsyncMock(return_value=data)
            return resp

        client._page.request.post = AsyncMock(side_effect=fake_request_post)

        with patch("gflow_cli.api.client.TokenMinter") as minter_cls:
            minter_cls.return_value.mint = AsyncMock(return_value="SECRET-TOKEN-XYZ")
            with pytest.raises(WireFormatError) as exc_info:
                await client.generate_image(project_id="proj-1", req=_make_req(), seed=1)

        discovery = exc_info.value.discovery
        body_prefix = discovery.get("body_prefix_redacted", "")
        # The literal token must not survive into the discovery payload.
        assert "SECRET-TOKEN-XYZ" not in body_prefix
        # And the redacted marker should be present.
        assert "<redacted>" in body_prefix
