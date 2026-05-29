"""Cloud storage helpers for gflow-cli.

``storage_path()`` returns a local ``Path`` or a cloud ``UPath`` depending on
``GFLOW_CLI_STORAGE_URI``.  Cloud deps are optional extras::

    pip install gflow-cli[gcs]   # adds gcsfs
    pip install gflow-cli[s3]    # adds s3fs + aiobotocore

Security notes
--------------
* All keys are sanitised via ``_sanitize_key()`` — strips leading slashes and
  collapses ``..`` segments — to prevent key-injection via API-controlled
  ``media_id`` values.
* ``GFLOW_CLI_STORAGE_URI`` is validated at settings load time; only ``gs://``
  and ``s3://`` (plus ``memory://`` for tests) are accepted.
* Cloud writes run inside ``asyncio.to_thread()`` so the Playwright event loop
  is never stalled.
"""

from __future__ import annotations

import asyncio
import posixpath
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Public type alias
# ---------------------------------------------------------------------------

#: Either a local ``pathlib.Path`` or a cloud ``UPath`` (UPath IS-A Path).
AnyPath = Path

_CLOUD_SCHEMES: tuple[str, ...] = ("gs://", "s3://", "memory://")


# ---------------------------------------------------------------------------
# Cloud metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CloudStorageInfo:
    """Metadata about a cloud-stored asset, persisted to the local DB."""

    uri: str  # gs://bucket/prefix/key  or  s3://bucket/prefix/key
    provider: str  # "gcs" | "s3"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sanitize_key(key: str) -> str:
    """Strip leading slashes and collapse ``..`` segments.

    Prevents key-injection where an API-controlled ``media_id`` could escape
    the configured storage prefix (e.g. ``../../other-bucket/secret``).
    """
    normalized = posixpath.normpath(key.lstrip("/"))
    if normalized.startswith(".."):
        msg = f"Unsafe storage key (path traversal detected): {key!r}"
        raise ValueError(msg)
    return normalized


def _make_upath(uri: str) -> Path:
    try:
        from upath import UPath  # type: ignore[import-untyped]
    except ImportError as exc:
        msg = (
            "Cloud storage requires universal_pathlib. "
            "Install the matching extra:\n"
            "  pip install 'gflow-cli[gcs]'   # Google Cloud Storage\n"
            "  pip install 'gflow-cli[s3]'    # Amazon S3 / MinIO"
        )
        raise ImportError(
            msg,
        ) from exc
    return UPath(uri)  # type: ignore[return-value]


def _write_local(path: Path, data: bytes) -> None:
    """Synchronous helper: mkdir + write_bytes for local paths."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_cloud_path(target: AnyPath) -> bool:
    """Return ``True`` when *target* points to cloud storage, not local disk."""
    s = str(target)
    return any(s.startswith(scheme) for scheme in _CLOUD_SCHEMES)


def cloud_info_from_path(target: AnyPath) -> CloudStorageInfo | None:
    """Extract :class:`CloudStorageInfo` from *target*, or ``None`` for local paths."""
    if not is_cloud_path(target):
        return None
    uri = str(target)
    if uri.startswith("gs://"):
        return CloudStorageInfo(uri=uri, provider="gcs")
    if uri.startswith("s3://"):
        return CloudStorageInfo(uri=uri, provider="s3")
    return None  # memory:// is test-only; no persistent metadata needed


def storage_path(storage_uri: str | None, output_dir: Path, key: str) -> AnyPath:
    """Return a local ``Path`` or cloud ``UPath`` for the given relative *key*.

    Args:
        storage_uri: Configured ``GFLOW_CLI_STORAGE_URI``, e.g.
                     ``"gs://my-bucket/gflow/"``.  ``None`` → local storage.
        output_dir:  Local output root used when *storage_uri* is ``None``.
        key:         Relative path within the storage root, e.g.
                     ``"images/2026-05-27/abc_1.png"``.

    Raises:
        ValueError:  If *key* contains path-traversal components (``..``).
        ImportError: If *storage_uri* is set but ``universal_pathlib`` is not
                     installed.
    """
    safe_key = _sanitize_key(key)
    if not storage_uri:
        return output_dir / safe_key
    return _make_upath(storage_uri) / safe_key


async def write_asset_async(target: AnyPath, data: bytes) -> None:
    """Write *data* to *target* without blocking the asyncio event loop.

    * **Local** ``Path``: creates parent directories then writes; runs in a
      thread pool via ``asyncio.to_thread()``.
    * **Cloud** ``UPath``: delegates to the fsspec backend (gcsfs / s3fs)
      via ``asyncio.to_thread()`` so the Playwright event loop is not stalled.
    """
    if is_cloud_path(target):
        await asyncio.to_thread(target.write_bytes, data)
    else:
        await asyncio.to_thread(_write_local, Path(str(target)), data)
