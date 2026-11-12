"""Shared state and helpers for the ``DataRepository`` table groups (#1249 wave 2).

Moved out of ``repository.py`` unchanged; ``repository`` re-exports the public names.
"""

# pyright: reportUnusedFunction=false
# The private helpers here are used by the sibling ``_repo_*`` table groups, not this module.

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from gflow_cli.data.models import (
    LocalFileRecord,
    OperationKind,
    OperationRecord,
    OperationStatus,
)
from gflow_cli.file_integrity import matches_recorded_file

if TYPE_CHECKING:
    from gflow_cli.data.store import DataStore


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _coerce_utc(dt: datetime) -> datetime:
    """UTC-aware view of a catalog timestamp; assume UTC if a legacy value is naive."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


_ASSET_LOOKUP_COLUMNS = (
    "id, profile_name, flow_project_id, flow_media_id, flow_workflow_id, metadata_json, kind, seed"
)


def verified_local_path(local_file: LocalFileRecord) -> Path | None:
    """Return an on-disk catalog file only when its recorded integrity still matches."""
    path = local_file.path
    if local_file.storage_provider is not None or path is None:
        return None
    recorded_sha256 = local_file.sha256
    if recorded_sha256 is None or not matches_recorded_file(
        path, sha256=recorded_sha256, size=local_file.bytes
    ):
        return None
    return path


@dataclasses.dataclass(frozen=True)
class ProjectWork:
    """One project's nameless catalog rows, grouped for the #543 sync sweep.

    ``run_sync`` consumes exactly these attribute names.
    """

    flow_project_id: str
    media_ids: tuple[str, ...]


def _decode_metadata_json(raw: object) -> dict[str, Any]:
    try:
        parsed: object = json.loads(str(raw)) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return {}
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {}


def _row_to_local_file(row: sqlite3.Row) -> LocalFileRecord:
    provider: str | None = row["storage_provider"]
    # Cloud-only rows: path column holds the cloud URI, not a filesystem path.
    local_path = None if provider is not None else Path(str(row["path"]))
    return LocalFileRecord(
        id=str(row["id"]),
        profile_name=str(row["profile_name"]),
        asset_id=str(row["asset_id"]),
        path=local_path,
        media_type=row["media_type"],
        bytes=row["bytes"],
        sha256=row["sha256"],
        storage_provider=provider,
        cloud_uri=row["cloud_uri"],
        created_at=row["created_at"],
    )


def _row_to_operation(row: sqlite3.Row) -> OperationRecord:
    return OperationRecord(
        id=str(row["id"]),
        profile_name=str(row["profile_name"]),
        flow_project_id=row["flow_project_id"],
        command=row["command"],
        mode=OperationKind(row["mode"]),
        status=OperationStatus(row["status"]),
        flow_operation_id=row["flow_operation_id"],
        flow_batch_id=row["flow_batch_id"],
        prompt=row["prompt"],
        prompt_hash=row["prompt_hash"],
        prompt_redacted=bool(int(row["prompt_redacted"])),
        model=row["model"],
        aspect_ratio=row["aspect_ratio"],
        error_type=row["error_type"],
        error_detail=row["error_detail"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        expanded_prompt=row["expanded_prompt"],
    )


class RepositoryBase:
    """The one store every table group reads and writes."""

    _store: DataStore
