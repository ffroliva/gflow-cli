"""FlowApiClient — construction + lifecycle smoke tests (no live network).

End-to-end live tests against the real Flow API live in
`tests/api/test_client_live.py` (planned) and are gated by `GFLOW_LIVE=1`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gflow_cli.api.client import (
    MAX_IMAGE_BYTES,
    FlowApiClient,
    FlowApiError,
    _default_project_title,
    _is_supported_image_header,
    _is_target_closed,
)
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import Settings
from gflow_cli.errors import BrowserSessionClosedError


class TestConstruction:
    def test_holds_profile_dir_and_headless_flag(self, tmp_path: Path) -> None:
        c = FlowApiClient(profile_dir=tmp_path / "prof", headless=False)
        assert c.profile_dir == tmp_path / "prof"
        assert c.headless is False

    def test_default_headless_false(self, tmp_path: Path) -> None:
        # ui_automation transport requires headed Chrome — reCAPTCHA rejects headless
        c = FlowApiClient(profile_dir=tmp_path / "prof")
        assert c.headless is False

    def test_page_property_raises_before_enter(self, tmp_path: Path) -> None:
        c = FlowApiClient(profile_dir=tmp_path / "prof")
        with pytest.raises(RuntimeError, match="not entered"):
            _ = c.page

    def test_storage_uri_plumbs_to_ui_automation_transport(self, tmp_path: Path) -> None:
        transport = UiAutomationTransport()
        settings = Settings(storage_uri="s3://bucket/prefix/", output_dir=tmp_path / "out")
        client = FlowApiClient(
            profile_dir=tmp_path / "prof",
            settings=settings,
            transport=transport,
        )

        client._plumb_storage_uri(transport)

        assert transport._storage_uri == "s3://bucket/prefix/"
        assert transport._output_dir == tmp_path / "out"


class TestApiError:
    def test_includes_status_route_and_body_excerpt(self) -> None:
        e = FlowApiError(401, "unauthorized — fake body", route="https://example/foo")
        assert e.status == 401
        assert e.route == "https://example/foo"
        assert "401" in str(e)
        assert "unauthorized" in str(e)


class TestDefaultProjectTitle:
    def test_starts_with_flow_cli_prefix(self) -> None:
        title = _default_project_title()
        assert title.startswith("gflow-cli ")


class TestSupportedImageHeader:
    """Magic-byte sniffing — defense-in-depth against symlink exfiltration."""

    def test_accepts_png(self) -> None:
        # 8-byte PNG signature + 4 filler bytes to reach the 12-byte read.
        assert _is_supported_image_header(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00")

    def test_accepts_jpeg(self) -> None:
        assert _is_supported_image_header(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01")

    def test_accepts_webp(self) -> None:
        assert _is_supported_image_header(b"RIFF\x00\x00\x00\x00WEBP")

    def test_accepts_gif87a(self) -> None:
        assert _is_supported_image_header(b"GIF87a\x00\x00\x00\x00\x00\x00")

    def test_accepts_gif89a(self) -> None:
        assert _is_supported_image_header(b"GIF89a\x00\x00\x00\x00\x00\x00")

    def test_rejects_text_blob(self) -> None:
        assert not _is_supported_image_header(b"#!/bin/bash\n")

    def test_rejects_short_buffer(self) -> None:
        # < 12 bytes is unsafe to sniff because WEBP needs bytes 8..11.
        assert not _is_supported_image_header(b"\x89PNG")

    def test_rejects_riff_without_webp(self) -> None:
        # RIFF .WAV / .AVI must NOT be accepted as image.
        assert not _is_supported_image_header(b"RIFF\x00\x00\x00\x00WAVE")


class TestUploadImageValidation:
    """Pre-flight validation in `upload_image` BEFORE any bytes hit the wire.

    Covers the three findings closed by this commit:
    * size cap (cheap stat() check first),
    * magic-byte sniff (rejects non-image blobs and symlink exfil attempts),
    * happy path still succeeds with a real PNG header.

    `_post_json` is monkey-patched so no Playwright context is needed.
    """

    @staticmethod
    def _client_with_mocked_post(tmp_path: Path) -> FlowApiClient:
        c = FlowApiClient(profile_dir=tmp_path / "prof")
        # Default upload response shape — only used by the happy-path test.
        c._post_json = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "media": {
                    "name": "asset-uuid-xyz",
                    "projectId": "proj-1",
                    "workflowId": "wf-1",
                    "image": {"dimensions": {"width": 4, "height": 4}},
                },
                "workflow": {"metadata": {"displayName": "ok.png"}},
            }
        )
        return c

    @pytest.mark.asyncio
    async def test_upload_image_accepts_png_header(self, tmp_path: Path) -> None:
        png = tmp_path / "ok.png"
        # Real PNG: 8-byte sig + 4 filler bytes so the 12-byte header read is full.
        png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00")
        c = self._client_with_mocked_post(tmp_path)

        asset = await c.upload_image("proj-1", png)

        assert asset.name == "asset-uuid-xyz"
        # Wire call DID happen exactly once — validation didn't short-circuit.
        c._post_json.assert_awaited_once()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_upload_image_rejects_non_image(self, tmp_path: Path) -> None:
        bogus = tmp_path / "shell.sh"
        bogus.write_bytes(b"#!/bin/bash\necho pwn\n")
        c = self._client_with_mocked_post(tmp_path)

        with pytest.raises(ValueError, match="Not a supported image format"):
            await c.upload_image("proj-1", bogus)

        # No network call must occur on a rejected file.
        c._post_json.assert_not_awaited()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_upload_image_rejects_empty_file(self, tmp_path: Path) -> None:
        empty = tmp_path / "blank.png"
        empty.write_bytes(b"")
        c = self._client_with_mocked_post(tmp_path)

        # Zero-byte file fails magic-byte sniffing (header < 12 bytes).
        with pytest.raises(ValueError, match="Not a supported image format"):
            await c.upload_image("proj-1", empty)
        c._post_json.assert_not_awaited()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_upload_image_rejects_oversized_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        big = tmp_path / "big.png"
        big.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00")  # valid header
        c = self._client_with_mocked_post(tmp_path)

        # Patch Path.stat globally to fake a 21 MB file without writing one.
        # Must run BEFORE magic-byte read so size check fails first (cheap-first).
        real_stat = Path.stat
        oversize = MAX_IMAGE_BYTES + 1024 * 1024  # 21 MB

        def fake_stat(self: Path, *args: object, **kwargs: object) -> object:
            result = real_stat(self, *args, **kwargs)  # type: ignore[arg-type]
            if self == big:
                # os.stat_result is immutable — return a lightweight stand-in
                # exposing only the attribute upload_image touches.
                class _Stat:
                    st_size = oversize

                return _Stat()
            return result

        monkeypatch.setattr(Path, "stat", fake_stat)

        with pytest.raises(ValueError, match="Image too large"):
            await c.upload_image("proj-1", big)
        c._post_json.assert_not_awaited()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Transport lifecycle ownership tests (Task A.5)
# ---------------------------------------------------------------------------


class _FakeTransport:
    name = "fake"

    def __init__(self) -> None:
        self.setup_called = 0
        self.teardown_called = 0

    async def setup(self, profile_dir: Path, *, page: object | None = None) -> None:
        # page kwarg accepted (Protocol-compliance for S1 shared-page fix); not used by the fake.
        _ = page
        self.setup_called += 1

    async def refresh_auth(self) -> None:
        pass

    async def generate_images(self, *, project_id: str | None, request: object) -> list[object]:
        return []

    async def teardown(self) -> None:
        self.teardown_called += 1


def _patch_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the Playwright stack so lifecycle tests run without a real browser."""
    from unittest.mock import AsyncMock, MagicMock

    fake_page = MagicMock()
    # Stub out page.goto so __aenter__ bootstrap navigation doesn't fail.
    fake_page.goto = AsyncMock()

    fake_context = MagicMock()
    fake_context.pages = [fake_page]
    fake_context.close = AsyncMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.add_init_script = AsyncMock()

    fake_pw = MagicMock()
    fake_pw.stop = AsyncMock()
    fake_pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)

    # async_playwright() returns an object whose .start() coroutine resolves to `fake_pw`.
    fake_pw_starter = MagicMock()
    fake_pw_starter.start = AsyncMock(return_value=fake_pw)

    monkeypatch.setattr("gflow_cli.api.client.async_playwright", lambda: fake_pw_starter)


