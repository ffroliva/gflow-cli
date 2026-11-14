"""``DataRepository`` table group: assets, their names and remote presence (#1249 wave 2).

Moved out of ``repository.py`` unchanged.
"""

# pyright: reportPrivateUsage=false
# The shared helpers are private to the data package; this module is one of its parts.

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from gflow_cli.data.models import (
    AssetKind,
    AssetLookup,
    AssetRecord,
)
from gflow_cli.errors import DataIntegrityError

if TYPE_CHECKING:
    from collections.abc import Sequence


from gflow_cli.data._repo_base import (
    _ASSET_LOOKUP_COLUMNS,
    ProjectWork,
    RepositoryBase,
    _coerce_utc,
    _decode_metadata_json,
    _row_to_local_file,
    _utc_now,
)


class AssetsRepo(RepositoryBase):
    """Assets, their names and remote presence."""

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
                    -- Conflict on `id`, NOT on UNIQUE(profile_name, flow_media_id), on
                    -- purpose (#898): callers link operations by the id they pass, and
                    -- the image paths rely on a repeated media id raising so that
                    -- escalate_asset_collision can name an attribution collision. A
                    -- caller re-recording a known media id looks its row up first.
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

    def set_asset_display_name(
        self,
        profile_name: str,
        flow_media_id: str,
        name: str,
        *,
        source: str,
    ) -> bool:
        """Atomically patch ``$.display_name`` + ``$.sync`` provenance (#543).

        Single-statement ``json_set`` inside ``BEGIN IMMEDIATE``: sets
        ``$.display_name``, ``$.sync.named_at`` (UTC ISO), ``$.sync.source``
        while preserving unrelated metadata keys. A changed name is
        overwritten (rename semantics). Returns False without writing when
        the media id is unknown (never inserts) or the stored name is already
        identical — the WHERE predicate prevents timestamp churn.
        """
        with self._store.transaction(immediate=True):
            cursor = self._store.conn.execute(
                """
                UPDATE assets
                SET metadata_json = json_set(
                    COALESCE(metadata_json, '{}'),
                    '$.display_name', ?,
                    '$.sync.named_at', ?,
                    '$.sync.source', ?
                )
                WHERE profile_name = ? AND flow_media_id = ?
                  AND COALESCE(json_extract(metadata_json, '$.display_name'), '') != ?
                """,
                (name, _utc_now(), source, profile_name, flow_media_id, name),
            )
            changed = cursor.rowcount
        return changed > 0

    def mark_asset_missing_remote(self, profile_name: str, flow_media_id: str) -> bool:
        """Flag an asset as absent from a complete remote listing (#543).

        FLAG semantics only: sets ``$.sync.status = 'missing_remote'`` +
        ``$.sync.checked_at`` (UTC ISO) via a single atomic ``json_set`` —
        never deletes rows or nulls other metadata. Returns False for an
        unknown media id (never inserts).
        """
        with self._store.transaction(immediate=True):
            cursor = self._store.conn.execute(
                """
                UPDATE assets
                SET metadata_json = json_set(
                    COALESCE(metadata_json, '{}'),
                    '$.sync.status', 'missing_remote',
                    '$.sync.checked_at', ?
                )
                WHERE profile_name = ? AND flow_media_id = ?
                """,
                (_utc_now(), profile_name, flow_media_id),
            )
            changed = cursor.rowcount
        return changed > 0

    def clear_missing_remote(self, profile_name: str, flow_media_id: str) -> bool:
        """Un-ghost an asset whose media reappeared in a listing (#543).

        Single atomic statement: ``json_remove`` drops ONLY ``$.sync.status``
        (the ``$.sync`` object itself survives — ``named_at`` and other keys
        stay) and ``json_set`` re-stamps ``$.sync.checked_at`` (UTC ISO).
        The WHERE predicate requires the row to currently be flagged
        ``missing_remote`` — returns False for non-ghost or unknown ids,
        never touching their metadata.
        """
        with self._store.transaction(immediate=True):
            cursor = self._store.conn.execute(
                """
                UPDATE assets
                SET metadata_json = json_set(
                    json_remove(metadata_json, '$.sync.status'),
                    '$.sync.checked_at', ?
                )
                WHERE profile_name = ? AND flow_media_id = ?
                  AND json_extract(metadata_json, '$.sync.status') = 'missing_remote'
                """,
                (_utc_now(), profile_name, flow_media_id),
            )
            changed = cursor.rowcount
        return changed > 0

    def list_missing_remote_media(self, profile_name: str, flow_project_id: str) -> tuple[str, ...]:
        """Ghost-marked media ids of one project, for the un-ghost pass (#543)."""
        rows = self._store.conn.execute(
            """
            SELECT flow_media_id
            FROM assets
            WHERE profile_name = ? AND flow_project_id = ?
              AND json_extract(metadata_json, '$.sync.status') = 'missing_remote'
            ORDER BY created_at ASC, id ASC
            """,
            (profile_name, flow_project_id),
        ).fetchall()
        return tuple(str(row["flow_media_id"]) for row in rows)

    def list_nameless_asset_projects(
        self,
        profile_name: str,
        *,
        limit: int | None = None,
        since: datetime | None = None,
        project_ids: Sequence[str] | None = None,
    ) -> list[ProjectWork]:
        """Group nameless, non-ghost asset rows per project for the sync sweep.

        Nameless = ``metadata_json`` NULL / no ``$.display_name`` / empty
        string; rows already flagged ``$.sync.status = 'missing_remote'`` are
        excluded. ``since`` drops individual rows created before it (which
        also drops projects left with no qualifying rows); ``project_ids``
        scopes projects; ``limit`` caps the project count. Ordered by each
        project's earliest qualifying asset ``created_at`` DESCENDING.
        """
        clauses = [
            "profile_name = ?",
            "flow_project_id IS NOT NULL",
            "COALESCE(json_extract(metadata_json, '$.display_name'), '') = ''",
            "COALESCE(json_extract(metadata_json, '$.sync.status'), '') != 'missing_remote'",
        ]
        params: list[object] = [profile_name]
        if since is not None:
            # created_at is stored as recorder-format UTC ISO ("...Z") —
            # normalize to the same shape so string comparison is chronological.
            clauses.append("created_at >= ?")
            params.append(
                _coerce_utc(since)
                .astimezone(UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        if project_ids is not None:
            if not project_ids:
                return []
            placeholders = ",".join("?" * len(project_ids))
            clauses.append(f"flow_project_id IN ({placeholders})")
            params.extend(project_ids)
        rows = self._store.conn.execute(
            f"""
            SELECT flow_project_id, flow_media_id, created_at
            FROM assets
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at ASC, id ASC
            """,  # noqa: S608 - constants and ? placeholders only, values stay bound
            params,
        ).fetchall()
        grouped: dict[str, list[str]] = {}
        earliest: dict[str, str] = {}
        for row in rows:
            project_id = str(row["flow_project_id"])
            grouped.setdefault(project_id, []).append(str(row["flow_media_id"]))
            earliest.setdefault(project_id, str(row["created_at"]))
        ordered = sorted(grouped, key=lambda pid: earliest[pid], reverse=True)
        if limit is not None:
            ordered = ordered[:limit]
        return [ProjectWork(flow_project_id=pid, media_ids=tuple(grouped[pid])) for pid in ordered]

    def get_asset_by_flow_media_id(
        self,
        profile_name: str,
        flow_media_id: str,
    ) -> AssetLookup | None:
        row = self._store.conn.execute(
            f"""
            SELECT {_ASSET_LOOKUP_COLUMNS}
            FROM assets
            WHERE profile_name = ? AND flow_media_id = ?
            """,  # noqa: S608 - constants and ? placeholders only, values stay bound
            (profile_name, flow_media_id),
        ).fetchone()
        if row is None:
            return None
        return self._hydrate_asset_lookup(row)

    def get_asset_by_any_id(
        self,
        profile_name: str,
        ref_id: str,
    ) -> AssetLookup | None:
        row = self._store.conn.execute(
            f"""
            SELECT {_ASSET_LOOKUP_COLUMNS}
            FROM assets
            WHERE profile_name = ? AND (flow_media_id = ? OR flow_workflow_id = ? OR id = ?)
            ORDER BY created_at DESC
            LIMIT 1
            """,  # noqa: S608 - constants and ? placeholders only, values stay bound
            (profile_name, ref_id, ref_id, ref_id),
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
            f"""
            SELECT {_ASSET_LOOKUP_COLUMNS}
            FROM assets
            WHERE flow_media_id = ?
            ORDER BY profile_name ASC, id ASC
            """,  # noqa: S608 - constants and ? placeholders only, values stay bound
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
            flow_workflow_id=row["flow_workflow_id"],
            kind=AssetKind(row["kind"]),
            local_files=local_files,
            metadata_json=_decode_metadata_json(row["metadata_json"]),
            seed=row["seed"],
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
