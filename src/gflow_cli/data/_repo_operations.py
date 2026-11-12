"""``DataRepository`` table group: operations, their metadata and asset links (#1249 wave 2).

Moved out of ``repository.py`` unchanged.
"""

# pyright: reportPrivateUsage=false
# The shared helpers are private to the data package; this module is one of its parts.

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from gflow_cli.data.models import (
    OperationAssetRole,
    OperationKind,
    OperationRecord,
    OperationStatus,
)
from gflow_cli.errors import DataIntegrityError

if TYPE_CHECKING:
    pass


from gflow_cli.data._repo_base import (
    RepositoryBase,
    _coerce_utc,
    _decode_metadata_json,
    _row_to_operation,
    _utc_now,
)


class OperationsRepo(RepositoryBase):
    """Operations, their metadata and asset links."""

    def insert_operation(self, record: OperationRecord) -> OperationRecord:
        now = _utc_now()
        started_at = record.started_at or now
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    INSERT INTO operations(
                        id, profile_name, flow_project_id, command, mode,
                        prompt, prompt_hash, prompt_redacted, model, aspect_ratio,
                        status, started_at, completed_at,
                        error_type, error_detail,
                        flow_operation_id, flow_batch_id, expanded_prompt
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.profile_name,
                        record.flow_project_id,
                        record.command,
                        record.mode.value,
                        record.prompt,
                        record.prompt_hash,
                        int(bool(record.prompt_redacted)),
                        record.model,
                        record.aspect_ratio,
                        record.status.value,
                        started_at,
                        record.completed_at,
                        record.error_type,
                        record.error_detail,
                        record.flow_operation_id,
                        record.flow_batch_id,
                        record.expanded_prompt,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.insert_operation") from exc
        return cast("OperationRecord", dataclasses.replace(record, started_at=started_at))  # pyright: ignore[reportUnnecessaryCast]

    def update_operation_status(
        self,
        operation_id: str,
        status: OperationStatus,
        completed_at: str | None,
        error_type: str | None,
        error_detail: str | None,
    ) -> None:
        with self._store.transaction(immediate=True):
            self._store.conn.execute(
                """
                UPDATE operations
                SET status = ?, completed_at = ?, error_type = ?, error_detail = ?
                WHERE id = ?
                """,
                (status.value, completed_at, error_type, error_detail, operation_id),
            )

    def prune_failed_operations(
        self,
        *,
        older_than: timedelta,
        profile: str | None = None,
        dry_run: bool = False,
    ) -> int:
        """Delete failed operations older than *older_than*; return how many were
        deleted (or, when *dry_run*, WOULD be deleted).

        Explicit retention for #345 — never invoked automatically. Rows are
        matched in Python against ``now - older_than`` (robust to timestamp
        format), then deleted by id in chunks. Child ``operation_assets`` rows
        (rare for failures, but not assumed absent) are removed first to satisfy
        the ``operations(id)`` foreign key with ``PRAGMA foreign_keys = ON``.
        """
        cutoff = datetime.now(UTC) - older_than
        rows = self._store.conn.execute(
            "SELECT id, started_at FROM operations"
            " WHERE status = 'failed'"
            " AND (:profile IS NULL OR profile_name = :profile)",
            {"profile": profile},
        ).fetchall()
        stale_ids = [
            str(r["id"])
            for r in rows
            if _coerce_utc(datetime.fromisoformat(str(r["started_at"]))) < cutoff
        ]
        if dry_run or not stale_ids:
            return len(stale_ids)
        # SQLite variable limit is usually 999; 500 is safe.
        chunk_size = 500
        with self._store.transaction(immediate=True):
            for i in range(0, len(stale_ids), chunk_size):
                chunk = stale_ids[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                self._store.conn.execute(
                    f"DELETE FROM operation_assets WHERE operation_id IN ({placeholders})",  # noqa: S608 - constants and ? placeholders only, values stay bound
                    chunk,
                )
                self._store.conn.execute(
                    f"DELETE FROM operations WHERE id IN ({placeholders})",  # noqa: S608 - constants and ? placeholders only, values stay bound
                    chunk,
                )
        return len(stale_ids)

    def set_operation_metadata(
        self,
        operation_id: str,
        metadata_json: dict[str, object],
    ) -> None:
        """Write (or overwrite) the metadata_json column for an operation row."""
        with self._store.transaction(immediate=True):
            self._store.conn.execute(
                "UPDATE operations SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata_json, sort_keys=True), operation_id),
            )

    def get_operation_metadata(self, operation_id: str) -> dict[str, Any]:
        """The decoded ``metadata_json`` of one operation; ``{}`` when absent."""
        row = self._store.conn.execute(
            "SELECT metadata_json FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()
        return _decode_metadata_json(row["metadata_json"] if row else None)

    def update_operation_metadata(
        self,
        operation_id: str,
        *,
        status: OperationStatus,
        completed_at: str | None,
        prompt: str | None,
        prompt_hash: str | None,
        prompt_redacted: bool,
        metadata_json: dict[str, object],
    ) -> None:
        """Update status, prompt fields, and metadata_json in a single write."""
        with self._store.transaction(immediate=True):
            self._store.conn.execute(
                """
                UPDATE operations
                SET status = ?, completed_at = ?,
                    prompt = ?, prompt_hash = ?, prompt_redacted = ?,
                    metadata_json = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    completed_at,
                    prompt,
                    prompt_hash,
                    int(bool(prompt_redacted)),
                    json.dumps(metadata_json, sort_keys=True),
                    operation_id,
                ),
            )

    def get_operation_for_output_asset(
        self,
        profile_name: str,
        flow_media_id: str,
        mode: OperationKind,
    ) -> OperationRecord | None:
        row = self._store.conn.execute(
            """
            SELECT o.id, o.profile_name, o.flow_project_id, o.command, o.mode,
                   o.prompt, o.prompt_hash, o.prompt_redacted, o.model, o.aspect_ratio,
                   o.status, o.started_at, o.completed_at,
                   o.error_type, o.error_detail,
                   o.flow_operation_id, o.flow_batch_id, o.expanded_prompt
            FROM operations o
            JOIN operation_assets oa ON oa.operation_id = o.id
            JOIN assets a ON a.id = oa.asset_id
            WHERE o.profile_name = ?
              AND a.flow_media_id = ?
              AND o.mode = ?
              AND oa.role = ?
            ORDER BY o.started_at DESC
            LIMIT 1
            """,
            (profile_name, flow_media_id, mode.value, OperationAssetRole.OUTPUT.value),
        ).fetchone()
        if row is None:
            return None
        return _row_to_operation(row)

    def link_operation_asset(
        self,
        operation_id: str,
        asset_id: str,
        role: OperationAssetRole,
        position: int,
    ) -> None:
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    INSERT INTO operation_assets(operation_id, asset_id, role, position)
                    VALUES (?, ?, ?, ?)
                    """,
                    (operation_id, asset_id, role.value, position),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.link_operation_asset") from exc
