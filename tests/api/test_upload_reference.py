"""Upload a local reference once and get the handle to mention it in place (#913, PR C)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.image import ImageRef
from gflow_cli.api.transports import migrated_composer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class _Page:
    def __init__(self, url: str) -> None:
        self.url = url


def _transport(url: str, monkeypatch: pytest.MonkeyPatch) -> UiAutomationTransport:
    t = UiAutomationTransport()
    t._setup_done = True  # noqa: SLF001
    t._page = _Page(url)  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr(t, "park_deferred_page", AsyncMock())
    monkeypatch.setattr(t, "_park_composer_page", AsyncMock())
    monkeypatch.setattr(migrated_composer.MigratedComposer, "ensure_editor", AsyncMock())
    monkeypatch.setattr(
        migrated_composer.MigratedComposer,
        "upload",
        AsyncMock(return_value=("media-1", "photo-1a2b3c4d.png")),
    )
    return t


async def test_flow_google_com_uploads_through_the_composer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    t = _transport("https://flow.google.com/project/p", monkeypatch)
    ref = await t.upload_reference(project_id="p", path=tmp_path / "photo.png")
    assert ref == ImageRef(name="media-1", display_name="photo-1a2b3c4d.png", in_project=True)
    t._park_composer_page.assert_awaited()  # type: ignore[attr-defined]  # noqa: SLF001


async def test_labs_routing_leaves_the_upload_to_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "labs.google")
    from gflow_cli.config import reset_settings

    reset_settings()
    t = _transport("https://labs.google/fx/tools/flow/project/p", monkeypatch)
    assert await t.upload_reference(project_id="p", path=tmp_path / "photo.png") is None
    migrated_composer.MigratedComposer.upload.assert_not_awaited()  # type: ignore[attr-defined]


async def _client_with(transport: Any) -> Any:
    from gflow_cli.api.client import FlowApiClient

    client = FlowApiClient.__new__(FlowApiClient)
    client.transport = transport
    return client


async def test_the_client_prefers_the_transport_handle(tmp_path: Path) -> None:
    photo = tmp_path / "photo.png"
    photo.write_bytes(_PNG)
    handle = ImageRef(name="media-1", display_name="photo.png", in_project=True)
    transport = MagicMock()
    transport.upload_reference = AsyncMock(return_value=handle)
    client = await _client_with(transport)
    client.upload_image = AsyncMock()
    assert await client.upload_reference("p", photo) == handle
    client.upload_image.assert_not_awaited()


async def test_the_client_falls_back_to_rest_and_checks_the_file_first(tmp_path: Path) -> None:
    photo = tmp_path / "photo.png"
    photo.write_bytes(_PNG)
    transport = MagicMock()
    transport.upload_reference = AsyncMock(return_value=None)
    client = await _client_with(transport)
    client.upload_image = AsyncMock(return_value=MagicMock(name="asset", display_name="photo.png"))
    client.upload_image.return_value.name = "asset-1"
    ref = await client.upload_reference("p", photo)
    assert (ref.name, ref.in_project) == ("asset-1", True)

    secret = tmp_path / "id_rsa"
    secret.write_text("-----BEGIN OPENSSH PRIVATE KEY-----", encoding="utf-8")
    with pytest.raises(ValueError, match="Not a supported image"):
        await client.upload_reference("p", secret)
