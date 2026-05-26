from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from gflow_cli.data.models import (
    AssetKind,
    AssetLookup,
    AssetRecord,
    LocalFileRecord,
    OperationAssetRole,
    OperationKind,
    OperationRecord,
    OperationStatus,
    ProjectRecord,
    SeedImage,
)
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataIntegrityError


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class DataRepository:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    @property
    def store(self) -> DataStore:
        return self._store

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def upsert_profile(self, name: str, profile_dir: Path) -> None:
        now = _utc_now()
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    INSERT INTO profiles(name, profile_dir, first_seen_at, last_used_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        profile_dir = excluded.profile_dir,
                        last_used_at = excluded.last_used_at
                    """,
                    (name, str(profile_dir), now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.upsert_profile") from exc

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def upsert_project(self, record: ProjectRecord) -> ProjectRecord:
        now = _utc_now()
        created_at = record.created_at or now
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    INSERT INTO projects(
                        id, profile_name, flow_project_id, title, source, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        profile_name = excluded.profile_name,
                        flow_project_id = excluded.flow_project_id,
                        title = excluded.title,
                        source = excluded.source
                    """,
                    (
                        record.id,
                        record.profile_name,
                        record.flow_project_id,
                        record.title,
                        record.source,
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.upsert_project") from exc
        return cast(ProjectRecord, dataclasses.replace(record, created_at=created_at))  # pyright: ignore[reportUnnecessaryCast]

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------

    def upsert_asset(self, record: AssetRecord) -> AssetRecord:
        now = _utc_now()
        created_at = record.created_at or now
        metadata = json.dumps(record.metadata_json, sort_keys=True)
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    INSERT INTO assets(
                        id, profile_name, flow_project_id, flow_media_id,
                        flow_workflow_id, flow_media_generation_id,
                        kind, status, model, aspect_ratio,
                        width, height, duration_seconds, seed,
                        created_at, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        profile_name = excluded.profile_name,
                        flow_project_id = excluded.flow_project_id,
                        flow_media_id = excluded.flow_media_id,
                        flow_workflow_id = excluded.flow_workflow_id,
                        flow_media_generation_id = excluded.flow_media_generation_id,
                        kind = excluded.kind,
                        status = excluded.status,
                        model = excluded.model,
                        aspect_ratio = excluded.aspect_ratio,
                        width = excluded.width,
                        height = excluded.height,
                        duration_seconds = excluded.duration_seconds,
                        seed = excluded.seed,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        record.id,
                        record.profile_name,
                        record.flow_project_id,
                        record.flow_media_id,
                        record.flow_workflow_id,
                        record.flow_media_generation_id,
                        record.kind.value,
                        record.status,
                        record.model,
                        record.aspect_ratio,
                        record.width,
                        record.height,
                        record.duration_seconds,
                        record.seed,
                        created_at,
                        metadata,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.upsert_asset") from exc
        return cast(AssetRecord, dataclasses.replace(record, created_at=created_at))  # pyright: ignore[reportUnnecessaryCast]

    def update_asset_status(self, profile_name: str, flow_media_id: str, status: str) -> None:
        with self._store.transaction(immediate=True):
            self._store.conn.execute(
                "UPDATE assets SET status = ? WHERE profile_name = ? AND flow_media_id = ?",
                (status, profile_name, flow_media_id),
            )

    def get_asset_by_flow_media_id(
        self, profile_name: str, flow_media_id: str
    ) -> AssetLookup | None:
        row = self._store.conn.execute(
            """
            SELECT id, profile_name, flow_project_id, flow_media_id, kind
            FROM assets
            WHERE profile_name = ? AND flow_media_id = ?
            """,
            (profile_name, flow_media_id),
        ).fetchone()
        if row is None:
            return None
        return self._hydrate_asset_lookup(row)

    def find_assets_by_flow_media_id(self, flow_media_id: str) -> list[AssetLookup]:
        """Return every asset matching ``flow_media_id`` across all profiles.

        Used by read-only catalog queries (e.g. ``gflow data media <id>`` when
        ``--profile`` is omitted) where the caller does not yet know which
        profile owns the row. Returns an empty list when nothing matches.
        Closes #87.
        """
        rows = self._store.conn.execute(
            """
            SELECT id, profile_name, flow_project_id, flow_media_id, kind
            FROM assets
            WHERE flow_media_id = ?
            ORDER BY profile_name ASC, id ASC
            """,
            (flow_media_id,),
        ).fetchall()
        return [self._hydrate_asset_lookup(row) for row in rows]

    def _hydrate_asset_lookup(self, row: sqlite3.Row) -> AssetLookup:
        file_rows = self._store.conn.execute(
            """
            SELECT id, profile_name, asset_id, path, media_type, bytes, sha256, created_at
            FROM local_files
            WHERE profile_name = ? AND asset_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (row["profile_name"], row["id"]),
        ).fetchall()
        local_files = [_row_to_local_file(r) for r in file_rows]
        return AssetLookup(
            id=str(row["id"]),
            profile_name=str(row["profile_name"]),
            flow_project_id=row["flow_project_id"],
            flow_media_id=str(row["flow_media_id"]),
            kind=AssetKind(row["kind"]),
            local_files=local_files,
        )

    def candidate_image_exists(self, profile_name: str, flow_media_id: str) -> bool:
        row = self._store.conn.execute(
            """
            SELECT 1 FROM assets
            WHERE profile_name = ? AND flow_media_id = ?
              AND kind = 'image' AND flow_project_id IS NOT NULL
            """,
            (profile_name, flow_media_id),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

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
                        flow_operation_id, flow_batch_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.insert_operation") from exc
        return cast(OperationRecord, dataclasses.replace(record, started_at=started_at))  # pyright: ignore[reportUnnecessaryCast]

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

    def get_operation_for_output_asset(
        self, profile_name: str, flow_media_id: str, mode: OperationKind
    ) -> OperationRecord | None:
        row = self._store.conn.execute(
            """
            SELECT o.id, o.profile_name, o.flow_project_id, o.command, o.mode,
                   o.prompt, o.prompt_hash, o.prompt_redacted, o.model, o.aspect_ratio,
                   o.status, o.started_at, o.completed_at,
                   o.error_type, o.error_detail,
                   o.flow_operation_id, o.flow_batch_id
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

    # ------------------------------------------------------------------
    # Operation-asset links
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Local files
    # ------------------------------------------------------------------

    def upsert_local_file(self, record: LocalFileRecord) -> LocalFileRecord:
        now = _utc_now()
        created_at = record.created_at or now
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    INSERT INTO local_files(
                        id, profile_name, asset_id, path,
                        sha256, bytes, media_type, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(asset_id, path) DO UPDATE SET
                        id = excluded.id,
                        profile_name = excluded.profile_name,
                        sha256 = excluded.sha256,
                        bytes = excluded.bytes,
                        media_type = excluded.media_type
                    """,
                    (
                        record.id,
                        record.profile_name,
                        record.asset_id,
                        str(record.path),
                        record.sha256,
                        record.bytes,
                        record.media_type,
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.upsert_local_file") from exc
        return cast(LocalFileRecord, dataclasses.replace(record, created_at=created_at))  # pyright: ignore[reportUnnecessaryCast]

    # ------------------------------------------------------------------
    # Seed image resolvers
    # ------------------------------------------------------------------

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
        """
        row = self._store.conn.execute(
            sql,
            [OperationAssetRole.OUTPUT.value, *params],
        ).fetchone()
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
                )
            )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _latest_local_path(self, asset_id: str) -> Path | None:
        row = self._store.conn.execute(
            """
            SELECT path FROM local_files
            WHERE asset_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (asset_id,),
        ).fetchone()
        if row is None:
            return None
        return Path(str(row["path"]))


# ------------------------------------------------------------------
# Row-to-dataclass helpers
# ------------------------------------------------------------------


def _row_to_local_file(row: sqlite3.Row) -> LocalFileRecord:
    return LocalFileRecord(
        id=str(row["id"]),
        profile_name=str(row["profile_name"]),
        asset_id=str(row["asset_id"]),
        path=Path(str(row["path"])),
        media_type=row["media_type"],
        bytes=row["bytes"],
        sha256=row["sha256"],
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
    )
