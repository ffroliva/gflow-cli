"""``DataRepository`` table group: scenes, their clips and chain links (#1249 wave 2).

Moved out of ``repository.py`` unchanged.
"""

# pyright: reportPrivateUsage=false
# The shared helpers are private to the data package; this module is one of its parts.

from __future__ import annotations

import dataclasses
import sqlite3
from typing import TYPE_CHECKING, cast

from gflow_cli.data.models import (
    ChainLinkRecord,
    SceneClipRecord,
    SceneRecord,
)
from gflow_cli.errors import DataIntegrityError

if TYPE_CHECKING:
    pass


from gflow_cli.data._repo_base import (
    RepositoryBase,
    _utc_now,
)


class ScenesChainsRepo(RepositoryBase):
    """Scenes, their clips and chain links."""

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
