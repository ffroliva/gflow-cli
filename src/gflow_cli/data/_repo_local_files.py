"""``DataRepository`` table group: local files and seed-image resolution (#1249 wave 2).

Moved out of ``repository.py`` unchanged.
"""

# pyright: reportPrivateUsage=false
# The shared helpers are private to the data package; this module is one of its parts.

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from typing import Any, cast

from gflow_cli.data._repo_base import (
    RepositoryBase,
    _utc_now,
)
from gflow_cli.data.models import (
    AssetKind,
    LocalFileRecord,
    OperationAssetRole,
    SeedImage,
)
from gflow_cli.errors import DataIntegrityError


class LocalFilesRepo(RepositoryBase):
    """Local files and seed-image resolution."""

    def upsert_local_file(self, record: LocalFileRecord) -> LocalFileRecord:
        now = _utc_now()
        created_at = record.created_at or now
        # For cloud-only rows path is None; store cloud_uri in the path column
        # to satisfy the NOT NULL + UNIQUE(asset_id, path) constraint.
        db_path = str(record.path) if record.path is not None else record.cloud_uri
        if db_path is None:
            raise DataIntegrityError(
                detail="LocalFileRecord must have path or cloud_uri",
                route="data.upsert_local_file",
            )
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    INSERT INTO local_files(
                        id, profile_name, asset_id, path,
                        sha256, bytes, media_type, created_at,
                        storage_provider, cloud_uri
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(asset_id, path) DO UPDATE SET
                        id = excluded.id,
                        profile_name = excluded.profile_name,
                        sha256 = excluded.sha256,
                        bytes = excluded.bytes,
                        media_type = excluded.media_type,
                        storage_provider = excluded.storage_provider,
                        cloud_uri = excluded.cloud_uri
                    """,
                    (
                        record.id,
                        record.profile_name,
                        record.asset_id,
                        db_path,
                        record.sha256,
                        record.bytes,
                        record.media_type,
                        created_at,
                        record.storage_provider,
                        record.cloud_uri,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.upsert_local_file") from exc
        return cast("LocalFileRecord", dataclasses.replace(record, created_at=created_at))  # pyright: ignore[reportUnnecessaryCast]

    def resolve_seed_image(self, profile_name: str, flow_media_id: str) -> SeedImage | None:
        row = self._store.conn.execute(
            """
            SELECT a.id, a.profile_name, a.flow_project_id, a.flow_media_id,
                   a.flow_workflow_id, a.kind, a.width, a.height,
                   a.model, a.aspect_ratio, a.created_at,
                   o.prompt
            FROM assets a
            LEFT JOIN operation_assets oa ON oa.asset_id = a.id AND oa.role = ?
            LEFT JOIN operations o ON o.id = oa.operation_id
            WHERE a.profile_name = ?
              AND a.flow_media_id = ?
              AND a.kind = 'image'
            LIMIT 1
            """,
            (OperationAssetRole.OUTPUT.value, profile_name, flow_media_id),
        ).fetchone()
        return self._row_to_seed_image(row)

    def resolve_seed_image_by_path(self, profile_name: str, path: Path) -> SeedImage | None:
        row = self._store.conn.execute(
            """
            SELECT a.id, a.profile_name, a.flow_project_id, a.flow_media_id,
                   a.flow_workflow_id, a.kind, a.width, a.height,
                   a.model, a.aspect_ratio, a.created_at,
                   o.prompt
            FROM assets a
            JOIN local_files lf ON lf.asset_id = a.id
            LEFT JOIN operation_assets oa ON oa.asset_id = a.id AND oa.role = ?
            LEFT JOIN operations o ON o.id = oa.operation_id
            WHERE a.profile_name = ?
              AND a.kind = 'image'
              AND lf.path = ?
            LIMIT 1
            """,
            (OperationAssetRole.OUTPUT.value, profile_name, str(path)),
        ).fetchone()
        return self._row_to_seed_image(row)

    def resolve_seed_image_by_file(
        self, profile_name: str, path: Path, sha256: str
    ) -> SeedImage | None:
        """The image whose recorded copy at ``path`` has exactly these bytes, newest first.

        A path alone is ambiguous: a re-run that overwrites the file adds a second row for
        the same path, and ``resolve_seed_image_by_path`` returns either (#934 review).
        """
        row = self._store.conn.execute(
            """
            SELECT a.id, a.profile_name, a.flow_project_id, a.flow_media_id,
                   a.flow_workflow_id, a.kind, a.width, a.height,
                   a.model, a.aspect_ratio, a.created_at,
                   o.prompt
            FROM assets a
            JOIN local_files lf ON lf.asset_id = a.id
            LEFT JOIN operation_assets oa ON oa.asset_id = a.id AND oa.role = ?
            LEFT JOIN operations o ON o.id = oa.operation_id
            WHERE a.profile_name = ?
              AND a.kind = 'image'
              AND lf.path = ?
              AND lf.sha256 = ?
            ORDER BY lf.created_at DESC
            LIMIT 1
            """,
            (OperationAssetRole.OUTPUT.value, profile_name, str(path), sha256),
        ).fetchone()
        return self._row_to_seed_image(row)

    def resolve_latest_image(
        self,
        profile_name: str,
        flow_project_id: str | None,
        model: str | None,
        aspect_ratio: str | None,
    ) -> SeedImage | None:
        clauses: list[str] = [
            "a.profile_name = ?",
            "a.kind = 'image'",
            "a.flow_project_id IS NOT NULL",
        ]
        params: list[object] = [profile_name]
        if flow_project_id is not None:
            clauses.append("a.flow_project_id = ?")
            params.append(flow_project_id)
        if model is not None:
            clauses.append("a.model = ?")
            params.append(model)
        if aspect_ratio is not None:
            clauses.append("a.aspect_ratio = ?")
            params.append(aspect_ratio)
        where = " AND ".join(clauses)
        sql = f"""
            SELECT a.id, a.profile_name, a.flow_project_id, a.flow_media_id,
                   a.flow_workflow_id, a.kind, a.width, a.height,
                   a.model, a.aspect_ratio, a.created_at,
                   o.prompt
            FROM assets a
            LEFT JOIN operation_assets oa ON oa.asset_id = a.id AND oa.role = ?
            LEFT JOIN operations o ON o.id = oa.operation_id
            WHERE {where}
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT 1
        """  # noqa: S608 - constants and ? placeholders only, values stay bound
        row = self._store.conn.execute(
            sql,
            [OperationAssetRole.OUTPUT.value, *params],
        ).fetchone()
        return self._row_to_seed_image(row)

    def list_project_images(self, profile_name: str, flow_project_id: str) -> list[SeedImage]:
        rows = self._store.conn.execute(
            """
            SELECT a.id, a.profile_name, a.flow_project_id, a.flow_media_id,
                   a.flow_workflow_id, a.kind, a.width, a.height,
                   a.model, a.aspect_ratio, a.created_at,
                   o.prompt
            FROM assets a
            LEFT JOIN operation_assets oa ON oa.asset_id = a.id AND oa.role = ?
            LEFT JOIN operations o ON o.id = oa.operation_id
            WHERE a.profile_name = ?
              AND a.flow_project_id = ?
              AND a.kind = 'image'
            ORDER BY a.created_at DESC, a.id DESC
            """,
            (OperationAssetRole.OUTPUT.value, profile_name, flow_project_id),
        ).fetchall()
        result: list[SeedImage] = []
        for row in rows:
            if row["flow_project_id"] is None:
                continue
            local_path = self._latest_local_path(str(row["id"]))
            result.append(
                SeedImage(
                    profile_name=str(row["profile_name"]),
                    flow_project_id=str(row["flow_project_id"]),
                    flow_media_id=str(row["flow_media_id"]),
                    flow_workflow_id=row["flow_workflow_id"],
                    kind=AssetKind(row["kind"]),
                    width=row["width"],
                    height=row["height"],
                    local_path=local_path,
                    prompt=row["prompt"],
                    model=row["model"],
                    aspect_ratio=row["aspect_ratio"],
                    created_at=str(row["created_at"]),
                ),
            )
        return result

    def find_incomplete_character(
        self,
        flow_project_id: str,
        name: str,
    ) -> dict[str, object] | None:
        """Return the most-recent STARTED CHARACTER operation for (project, name).

        Used by the create saga to detect a prior crash so it can resume
        without re-spending credits.  Returns a plain dict with keys::

            row_id           – operations.id (used to update the row later)
            entity_id        – flow_operation_id (the entityId already created)
            workflow_ids     – list[str] already recorded (may be empty)
            primary_media_ids – list[str] already recorded (may be empty)

        Returns ``None`` when no incomplete row exists for the given project
        and character name.
        """
        import json as _json

        row = self._store.conn.execute(
            """
            SELECT id, flow_operation_id, metadata_json
            FROM operations
            WHERE flow_project_id = ?
              AND mode = 'character'
              AND status = 'started'
              AND json_extract(metadata_json, '$.name') = ?
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (flow_project_id, name),
        ).fetchone()
        if row is None:
            return None
        meta: dict[str, object] = {}
        if row["metadata_json"]:
            try:
                meta = _json.loads(row["metadata_json"])
            except (ValueError, TypeError):
                meta = {}
        workflow_ids: list[str] = list(meta.get("workflow_ids") or [])  # type: ignore[arg-type]
        primary_media_ids: list[str] = list(meta.get("primary_media_ids") or [])  # type: ignore[arg-type]
        return {
            "row_id": str(row["id"]),
            "entity_id": str(row["flow_operation_id"]),
            "workflow_ids": workflow_ids,
            "primary_media_ids": primary_media_ids,
        }

    def _row_to_seed_image(self, row: Any) -> SeedImage | None:
        """Hydrate a SeedImage from an assets+prompt row, or None if the row is
        absent or lacks a flow_project_id. Shared by the resolve_* readers."""
        if row is None:
            return None
        if row["flow_project_id"] is None:
            return None
        local_path = self._latest_local_path(str(row["id"]))
        return SeedImage(
            profile_name=str(row["profile_name"]),
            flow_project_id=str(row["flow_project_id"]),
            flow_media_id=str(row["flow_media_id"]),
            flow_workflow_id=row["flow_workflow_id"],
            kind=AssetKind(row["kind"]),
            width=row["width"],
            height=row["height"],
            local_path=local_path,
            prompt=row["prompt"],
            model=row["model"],
            aspect_ratio=row["aspect_ratio"],
            created_at=str(row["created_at"]),
        )

    def _latest_local_path(self, asset_id: str) -> Path | None:
        row = self._store.conn.execute(
            """
            SELECT path, storage_provider FROM local_files
            WHERE asset_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (asset_id,),
        ).fetchone()
        if row is None:
            return None
        # Cloud-only rows have storage_provider set; path holds the cloud URI.
        if row["storage_provider"] is not None:
            return None
        return Path(str(row["path"]))
