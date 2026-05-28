"""GCS / fake-gcs-server storage integration tests.

Run with Docker and the ``containers`` dependency group::

    uv run --group containers pytest -m containers tests/integration/test_storage_gcs.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.containers]


def test_storage_path_returns_cloud_upath_gcs(fake_gcs_storage_uri: str, tmp_path: Path) -> None:
    """storage_path() returns a UPath (not a plain Path) for a gs:// URI."""
    pytest.importorskip("upath")
    from gflow_cli.storage import is_cloud_path, storage_path

    target = storage_path(fake_gcs_storage_uri, tmp_path, "images/2026-05-27/abc_1.png")
    assert is_cloud_path(target)
    assert str(target).startswith("gs://")


def test_cloud_info_from_path_gcs(fake_gcs_storage_uri: str, tmp_path: Path) -> None:
    """cloud_info_from_path() returns provider='gcs' for a gs:// UPath."""
    pytest.importorskip("upath")
    from gflow_cli.storage import cloud_info_from_path, storage_path

    target = storage_path(fake_gcs_storage_uri, tmp_path, "images/test/abc.png")
    info = cloud_info_from_path(target)
    assert info is not None
    assert info.provider == "gcs"
    assert info.uri.startswith("gs://")


@pytest.mark.asyncio
async def test_write_and_read_roundtrip_gcs(fake_gcs_storage_uri: str, tmp_path: Path) -> None:
    """write_asset_async() uploads bytes to fake-gcs; reading back returns the same data."""
    pytest.importorskip("upath")
    from gflow_cli.storage import storage_path, write_asset_async

    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # fake PNG header + padding
    key = "images/2026-05-27/integration_roundtrip_gcs.png"
    target = storage_path(fake_gcs_storage_uri, tmp_path, key)
    await write_asset_async(target, data)
    assert target.read_bytes() == data


def test_path_traversal_blocked_for_gcs_uri(fake_gcs_storage_uri: str, tmp_path: Path) -> None:
    """Key sanitisation raises ValueError for path-traversal attempts."""
    from gflow_cli.storage import storage_path

    with pytest.raises(ValueError, match="traversal"):
        storage_path(fake_gcs_storage_uri, tmp_path, "../../etc/passwd")