@pytest.mark.asyncio
async def test_client_with_preinitialized_transport_does_not_own_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-initialized transport: client must NOT call setup() or teardown()."""
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    _patch_playwright(monkeypatch)
    fake = _FakeTransport()
    async with FlowApiClient(profile_dir=tmp_path, transport=fake) as client:
        assert client.transport is fake
        assert fake.setup_called == 0
    assert fake.teardown_called == 0


@pytest.mark.asyncio
async def test_enter_setup_routes_launch_through_kwargs_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """__aenter__ must launch the persistent context via the
    _persistent_context_kwargs() seam, not a stale inline dict, so a dev-scoped
    recording subclass override actually takes effect. The pin test alone cannot
    catch a re-inlined call site; this can."""
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    _patch_playwright(monkeypatch)
    fake = _FakeTransport()
    seam_calls: list[object] = []
    original = FlowApiClient._persistent_context_kwargs

    def spy(self: FlowApiClient) -> dict[str, object]:
        kwargs = original(self)
        seam_calls.append(kwargs)
        return kwargs

    monkeypatch.setattr(FlowApiClient, "_persistent_context_kwargs", spy)
    async with FlowApiClient(profile_dir=tmp_path, transport=fake):
        pass
    # Consulted exactly once during setup -> the launch routes through the seam.
    # A future re-inline of the dict would drop this to 0.
    assert len(seam_calls) == 1


@pytest.mark.asyncio
async def test_client_with_string_transport_owns_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """String/None transport: client resolves via make_transport, owns lifecycle."""
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    _patch_playwright(monkeypatch)
    fake = _FakeTransport()
    monkeypatch.setattr(
        "gflow_cli.api.client.make_transport",
        lambda name=None: fake,
    )
    async with FlowApiClient(profile_dir=tmp_path, transport=None) as client:
        assert client.transport is fake
        assert fake.setup_called == 1
    assert fake.teardown_called == 1


# ---------------------------------------------------------------------------
# Image-gen transport delegation test (Task A.7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_delegates_image_gen_to_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A.7 — client.generate_image MUST delegate to self.transport.generate_images,
    not POST directly. This is the contract introduced by Phase A and consumed by
    Phase B strategy implementations."""
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    _patch_playwright(monkeypatch)
    fake = _FakeTransport()

    sentinel = GeneratedImage(
        media_name="sentinel-uuid",
        workflow_id="wf-sentinel",
        seed=42,
        prompt="delegated",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://example.com/img.png",
        dimensions=(512, 512),
    )

    async def fake_gen(*, project_id: str | None, request: object) -> list[GeneratedImage]:
        assert project_id == "test-proj-xyz"
        assert isinstance(request, GenerateImageRequest)
        assert request.prompt == "delegated"
        return [sentinel]

    fake.generate_images = fake_gen  # type: ignore[method-assign]

    async with FlowApiClient(profile_dir=tmp_path, transport=fake) as client:
        # Stub the reCAPTCHA mint — real mint needs a Page with reCAPTCHA Enterprise JS loaded.
        client._mint_recaptcha_token = AsyncMock(return_value="test_recaptcha_token")  # type: ignore[method-assign]
        result = await client.generate_image(
            project_id="test-proj-xyz",
            req=GenerateImageRequest(
                prompt="delegated",
                model=Model.NARWHAL,
                aspect=Aspect.PORTRAIT,
            ),
        )
    assert result is sentinel


