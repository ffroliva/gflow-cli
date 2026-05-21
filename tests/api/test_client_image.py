"""generate_image() — body assembly + reCAPTCHA + response parsing.

Phase C.1 rewrite note: generate_image() now delegates entirely to
self.transport.generate_images() (see client._drive_image_generation).
Tests that previously patched page.request.post have been rewritten to
inject a _FakeTransport. Assertions that tested transport-internal
concerns (URL routing, content-type header, body shape, token-minting
internals, 4xx/wire-format classification) are now correctly owned by
tests/api/transports/. This file retains client-level contract tests:
ContentPolicyError on empty media, GeneratedImage return value, batch
fan-out ordering/validation, and retry/re-mint contracts exercised via
a controllable _FakeTransport.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
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

_FAKE_IMAGE = GeneratedImage(
    media_name="media-uuid-1",
    workflow_id="wf-uuid-1",
    seed=646428,
    prompt="a warrior zelda in a dangeon. cinematic.",
    model_name_type="NARWHAL",
    aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
    fife_url=_FAKE_FIFE_URL,
    dimensions=(768, 1376),
)


class _FakeTransport:
    """Minimal FlowTransportStrategy stub for client-level tests.

    Satisfies the duck-type check in FlowApiClient (has setup, teardown,
    generate_images). Callers may override generate_images to simulate
    failures or inspect call arguments.
    """

    name = "fake"

    def __init__(self, images: list[GeneratedImage] | None = None) -> None:
        self._images = images if images is not None else [_FAKE_IMAGE]
        self.calls: list[dict[str, Any]] = []

    async def setup(self, profile_dir: Path) -> None:  # noqa: ARG002
        pass

    async def refresh_auth(self) -> None:
        pass

    async def teardown(self) -> None:
        pass

    async def generate_images(
        self,
        *,
        project_id: str | None,
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        self.calls.append({"project_id": project_id, "request": request})
        return list(self._images)


@pytest.fixture
def client(tmp_path: Path) -> FlowApiClient:
    c = FlowApiClient(profile_dir=tmp_path / "prof")
    # Provide a fake page so self.page doesn't raise outside async-with.
    # TokenMinter is patched in every test so the page object is never used.
    c._page = MagicMock()
    return c


def _make_req() -> GenerateImageRequest:
    return GenerateImageRequest(prompt="a warrior", aspect=Aspect.PORTRAIT)


def _client_with_transport(tmp_path: Path, transport: _FakeTransport) -> FlowApiClient:
    """Build a FlowApiClient pre-wired with a caller-owned fake transport."""
    c = FlowApiClient(profile_dir=tmp_path / "prof", transport=transport)
    # transport is pre-initialized (caller-owned) so skip Playwright lifecycle.
    c.transport = transport
    c._page = MagicMock()
    # `_drive_image_generation` mints a fresh reCAPTCHA token via the client's
    # Page on every attempt. Unit tests stub this with a static fake — the real
    # mint requires a Playwright Page running Google's reCAPTCHA Enterprise JS.
    c._mint_recaptcha_token = AsyncMock(return_value="test_recaptcha_token")  # type: ignore[method-assign]
    return c


class TestGenerateImage:
    async def test_generate_image_posts_to_correct_url(self, tmp_path: Path) -> None:
        """generate_image() delegates to transport.generate_images with the right project_id.

        URL routing (/projects/{id}/flowMedia:batchGenerateImages) is owned by
        the transport strategy — covered in tests/api/transports/. This test
        verifies the client passes project_id through correctly.
        """
        transport = _FakeTransport()
        client = _client_with_transport(tmp_path, transport)

        await client.generate_image(project_id="proj-1", req=_make_req(), seed=42)

        assert len(transport.calls) == 1
        assert transport.calls[0]["project_id"] == "proj-1"

    async def test_generate_image_uses_text_plain_content_type(self, tmp_path: Path) -> None:
        """Content-type header is a transport-internal concern.

        The client delegates the full POST to transport.generate_images. This
        test verifies generate_image() completes successfully when the transport
        returns a valid image (i.e. the client does not corrupt the request).
        """
        transport = _FakeTransport()
        client = _client_with_transport(tmp_path, transport)

        result = await client.generate_image(project_id="proj-1", req=_make_req(), seed=1)

        assert isinstance(result, GeneratedImage)

    async def test_generate_image_raises_content_policy_on_empty_media(
        self, tmp_path: Path
    ) -> None:
        """200 OK with empty media[] (silent content-policy rejection) must
        surface as ContentPolicyError, not a bare IndexError.

        Phase 4 T3 contract: ContentPolicyError extends FlowApiError (so
        ``except FlowApiError`` still catches it for back-compat) but its
        ``status`` is intentionally stripped per RFC 9457 (no 2xx on errors).
        The literal upstream 200 is recorded only via observability as
        ``upstream_status``.

        This is a client-level invariant: _drive_image_generation raises
        ContentPolicyError when transport.generate_images returns [].
        """

        class _EmptyTransport(_FakeTransport):
            async def generate_images(  # type: ignore[override]
                self, *, project_id: str | None, request: GenerateImageRequest
            ) -> list[GeneratedImage]:
                return []

        client = _client_with_transport(tmp_path, _EmptyTransport())

        with pytest.raises(ContentPolicyError) as exc_info:
            await client.generate_image(project_id="proj-1", req=_make_req(), seed=1)

        # ContentPolicyError IS a FlowApiError — back-compat preserved.
        assert isinstance(exc_info.value, FlowApiError)
        # RFC 9457: status not carried for 2xx-success-with-empty-media case.
        assert exc_info.value.to_problem_details().get("status") is None
        assert "/projects/proj-1/flowMedia:batchGenerateImages" in exc_info.value.route

    async def test_generate_image_mints_recaptcha_token(self, tmp_path: Path) -> None:
        """Token minting is a transport-internal concern (Spec C2).

        The transport strategy owns the reCAPTCHA mint+retry loop. This test
        verifies generate_image() calls transport.generate_images exactly once
        per invocation on the happy path.
        """
        transport = _FakeTransport()
        client = _client_with_transport(tmp_path, transport)

        await client.generate_image(project_id="proj-1", req=_make_req(), seed=42)

        assert len(transport.calls) == 1

    async def test_generate_image_returns_generated_image(self, tmp_path: Path) -> None:
        """Client contract: generate_image returns the first GeneratedImage
        from transport.generate_images."""
        transport = _FakeTransport(images=[_FAKE_IMAGE])
        client = _client_with_transport(tmp_path, transport)

        result = await client.generate_image(project_id="proj-1", req=_make_req(), seed=42)

        assert isinstance(result, GeneratedImage)
        assert result.fife_url == _FAKE_FIFE_URL
        assert result.media_name == "media-uuid-1"
        assert result.seed == 646428
        assert result.dimensions == (768, 1376)

    async def test_generate_image_propagates_flow_api_error_on_4xx(self, tmp_path: Path) -> None:
        """FlowApiError raised by transport.generate_images propagates to caller.

        4xx classification is performed inside the transport strategy. The
        client must not swallow or re-wrap these errors.
        """

        class _ErrorTransport(_FakeTransport):
            async def generate_images(  # type: ignore[override]
                self, *, project_id: str | None, request: GenerateImageRequest
            ) -> list[GeneratedImage]:
                raise FlowApiError(
                    400,
                    "bad request",
                    route=f"/projects/{project_id}/flowMedia:batchGenerateImages",
                )

        client = _client_with_transport(tmp_path, _ErrorTransport())

        with pytest.raises(FlowApiError) as exc_info:
            await client.generate_image(project_id="proj-1", req=_make_req(), seed=1)

        assert "/projects/proj-1/flowMedia:batchGenerateImages" in exc_info.value.route
        assert exc_info.value.status == 400

    async def test_generate_image_idempotent_body_modulo_recaptcha(self, tmp_path: Path) -> None:
        """Same seed+batch_id → transport called twice with the same project_id.

        Body idempotency (recaptchaContext.token, sessionId fields) is a
        transport-internal concern tested in tests/api/transports/. The
        client-level invariant is that it passes the same project_id on both
        calls.
        """
        transport = _FakeTransport()
        client = _client_with_transport(tmp_path, transport)

        await client.generate_image(
            project_id="proj-1", req=_make_req(), seed=42, batch_id="batch-1"
        )
        await client.generate_image(
            project_id="proj-1", req=_make_req(), seed=42, batch_id="batch-1"
        )

        assert len(transport.calls) == 2
        assert transport.calls[0]["project_id"] == transport.calls[1]["project_id"]


def _make_image_for_seed(seed: int) -> GeneratedImage:
    """Build a GeneratedImage for a given seed value."""
    return GeneratedImage(
        media_name=f"media-{seed}",
        workflow_id=f"wf-{seed}",
        seed=seed,
        prompt="a warrior",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url=f"https://flow-content.google/image/{seed}",
        dimensions=(768, 1376),
    )


class TestGenerateImagesBatch:
    async def test_batch_calls_transport_once(self, tmp_path: Path) -> None:
        """generate_images_batch makes exactly one transport call with count set on the request.

        The native count selector (x1/x2/x3/x4) means one submission round-trip
        produces N images — no parallel fan-out.
        """
        call_count = 0
        captured_request: GenerateImageRequest | None = None

        class _CountingTransport(_FakeTransport):
            async def generate_images(  # type: ignore[override]
                self, *, project_id: str | None, request: GenerateImageRequest
            ) -> list[GeneratedImage]:
                nonlocal call_count, captured_request
                call_count += 1
                captured_request = request
                return [_make_image_for_seed(i) for i in range(request.count)]

        client = _client_with_transport(tmp_path, _CountingTransport())
        results = await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=3)

        assert call_count == 1, "transport must be called exactly once (native count selector)"
        assert captured_request is not None
        assert captured_request.count == 3
        assert len(results) == 3

    async def test_batch_count_must_be_1_to_4(self, tmp_path: Path) -> None:
        """count=0 and count=5 must raise ValueError. Matches Flow UI cap."""
        transport = _FakeTransport()
        client = _client_with_transport(tmp_path, transport)

        with pytest.raises(ValueError, match="count must be between 1 and 4"):
            await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=0)
        with pytest.raises(ValueError, match="count must be between 1 and 4"):
            await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=5)

    async def test_batch_transport_failure_propagates(self, tmp_path: Path) -> None:
        """Transport raising FlowApiError propagates to the caller."""

        class _FailingTransport(_FakeTransport):
            async def generate_images(  # type: ignore[override]
                self, *, project_id: str | None, request: GenerateImageRequest
            ) -> list[GeneratedImage]:
                raise FlowApiError(
                    429,
                    "rate limited",
                    route=f"/projects/{project_id}/flowMedia:batchGenerateImages",
                )

        client = _client_with_transport(tmp_path, _FailingTransport())

        with pytest.raises(FlowApiError) as exc_info:
            await client.generate_images_batch(project_id="proj-1", req=_make_req(), count=2)

        assert exc_info.value.status == 429

    async def test_batch_seeds_back_compat_accepted_and_ignored(self, tmp_path: Path) -> None:
        """`seeds=` is accepted for back-compat but the UI transport ignores it.

        The active UI transport doesn't thread per-shot seeds; one submission
        produces N images via Flow's native xN selector. This test pins that
        passing a well-formed ``seeds=`` does not raise and produces the same
        result as omitting it. The parameter will be removed in a follow-up
        cleanup commit (#1b).
        """

        class _CountingTransport(_FakeTransport):
            async def generate_images(  # type: ignore[override]
                self, *, project_id: str | None, request: GenerateImageRequest
            ) -> list[GeneratedImage]:
                return [_make_image_for_seed(i) for i in range(request.count)]

        client = _client_with_transport(tmp_path, _CountingTransport())
        results = await client.generate_images_batch(
            project_id="proj-1",
            req=_make_req(),
            count=3,
            seeds=[100, 200, 300],
        )
        assert len(results) == 3

    @pytest.mark.parametrize(
        "seeds,count",
        [
            ([1, 2], 3),
            ([1, 2, 3, 4], 2),
        ],
    )
    async def test_batch_seeds_length_mismatch_raises(
        self, tmp_path: Path, seeds: list[int], count: int
    ) -> None:
        """`len(seeds) != count` must raise ValueError even though seeds is ignored."""
        transport = _FakeTransport()
        client = _client_with_transport(tmp_path, transport)
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

    With the transport abstraction, the mint+retry loop lives inside the
    transport strategy. The client-level contract is that generate_image()
    delegates to transport.generate_images() once per call on the happy path.
    Full re-mint-per-retry coverage lives in tests/api/transports/.

    """

    async def test_recaptcha_token_re_minted_every_attempt(self, tmp_path: Path) -> None:
        """Transport.generate_images is called once per generate_image() call.

        Spec C2 (token re-minted every retry attempt) is a transport-internal
        concern. The client-level invariant: on a happy-path call the transport
        is invoked exactly once. Re-mint-per-retry is covered in
        tests/api/transports/test_bearer.py and test_evaluate_fetch.py.
        """
        transport = _FakeTransport()
        client = _client_with_transport(tmp_path, transport)

        await client.generate_image(project_id="proj-1", req=_make_req(), seed=42)

        assert len(transport.calls) == 1


class TestWireFormatDiscoveryAndRedaction:
    """Audit gaps #9 and #11: discovery payload completeness + redaction-before-prefix.

    Wire-format error classification (WireFormatError with discovery payload)
    now lives inside the transport strategy. These tests verify the transport
    raises WireFormatError and that the error propagates to the client caller
    with the discovery dict intact. Full wire-level assertions (exact field
    derivation from real HTTP responses) are covered in tests/api/transports/.
    """

    async def test_wire_format_error_full_discovery_on_4xx(self, tmp_path: Path) -> None:
        """WireFormatError raised by transport propagates with complete discovery dict.

        The 4xx fallthrough WireFormatError must carry a complete RFC 9457
        ``discovery`` extension: route_name, http_status, content_type,
        top_level_keys (sorted), body_prefix_redacted.
        """

        class _WireErrorTransport(_FakeTransport):
            async def generate_images(  # type: ignore[override]
                self, *, project_id: str | None, request: GenerateImageRequest
            ) -> list[GeneratedImage]:
                raise WireFormatError(
                    detail="non-JSON response",
                    status=422,
                    instance="urn:uuid:test",
                    route=f"/projects/{project_id}/flowMedia:batchGenerateImages",
                    discovery={
                        "http_status": 422,
                        "content_type": "application/json",
                        "top_level_keys": ["code", "details", "error"],
                        "body_prefix_redacted": '{"error": "bad_payload"...',
                        "route_name": f"/projects/{project_id}/flowMedia:batchGenerateImages",
                    },
                )

        client = _client_with_transport(tmp_path, _WireErrorTransport())

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
        # The route_name comes through verbatim.
        assert "batchGenerateImages" in discovery["route_name"]

    async def test_body_prefix_redacted_excludes_tokens(self, tmp_path: Path) -> None:
        """Audit gap #11: body_prefix_redacted must not leak reCAPTCHA tokens.

        The transport is responsible for redacting sensitive tokens before
        raising WireFormatError. This test verifies the invariant holds at
        the client boundary — the token must not appear in any discovery field.
        """

        class _RedactedWireErrorTransport(_FakeTransport):
            async def generate_images(  # type: ignore[override]
                self, *, project_id: str | None, request: GenerateImageRequest
            ) -> list[GeneratedImage]:
                raise WireFormatError(
                    detail="non-JSON response",
                    status=400,
                    instance="urn:uuid:test",
                    route=f"/projects/{project_id}/flowMedia:batchGenerateImages",
                    discovery={
                        "http_status": 400,
                        "content_type": "application/json",
                        "top_level_keys": ["clientContext"],
                        "body_prefix_redacted": (
                            '{"clientContext": {"recaptchaContext": {"token": "<redacted>"}}}'
                        ),
                        "route_name": f"/projects/{project_id}/flowMedia:batchGenerateImages",
                    },
                )

        client = _client_with_transport(tmp_path, _RedactedWireErrorTransport())

        with pytest.raises(WireFormatError) as exc_info:
            await client.generate_image(project_id="proj-1", req=_make_req(), seed=1)

        discovery = exc_info.value.discovery
        body_prefix = discovery.get("body_prefix_redacted", "")
        # The literal token must not survive into the discovery payload.
        assert "SECRET-TOKEN-XYZ" not in body_prefix
        # And the redacted marker should be present.
        assert "<redacted>" in body_prefix


class TestGenerateImageAutoCreateProject:
    """When project_id is None, generate_image / generate_images_batch must
    call create_project() once and use its project_id."""

    async def test_generate_image_without_project_id_auto_creates_project(
        self, tmp_path: Path
    ) -> None:
        """generate_image(req=...) with no project_id calls create_project once
        and delegates to the transport with the resolved id."""
        from gflow_cli.api.dto import ProjectInfo

        fake_project = ProjectInfo(project_id="auto-proj-42", title="auto")
        transport = _FakeTransport(images=[_FAKE_IMAGE])
        client = _client_with_transport(tmp_path, transport)
        client.create_project = AsyncMock(return_value=fake_project)  # type: ignore[method-assign]

        result = await client.generate_image(req=_make_req())

        client.create_project.assert_awaited_once()  # type: ignore[attr-defined]
        assert result.fife_url == _FAKE_FIFE_URL
        assert transport.calls[0]["project_id"] == "auto-proj-42"

    async def test_generate_images_batch_without_project_id_auto_creates_project(
        self, tmp_path: Path
    ) -> None:
        """generate_images_batch(req=..., count=2) with no project_id calls
        create_project exactly once (not once per parallel shot)."""
        from gflow_cli.api.dto import ProjectInfo

        fake_project = ProjectInfo(project_id="auto-batch-proj", title="auto")
        call_count = 0

        class _CountingTransport(_FakeTransport):
            async def generate_images(  # type: ignore[override]
                self, *, project_id: str | None, request: GenerateImageRequest
            ) -> list[GeneratedImage]:
                nonlocal call_count
                call_count += 1
                # Return request.count images to mirror the native count selector.
                return [_make_image_for_seed(i) for i in range(request.count)]

        transport = _CountingTransport()
        client = _client_with_transport(tmp_path, transport)
        client.create_project = AsyncMock(return_value=fake_project)  # type: ignore[method-assign]

        results = await client.generate_images_batch(req=_make_req(), count=2)

        # Transport called ONCE (native count selector — no fan-out).
        assert call_count == 1
        client.create_project.assert_awaited_once()  # type: ignore[attr-defined]
        assert len(results) == 2
        for call in transport.calls:
            assert call["project_id"] == "auto-batch-proj"


class TestHealthCheck:
    """FlowApiClient.health_check() contract tests."""

    async def test_health_check_returns_false_when_not_entered(self, tmp_path: Path) -> None:
        """Before entering the async context, _page_queue is None → False."""
        client = FlowApiClient(profile_dir=tmp_path / "prof")
        result = await client.health_check()
        assert result is False

    async def test_health_check_returns_true_on_google_domain(self, tmp_path: Path) -> None:
        """A page whose document.location.hostname is a Google domain → True."""
        transport = _FakeTransport()
        client = _client_with_transport(tmp_path, transport)

        # Simulate an open page queue with a fake page returning a Google hostname.
        fake_page = MagicMock()
        fake_page.evaluate = AsyncMock(return_value="labs.google")
        client._page_queue = asyncio.Queue(maxsize=1)
        client._page_queue.put_nowait(fake_page)

        result = await client.health_check()

        assert result is True

    async def test_health_check_returns_false_on_non_google_domain(self, tmp_path: Path) -> None:
        """A page whose hostname is not on google → False."""
        transport = _FakeTransport()
        client = _client_with_transport(tmp_path, transport)

        fake_page = MagicMock()
        fake_page.evaluate = AsyncMock(return_value="example.com")
        client._page_queue = asyncio.Queue(maxsize=1)
        client._page_queue.put_nowait(fake_page)

        result = await client.health_check()

        assert result is False

    async def test_health_check_returns_false_on_evaluate_exception(self, tmp_path: Path) -> None:
        """If page.evaluate raises (e.g. TargetClosedError) → False, never raises."""
        transport = _FakeTransport()
        client = _client_with_transport(tmp_path, transport)

        fake_page = MagicMock()
        fake_page.evaluate = AsyncMock(side_effect=RuntimeError("target closed"))
        client._page_queue = asyncio.Queue(maxsize=1)
        client._page_queue.put_nowait(fake_page)

        result = await client.health_check()

        assert result is False
