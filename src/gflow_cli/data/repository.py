from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from gflow_cli.data.models import (
    AssetKind,
    AssetLookup,
    AssetRecord,
    ChainLinkRecord,
    LocalFileRecord,
    OperationAssetRole,
    OperationKind,
    OperationRecord,
    OperationStatus,
    ProjectRecord,
    SceneClipRecord,
    SceneRecord,
    SeedImage,
)
from gflow_cli.errors import DataIntegrityError

if TYPE_CHECKING:
    from gflow_cli.data.store import DataStore


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
                    ON CONFLICT(profile_name, flow_project_id) DO UPDATE SET
                        title = COALESCE(excluded.title, projects.title),
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
        return cast("ProjectRecord", dataclasses.replace(record, created_at=created_at))  # pyright: ignore[reportUnnecessaryCast]

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
        return cast("AssetRecord", dataclasses.replace(record, created_at=created_at))  # pyright: ignore[reportUnnecessaryCast]

    def update_asset_status(self, profile_name: str, flow_media_id: str, status: str) -> None:
        with self._store.transaction(immediate=True):
            self._store.conn.execute(
                "UPDATE assets SET status = ? WHERE profile_name = ? AND flow_media_id = ?",
                (status, profile_name, flow_media_id),
            )

    def get_asset_by_flow_media_id(
        self,
        profile_name: str,
        flow_media_id: str,
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
            SELECT id, profile_name, asset_id, path, media_type, bytes, sha256, created_at,
                   storage_provider, cloud_uri
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
    # Scenes
    # ------------------------------------------------------------------

    def upsert_scene(self, record: SceneRecord) -> SceneRecord:
        created_at = record.created_at or _utc_now()
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    INSERT INTO scenes(
                        id, profile_name, flow_project_id, flow_scene_id,
                        total_duration, source, output_path, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(profile_name, flow_scene_id) DO UPDATE SET
                        total_duration = excluded.total_duration,
                        source = excluded.source
                    """,
                    (
                        record.id,
                        record.profile_name,
                        record.flow_project_id,
                        record.flow_scene_id,
                        record.total_duration,
                        record.source,
                        record.output_path,
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.upsert_scene") from exc
        return cast("SceneRecord", dataclasses.replace(record, created_at=created_at))  # pyright: ignore[reportUnnecessaryCast]

    def set_scene_output(self, scene_id: str, output_path: str) -> None:
        """Record the rendered extended-video path on an existing scene row.

        Called after a successful server-side concat (the compose was already
        persisted by ``upsert_scene``), so a render failure never loses the
        compose and a later retry can re-attach the output.
        """
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    "UPDATE scenes SET output_path = ? WHERE id = ?",
                    (output_path, scene_id),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.set_scene_output") from exc

    def replace_scene_clips(self, scene_id: str, clips: list[SceneClipRecord]) -> None:
        rows = [
            (
                c.id,
                scene_id,
                c.position,
                c.flow_instance_workflow_id,
                c.flow_source_workflow_id,
                c.flow_media_id,
                c.start_time,
                c.end_time,
                c.total_duration,
                c.created_at or _utc_now(),
            )
            for c in clips
        ]
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    "DELETE FROM scene_clips WHERE scene_id = ?",
                    (scene_id,),
                )
                self._store.conn.executemany(
                    """
                    INSERT INTO scene_clips(
                        id, scene_id, position, flow_instance_workflow_id,
                        flow_source_workflow_id, flow_media_id,
                        start_time, end_time, total_duration, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.replace_scene_clips") from exc

    def get_scene_by_flow_scene_id(
        self,
        profile_name: str,
        flow_scene_id: str,
    ) -> SceneRecord | None:
        row = self._store.conn.execute(
            """
            SELECT id, profile_name, flow_project_id, flow_scene_id,
                   total_duration, source, output_path, created_at
            FROM scenes
            WHERE profile_name = ? AND flow_scene_id = ?
            """,
            (profile_name, flow_scene_id),
        ).fetchone()
        if row is None:
            return None
        return SceneRecord(
            id=str(row["id"]),
            profile_name=str(row["profile_name"]),
            flow_project_id=str(row["flow_project_id"]),
            flow_scene_id=str(row["flow_scene_id"]),
            total_duration=row["total_duration"],
            source=str(row["source"]),
            output_path=row["output_path"],
            created_at=row["created_at"],
        )

    def get_scene_clips(self, scene_id: str) -> list[SceneClipRecord]:
        rows = self._store.conn.execute(
            """
            SELECT id, scene_id, position, flow_instance_workflow_id,
                   flow_source_workflow_id, flow_media_id,
                   start_time, end_time, total_duration, created_at
            FROM scene_clips
            WHERE scene_id = ?
            ORDER BY position
            """,
            (scene_id,),
        ).fetchall()
        return [
            SceneClipRecord(
                id=str(row["id"]),
                scene_id=str(row["scene_id"]),
                position=int(row["position"]),
                flow_instance_workflow_id=str(row["flow_instance_workflow_id"]),
                flow_source_workflow_id=row["flow_source_workflow_id"],
                flow_media_id=row["flow_media_id"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                total_duration=row["total_duration"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Chain links
    # ------------------------------------------------------------------

    def upsert_chain_link(self, record: ChainLinkRecord) -> ChainLinkRecord:
        """Persist one chain link, keyed by (profile_name, chain_id, link_index).

        Idempotent: re-recording the same link (e.g. a resumed run that reaches
        an already-completed link) overwrites the prior row in place rather than
        raising. ``seed_frame_path`` is updated too, so attaching the extracted
        seed frame after the clip was recorded is a plain re-upsert.
        """
        created_at = record.created_at or _utc_now()
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    INSERT INTO chain_links(
                        id, profile_name, chain_id, link_index,
                        flow_project_id, flow_media_id, flow_operation_id,
                        prompt, local_path, seed_frame_path, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(profile_name, chain_id, link_index) DO UPDATE SET
                        flow_project_id = excluded.flow_project_id,
                        flow_media_id = excluded.flow_media_id,
                        flow_operation_id = excluded.flow_operation_id,
                        prompt = excluded.prompt,
                        local_path = excluded.local_path,
                        seed_frame_path = excluded.seed_frame_path
                    """,
                    (
                        record.id,
                        record.profile_name,
                        record.chain_id,
                        record.link_index,
                        record.flow_project_id,
                        record.flow_media_id,
                        record.flow_operation_id,
                        record.prompt,
                        record.local_path,
                        record.seed_frame_path,
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.upsert_chain_link") from exc
        return cast("ChainLinkRecord", dataclasses.replace(record, created_at=created_at))  # pyright: ignore[reportUnnecessaryCast]

    def completed_chain_links(self, profile_name: str, chain_id: str) -> list[ChainLinkRecord]:
        """Return the recorded links of ``chain_id`` whose clip is on disk.

        Ordered by ``link_index``. A returned link with ``seed_frame_path`` set
        is fully done (clip + seed frame); one with ``seed_frame_path is None``
        needs its seed frame re-extracted before the next link can be seeded
        (unless it is the final link). ``--resume-from`` consumes this to decide
        the restart point without re-paying for completed clips.
        """
        rows = self._store.conn.execute(
            """
            SELECT id, profile_name, chain_id, link_index,
                   flow_project_id, flow_media_id, flow_operation_id,
                   prompt, local_path, seed_frame_path, created_at
            FROM chain_links
            WHERE profile_name = ? AND chain_id = ?
            ORDER BY link_index
            """,
            (profile_name, chain_id),
        ).fetchall()
        return [
            ChainLinkRecord(
                id=str(row["id"]),
                profile_name=str(row["profile_name"]),
                chain_id=str(row["chain_id"]),
                link_index=int(row["link_index"]),
                flow_project_id=row["flow_project_id"],
                flow_media_id=str(row["flow_media_id"]),
                flow_operation_id=row["flow_operation_id"],
                prompt=row["prompt"],
                local_path=str(row["local_path"]),
                seed_frame_path=row["seed_frame_path"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Local files
    # ------------------------------------------------------------------

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
                ),
            )
        return result

    # ------------------------------------------------------------------
    # Character recovery
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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


# ------------------------------------------------------------------
# Row-to-dataclass helpers
# ------------------------------------------------------------------


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