# ---------------------------------------------------------------------------
# Issue #18 — out_dir plumbing + TargetClosedError translation
# ---------------------------------------------------------------------------


class _OutDirAwareTransport(_FakeTransport):
    """Like _FakeTransport but exposes an `_out_dir` attribute the client can set."""

    def __init__(self) -> None:
        super().__init__()
        self._out_dir: Path | None = None


@pytest.mark.asyncio
async def test_client_plumbs_out_dir_to_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#18 — FlowApiClient.out_dir must propagate to transport._out_dir."""
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    _patch_playwright(monkeypatch)
    fake = _OutDirAwareTransport()
    out = tmp_path / "shots"
    async with FlowApiClient(profile_dir=tmp_path, transport=fake, out_dir=out) as client:
        assert client._out_dir == out
        assert fake._out_dir == out


@pytest.mark.asyncio
async def test_client_omits_out_dir_when_transport_lacks_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transports without an `_out_dir` slot are left untouched (hasattr-guarded)."""
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    _patch_playwright(monkeypatch)
    fake = _FakeTransport()  # NOTE: no _out_dir attribute
    async with FlowApiClient(profile_dir=tmp_path, transport=fake, out_dir=tmp_path / "shots"):
        assert not hasattr(fake, "_out_dir")


def test_is_target_closed_recognises_marker() -> None:
    err = RuntimeError("Target page, context or browser has been closed")
    assert _is_target_closed(err) is True


def test_is_target_closed_recognises_class_name() -> None:
    class TargetClosedError(Exception):  # noqa: N818 — mimic Playwright class name
        pass

    assert _is_target_closed(TargetClosedError("anything")) is True


def test_is_target_closed_returns_false_for_other_errors() -> None:
    assert _is_target_closed(ValueError("nope")) is False


@pytest.mark.asyncio
async def test_generate_image_translates_target_closed_to_browser_session_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Playwright TargetClosedError raised by the transport must surface as
    gflow_cli.errors.BrowserSessionClosedError so long-lived workers can catch
    a stable library-owned class without importing playwright._impl._errors."""
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    _patch_playwright(monkeypatch)
    fake = _FakeTransport()

    async def boom(*, project_id: str | None, request: object) -> list[GeneratedImage]:
        _ = project_id
        _ = request
        raise RuntimeError("Target page, context or browser has been closed")

    fake.generate_images = boom  # type: ignore[method-assign]

    async with FlowApiClient(profile_dir=tmp_path, transport=fake) as client:
        client._mint_recaptcha_token = AsyncMock(return_value="tok")  # type: ignore[method-assign]
        with pytest.raises(BrowserSessionClosedError):
            await client.generate_image(
                project_id="p",
                req=GenerateImageRequest(prompt="x", model=Model.NARWHAL, aspect=Aspect.PORTRAIT),
            )


