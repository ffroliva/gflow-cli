"""Red tests for FlowProvider — they pin the contract before implementation lands.

Each method-level test starts as `pytest.raises(NotImplementedError)`. As routes
get wired, these become real behavioural tests with mocked HTTP. Live tests
(against the real Flow API) live in test_flow_live.py and are gated by the
GFLOW_LIVE env var.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from flow_cli.models import GenerationRequest
from flow_cli.providers.flow import FlowProvider


@pytest.fixture
def provider(tmp_path: Path) -> FlowProvider:
    """A FlowProvider with a tmp profile dir — no real auth, no network."""
    return FlowProvider(profile_dir=tmp_path / "profile_test")


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    """Smallest valid PNG — 1x1 transparent pixel."""
    png_bytes = bytes([
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
        0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
        0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
        0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41,
        0x54, 0x78, 0x9C, 0x62, 0x00, 0x01, 0x00, 0x00,
        0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00,
        0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,
        0x42, 0x60, 0x82,
    ])
    p = tmp_path / "1x1.png"
    p.write_bytes(png_bytes)
    return p


class TestProviderProtocol:
    """Provider exposes the documented interface."""

    def test_name_attr(self, provider: FlowProvider) -> None:
        assert provider.name == "flow"

    def test_is_async_context_manager(self, provider: FlowProvider) -> None:
        # __aenter__ and __aexit__ must exist
        assert hasattr(provider, "__aenter__")
        assert hasattr(provider, "__aexit__")


class TestUploadImage:
    """`upload_image` route — POST /v1/flow/uploadImage."""

    @pytest.mark.unit
    async def test_raises_not_implemented_for_now(
        self, provider: FlowProvider, sample_png: Path,
    ) -> None:
        # Red — turns into a behavioural test once route is wired.
        # When implemented, replace with: assert (await ...).uuid
        with pytest.raises(NotImplementedError):
            await provider.upload_image(sample_png)

    @pytest.mark.unit
    async def test_rejects_missing_file(self, provider: FlowProvider) -> None:
        # Behavioural contract: missing file should fail clearly, not silently.
        # Currently raises NotImplementedError before reaching that check; once
        # implemented this should raise FileNotFoundError or similar.
        with pytest.raises((NotImplementedError, FileNotFoundError, OSError)):
            await provider.upload_image(Path("/does/not/exist.png"))


class TestStartGeneration:
    """`start_generation` route — POST /v1/video:batchAsyncGenerateVideoText."""

    @pytest.mark.unit
    async def test_raises_not_implemented_for_now(
        self, provider: FlowProvider, sample_png: Path,
    ) -> None:
        req = GenerationRequest(start_image=sample_png, motion_prompt="test")
        with pytest.raises(NotImplementedError):
            await provider.start_generation(req)

    @pytest.mark.unit
    async def test_request_carries_required_fields(self, sample_png: Path) -> None:
        req = GenerationRequest(
            start_image=sample_png, motion_prompt="A push-in", aspect="16:9",
        )
        assert req.start_image == sample_png
        assert req.motion_prompt == "A push-in"
        assert req.aspect == "16:9"


class TestGetJob:
    """`get_job` route — POST /v1/video:batchCheckAsyncVideoGenerationStatus."""

    @pytest.mark.unit
    async def test_raises_not_implemented_for_now(self, provider: FlowProvider) -> None:
        with pytest.raises(NotImplementedError):
            await provider.get_job("dummy-job-id")


class TestDownload:
    """Signed-URL fetch via Playwright's request API."""

    @pytest.mark.unit
    async def test_raises_not_implemented_for_now(
        self, provider: FlowProvider, tmp_path: Path,
    ) -> None:
        out = tmp_path / "out.mp4"
        with pytest.raises(NotImplementedError):
            await provider.download("https://example/asset.mp4", out)
