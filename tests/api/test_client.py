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
)


class TestConstruction:
    def test_holds_profile_dir_and_headless_flag(self, tmp_path: Path) -> None:
        c = FlowApiClient(profile_dir=tmp_path / "prof", headless=False)
        assert c.profile_dir == tmp_path / "prof"
        assert c.headless is False

    def test_default_headless_true(self, tmp_path: Path) -> None:
        c = FlowApiClient(profile_dir=tmp_path / "prof")
        assert c.headless is True

    def test_page_property_raises_before_enter(self, tmp_path: Path) -> None:
        c = FlowApiClient(profile_dir=tmp_path / "prof")
        with pytest.raises(RuntimeError, match="not entered"):
            _ = c.page


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