# ---------------------------------------------------------------------------
# generate_video client-boundary tests (Task 7)
# ---------------------------------------------------------------------------


class _VideoCapableFakeTransport(_FakeTransport):
    """FakeTransport that also implements generate_video."""

    async def generate_video(
        self,
        *,
        request: object,
        out_dir: object,
        poll_timeout_s: float,
        download: bool,
        on_started: object = None,
    ) -> object:
        from gflow_cli.api.video import VideoResult, VideoStatus

        status = VideoStatus(media_id="media-1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
        return VideoResult(status=status, local_path=None)


@pytest.mark.asyncio
async def test_generate_video_delegates_to_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FlowApiClient.generate_video must delegate to the transport's generate_video."""
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    _patch_playwright(monkeypatch)
    fake = _VideoCapableFakeTransport()

    from gflow_cli.api.video import GenerateVideoRequest, Mode

    request = GenerateVideoRequest(prompt="sunset over mountains", mode=Mode.T2V)

    async with FlowApiClient(profile_dir=tmp_path, transport=fake) as client:
        result = await client.generate_video(req=request, out_dir=tmp_path, download=True)

    assert result.status.media_id == "media-1"


@pytest.mark.asyncio
async def test_generate_video_raises_for_non_video_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """generate_video must raise RuntimeError if the transport lacks generate_video."""
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    _patch_playwright(monkeypatch)
    fake = _FakeTransport()  # does NOT implement generate_video

    from gflow_cli.api.video import GenerateVideoRequest, Mode

    request = GenerateVideoRequest(prompt="x", mode=Mode.T2V)

    async with FlowApiClient(profile_dir=tmp_path, transport=fake) as client:
        with pytest.raises(RuntimeError, match="does not support video"):
            await client.generate_video(req=request, out_dir=tmp_path, download=True)


@pytest.mark.asyncio
async def test_download_video_delegates_to_download(tmp_path: Path) -> None:
    """download_video(media_id, out_path) delegates to self.download()."""
    from unittest.mock import patch

    out_path = tmp_path / "my_video.mp4"

    with patch.object(FlowApiClient, "download", new_callable=AsyncMock) as mock_dl:
        mock_dl.return_value = out_path
        client = object.__new__(FlowApiClient)  # bypass __init__
        result = await client.download_video("media-uuid-abc", out_path)

    mock_dl.assert_awaited_once_with("media-uuid-abc", out_path)
    assert result == out_path
