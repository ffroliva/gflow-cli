"""S3 / MinIO storage integration tests.

Run with Docker and the ``containers`` dependency group::

    uv run --group containers pytest -m containers tests/integration/test_storage_s3.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.containers]


def test_storage_path_returns_cloud_upath(minio_storage_uri: str, tmp_path: Path) -> None:
    """storage_path() returns a UPath (not a plain Path) for an s3:// URI."""
    pytest.importorskip("upath")
    from gflow_cli.storage import is_cloud_path, storage_path

    target = storage_path(minio_storage_uri, tmp_path, "images/2026-05-27/abc_1.png")
    assert is_cloud_path(target)
    assert str(target).startswith("s3://")


def test_cloud_info_from_path_s3(minio_storage_uri: str, tmp_path: Path) -> None:
    """cloud_info_from_path() returns provider='s3' for an s3:// UPath."""
    pytest.importorskip("upath")
    from gflow_cli.storage import cloud_info_from_path, storage_path

    target = storage_path(minio_storage_uri, tmp_path, "images/test/abc.png")
    info = cloud_info_from_path(target)
    assert info is not None
    assert info.provider == "s3"
    assert info.uri.startswith("s3://")


@pytest.mark.asyncio
async def test_write_and_read_roundtrip_s3(minio_storage_uri: str, tmp_path: Path) -> None:
    """write_asset_async() uploads bytes; reading back via UPath returns the same data."""
    pytest.importorskip("upath")
    from gflow_cli.storage import storage_path, write_asset_async

    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # fake PNG header + padding
    key = "images/2026-05-27/integration_roundtrip.png"
    target = storage_path(minio_storage_uri, tmp_path, key)
    await write_asset_async(target, data)
    assert target.read_bytes() == data


@pytest.mark.asyncio
async def test_multiple_writes_to_different_keys(minio_storage_uri: str, tmp_path: Path) -> None:
    """Multiple objects can be written under different keys in the same prefix."""
    pytest.importorskip("upath")
    from gflow_cli.storage import storage_path, write_asset_async

    payloads = {
        "images/2026-05-27/img_a.png": b"data-a",
        "images/2026-05-27/img_b.png": b"data-b",
        "videos/2026-05-27/clip.mp4": b"video-data",
    }
    for key, data in payloads.items():
        target = storage_path(minio_storage_uri, tmp_path, key)
        await write_asset_async(target, data)

    for key, expected in payloads.items():
        target = storage_path(minio_storage_uri, tmp_path, key)
        assert target.read_bytes() == expected


def test_path_traversal_blocked_for_cloud_uri(minio_storage_uri: str, tmp_path: Path) -> None:
    """Key sanitisation raises ValueError for path-traversal attempts."""
    from gflow_cli.storage import storage_path

    with pytest.raises(ValueError, match="traversal"):
        storage_path(minio_storage_uri, tmp_path, "../../etc/passwd")


def test_storage_path_local_fallback_unchanged(tmp_path: Path) -> None:
    """When storage_uri=None, storage_path() returns a plain local Path."""
    from gflow_cli.storage import is_cloud_path, storage_path

    target = storage_path(None, tmp_path, "images/2026-05-27/abc.png")
    assert not is_cloud_path(target)
    assert isinstance(target, Path)
    assert target == tmp_path / "images" / "2026-05-27" / "abc.png"


@pytest.mark.asyncio
async def test_predictable_output_s3_single_file(minio_storage_uri: str, tmp_path: Path) -> None:
    """Explicit predictable output path written to MinIO S3 bucket with isolated DB."""
    pytest.importorskip("upath")
    from gflow_cli.storage import (
        cloud_info_from_path,
        is_cloud_path,
        storage_path,
        write_asset_async,
    )

    # Isolated environment: use tmp_path as GFLOW_CLI_HOME so live DB is untouched
    isolated_home = tmp_path / "isolated_home"
    isolated_home.mkdir(parents=True, exist_ok=True)

    data = b"\x89PNG\r\n\x1a\n" + b"custom_predictable_s3_data"
    key = "renders/custom_shot.png"
    target_upath = storage_path(minio_storage_uri, tmp_path, key)

    assert is_cloud_path(target_upath)
    await write_asset_async(target_upath, data)

    # Verify bytes written to MinIO S3 bucket match
    assert target_upath.read_bytes() == data

    # Verify cloud info metadata
    info = cloud_info_from_path(target_upath)
    assert info is not None
    assert info.provider == "s3"
    assert info.uri.endswith("renders/custom_shot.png")


@pytest.mark.asyncio
async def test_predictable_output_s3_multi_file(minio_storage_uri: str, tmp_path: Path) -> None:
    """Multi-asset output stem formatting written to MinIO S3 bucket."""
    pytest.importorskip("upath")
    from gflow_cli.storage import storage_path, write_asset_async

    base_upath = storage_path(minio_storage_uri, tmp_path, "batch/output.png")
    # Simulate multi-count index suffixes on UPath stem
    upath_1 = base_upath.parent / f"{base_upath.stem}_1{base_upath.suffix}"
    upath_2 = base_upath.parent / f"{base_upath.stem}_2{base_upath.suffix}"

    await write_asset_async(upath_1, b"s3_batch_1")
    await write_asset_async(upath_2, b"s3_batch_2")

    assert upath_1.read_bytes() == b"s3_batch_1"
    assert upath_2.read_bytes() == b"s3_batch_2"
