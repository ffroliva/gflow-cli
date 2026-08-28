"""Read-only query functions over the gflow-cli SQLite catalog.

These are pure functions: they accept a db path, return frozen dataclass rows,
do not mutate state, and have no dependency on Click or Rich. The CLI adapter
(cli_data.py, Phase 2 Task 2.6) formats their output.

This module is the foundation for v0.10's ``show``/``search``/``export``
sub-commands; additional query functions (list_images, list_videos,
list_profiles) will be added in Tasks 2.3–2.5.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@contextmanager
def _safe_db(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Open the catalog DB via :class:`DataStore` and yield a connection.

    Routes through ``DataStore.open`` so schema migrations are applied even on
    a freshly created (or just-deleted) DB file. The previous implementation
    used a raw ``sqlite3.connect`` and skipped migrations, which made any
    ``gflow data list ...`` invocation crash with ``no such table: assets``
    on an empty DB. Closes #88.

    Query-time sqlite errors are mapped to :class:`DataStoreError`. DB open /
    migration errors propagate from ``DataStore.open`` as their typed errors.
    """
    with DataStore.open(db_path) as store:
        try:
            yield store.conn
        except sqlite3.Error as exc:
            msg = f"Catalog query failed: {exc}"
            raise DataStoreError(msg) from exc


@dataclass(frozen=True)
class ProjectRow:
    project_id: str
    profile: str
    created_at: datetime
    image_count: int
    video_count: int
    title: str | None = None


_LIST_PROJECTS_SQL = """
    SELECT
        p.flow_project_id AS project_id,
        p.profile_name    AS profile,
        p.created_at      AS created_at,
        p.title           AS title,
        (SELECT COUNT(*) FROM assets
         WHERE kind = 'image'
           AND flow_project_id = p.flow_project_id
           AND profile_name = p.profile_name) AS image_count,
        (SELECT COUNT(*) FROM assets
         WHERE kind = 'video'
           AND flow_project_id = p.flow_project_id
           AND profile_name = p.profile_name) AS video_count
    FROM projects p
    WHERE (:profile IS NULL OR p.profile_name = :profile)
    ORDER BY p.created_at DESC
    LIMIT :limit OFFSET :offset
"""


def list_projects(
    *,
    db_path: Path,
    profile: str | None,
    limit: int,
    offset: int,
) -> list[ProjectRow]:
    """Return projects newest-first, filtered by profile if given.

    image_count / video_count are aggregated via subqueries over the assets
    table (no dedicated images/videos tables in this schema).

    Args:
        db_path: Absolute path to the SQLite catalog.
        profile: If given, only return projects belonging to this profile name.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip (for pagination).

    Returns:
        A list of :class:`ProjectRow` instances ordered newest-first.
    """
    params = {"profile": profile, "limit": limit, "offset": offset}
    with _safe_db(db_path) as conn:
        rows = conn.execute(_LIST_PROJECTS_SQL, params).fetchall()
    return [
        ProjectRow(
            project_id=str(r["project_id"]),
            profile=str(r["profile"]),
            created_at=datetime.fromisoformat(str(r["created_at"])),
            image_count=int(r["image_count"]),
            video_count=int(r["video_count"]),
            title=str(r["title"]) if r["title"] is not None else None,
        )
        for r in rows
    ]


# ─── ImageRow + list_images ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ImageRow:
    media_id: str
    profile: str
    project_id: str | None
    prompt: str | None
    aspect: str | None
    model: str | None
    created_at: datetime
    local_path: str | None
    copy_count: int = 1


# Aggregated: one row per asset, local_files collapsed into a subquery.
_LIST_IMAGES_SQL = """
    SELECT
        a.flow_media_id   AS media_id,
        a.profile_name    AS profile,
        a.flow_project_id AS project_id,
        op.prompt         AS prompt,
        a.aspect_ratio    AS aspect,
        a.model           AS model,
        a.created_at      AS created_at,
        COALESCE(lfa.copy_count, 0) AS copy_count,
        lfa.latest_path   AS local_path
    FROM assets a
    LEFT JOIN (
        SELECT oa2.asset_id, MAX(o2.prompt) AS prompt
          FROM operation_assets oa2
          JOIN operations o2 ON o2.id = oa2.operation_id
         WHERE oa2.role = 'output'
         GROUP BY oa2.asset_id
    ) op ON op.asset_id = a.id
    LEFT JOIN (
        SELECT asset_id, COUNT(*) AS copy_count, MAX(path) AS latest_path
          FROM local_files
         GROUP BY asset_id
    ) lfa ON lfa.asset_id = a.id
    WHERE a.kind = 'image'
      AND (:profile IS NULL OR a.profile_name = :profile)
    ORDER BY a.created_at DESC
    LIMIT :limit OFFSET :offset
"""

# Flat: one row per local_file (current row-per-file behaviour).
_LIST_IMAGES_ALL_COPIES_SQL = """
    SELECT
        a.flow_media_id   AS media_id,
        a.profile_name    AS profile,
        a.flow_project_id AS project_id,
        op.prompt         AS prompt,
        a.aspect_ratio    AS aspect,
        a.model           AS model,
        a.created_at      AS created_at,
        1                 AS copy_count,
        lf.path           AS local_path
    FROM assets a
    LEFT JOIN (
        SELECT oa2.asset_id, MAX(o2.prompt) AS prompt
          FROM operation_assets oa2
          JOIN operations o2 ON o2.id = oa2.operation_id
         WHERE oa2.role = 'output'
         GROUP BY oa2.asset_id
    ) op ON op.asset_id = a.id
    LEFT JOIN local_files lf ON lf.asset_id = a.id
    WHERE a.kind = 'image'
      AND (:profile IS NULL OR a.profile_name = :profile)
    ORDER BY a.created_at DESC
    LIMIT :limit OFFSET :offset
"""


_ASSET_PROMPT_SQL = """
    SELECT o.prompt AS prompt
      FROM assets a
      JOIN operation_assets oa ON oa.asset_id = a.id AND oa.role = 'output'
      JOIN operations o ON o.id = oa.operation_id
     WHERE a.flow_media_id = :media_id
       AND o.prompt IS NOT NULL
     ORDER BY o.started_at DESC
     LIMIT 1
"""


def get_asset_prompt(*, db_path: Path, media_id: str) -> str | None:
    """Recorded generation prompt for an asset, by its Flow media UUID.

    #287 round 6: the media picker's search does not index UUIDs, but each
    picker tile's alt text carries the generation PROMPT — the CLI resolves
    the prompt here (the CLI layer owns catalog access) and hands its first
    words to the transport as picker search hints. The latest
    output-operation prompt wins.

    Args:
        db_path: Absolute path to the SQLite catalog.
        media_id: The asset's ``flow_media_id`` (Flow media UUID).

    Returns:
        The prompt text, or ``None`` when the asset is unknown or has no
        recorded prompt.
    """
    params = {"media_id": media_id}
    with _safe_db(db_path) as conn:
        row = conn.execute(_ASSET_PROMPT_SQL, params).fetchone()
    if row is None or row["prompt"] is None:
        return None
    return str(row["prompt"])


def list_images(
    *,
    db_path: Path,
    profile: str | None,
    limit: int,
    offset: int,
    all_copies: bool = False,
) -> list[ImageRow]:
    """Return image assets newest-first, filtered by profile if given.

    By default returns one row per asset with ``copy_count`` showing how many
    local copies exist.  Pass ``all_copies=True`` for one row per local_file
    (the pre-aggregation behaviour).

    Args:
        db_path: Absolute path to the SQLite catalog.
        profile: If given, only return images belonging to this profile name.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip (for pagination).
        all_copies: If True, emit one row per local_file instead of one per asset.

    Returns:
        A list of :class:`ImageRow` instances ordered newest-first.
    """
    sql = _LIST_IMAGES_ALL_COPIES_SQL if all_copies else _LIST_IMAGES_SQL
    params = {"profile": profile, "limit": limit, "offset": offset}
    with _safe_db(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        ImageRow(
            media_id=str(r["media_id"]),
            profile=str(r["profile"]),
            project_id=str(r["project_id"]) if r["project_id"] is not None else None,
            prompt=str(r["prompt"]) if r["prompt"] is not None else None,
            aspect=str(r["aspect"]) if r["aspect"] is not None else None,
            model=str(r["model"]) if r["model"] is not None else None,
            created_at=datetime.fromisoformat(str(r["created_at"])),
            local_path=str(r["local_path"]) if r["local_path"] is not None else None,
            copy_count=int(r["copy_count"]),
        )
        for r in rows
    ]


# ─── VideoRow + list_videos ───────────────────────────────────────────────────


@dataclass(frozen=True)
class VideoRow:
    media_id: str
    profile: str
    project_id: str | None
    prompt: str | None
    aspect: str | None
    model: str | None
    duration: float | None
    created_at: datetime
    local_path: str | None
    copy_count: int = 1


# Aggregated: one row per asset, local_files collapsed into a subquery.
_LIST_VIDEOS_SQL = """
    SELECT
        a.flow_media_id   AS media_id,
        a.profile_name    AS profile,
        a.flow_project_id AS project_id,
        op.prompt         AS prompt,
        a.aspect_ratio    AS aspect,
        a.model           AS model,
        a.duration_seconds AS duration,
        a.created_at      AS created_at,
        COALESCE(lfa.copy_count, 0) AS copy_count,
        lfa.latest_path   AS local_path
    FROM assets a
    LEFT JOIN (
        SELECT oa2.asset_id, MAX(o2.prompt) AS prompt
          FROM operation_assets oa2
          JOIN operations o2 ON o2.id = oa2.operation_id
         WHERE oa2.role = 'output'
         GROUP BY oa2.asset_id
    ) op ON op.asset_id = a.id
    LEFT JOIN (
        SELECT asset_id, COUNT(*) AS copy_count, MAX(path) AS latest_path
          FROM local_files
         GROUP BY asset_id
    ) lfa ON lfa.asset_id = a.id
    WHERE a.kind = 'video'
      AND (:profile IS NULL OR a.profile_name = :profile)
    ORDER BY a.created_at DESC
    LIMIT :limit OFFSET :offset
"""

# Flat: one row per local_file (current row-per-file behaviour).
_LIST_VIDEOS_ALL_COPIES_SQL = """
    SELECT
        a.flow_media_id   AS media_id,
        a.profile_name    AS profile,
        a.flow_project_id AS project_id,
        op.prompt         AS prompt,
        a.aspect_ratio    AS aspect,
        a.model           AS model,
        a.duration_seconds AS duration,
        a.created_at      AS created_at,
        1                 AS copy_count,
        lf.path           AS local_path
    FROM assets a
    LEFT JOIN (
        SELECT oa2.asset_id, MAX(o2.prompt) AS prompt
          FROM operation_assets oa2
          JOIN operations o2 ON o2.id = oa2.operation_id
         WHERE oa2.role = 'output'
         GROUP BY oa2.asset_id
    ) op ON op.asset_id = a.id
    LEFT JOIN local_files lf ON lf.asset_id = a.id
    WHERE a.kind = 'video'
      AND (:profile IS NULL OR a.profile_name = :profile)
    ORDER BY a.created_at DESC
    LIMIT :limit OFFSET :offset
"""


def list_videos(
    *,
    db_path: Path,
    profile: str | None,
    limit: int,
    offset: int,
    all_copies: bool = False,
) -> list[VideoRow]:
    """Return video assets newest-first, filtered by profile if given.

    By default returns one row per asset with ``copy_count`` showing how many
    local copies exist.  Pass ``all_copies=True`` for one row per local_file.

    Args:
        db_path: Absolute path to the SQLite catalog.
        profile: If given, only return videos belonging to this profile name.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip (for pagination).
        all_copies: If True, emit one row per local_file instead of one per asset.

    Returns:
        A list of :class:`VideoRow` instances ordered newest-first.
    """
    sql = _LIST_VIDEOS_ALL_COPIES_SQL if all_copies else _LIST_VIDEOS_SQL
    params = {"profile": profile, "limit": limit, "offset": offset}
    with _safe_db(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        VideoRow(
            media_id=str(r["media_id"]),
            profile=str(r["profile"]),
            project_id=str(r["project_id"]) if r["project_id"] is not None else None,
            prompt=str(r["prompt"]) if r["prompt"] is not None else None,
            aspect=str(r["aspect"]) if r["aspect"] is not None else None,
            model=str(r["model"]) if r["model"] is not None else None,
            duration=float(r["duration"]) if r["duration"] is not None else None,
            created_at=datetime.fromisoformat(str(r["created_at"])),
            local_path=str(r["local_path"]) if r["local_path"] is not None else None,
            copy_count=int(r["copy_count"]),
        )
        for r in rows
    ]


# ─── OperationErrorRow + list_errors ──────────────────────────────────────────


@dataclass(frozen=True)
class OperationErrorRow:
    started_at: datetime
    completed_at: datetime | None
    profile: str
    command: str | None
    mode: str
    model: str | None
    error_type: str | None
    error_detail: str | None


def _as_utc(dt: datetime) -> datetime:
    """Coerce a catalog timestamp to UTC-aware.

    Timestamps are written UTC-aware ("…Z", see ``repository._utc_now``), but a
    legacy/naive value must still compare against an aware cutoff without raising
    — assume UTC when tzinfo is absent.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _row_to_operation_error(r: Any) -> OperationErrorRow:
    return OperationErrorRow(
        started_at=datetime.fromisoformat(str(r["started_at"])),
        completed_at=(
            datetime.fromisoformat(str(r["completed_at"]))
            if r["completed_at"] is not None
            else None
        ),
        profile=str(r["profile"]),
        command=str(r["command"]) if r["command"] is not None else None,
        mode=str(r["mode"]),
        model=str(r["model"]) if r["model"] is not None else None,
        error_type=str(r["error_type"]) if r["error_type"] is not None else None,
        error_detail=str(r["error_detail"]) if r["error_detail"] is not None else None,
    )


_LIST_ERRORS_SQL = """
    SELECT
        o.started_at   AS started_at,
        o.completed_at AS completed_at,
        o.profile_name AS profile,
        o.command      AS command,
        o.mode         AS mode,
        o.model        AS model,
        o.error_type   AS error_type,
        o.error_detail AS error_detail
    FROM operations o
    WHERE o.status = 'failed'
      AND (:profile IS NULL OR o.profile_name = :profile)
    ORDER BY o.started_at DESC
    LIMIT :limit OFFSET :offset
"""


def list_errors(
    *,
    db_path: Path,
    profile: str | None,
    limit: int,
    offset: int,
) -> list[OperationErrorRow]:
    """Return FAILED operations newest-first, filtered by profile if given (#341).

    Reads only the persisted (already-redacted) ``error_type``/``error_detail``
    columns — never re-derives detail from a live exception.

    Args:
        db_path: Absolute path to the SQLite catalog.
        profile: If given, only return failures belonging to this profile name.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip (for pagination).

    Returns:
        A list of :class:`OperationErrorRow` instances ordered newest-first.
    """
    params = {"profile": profile, "limit": limit, "offset": offset}
    with _safe_db(db_path) as conn:
        rows = conn.execute(_LIST_ERRORS_SQL, params).fetchall()
    return [_row_to_operation_error(r) for r in rows]


_EXPORT_ERRORS_SQL = """
    SELECT
        o.started_at   AS started_at,
        o.completed_at AS completed_at,
        o.profile_name AS profile,
        o.command      AS command,
        o.mode         AS mode,
        o.model        AS model,
        o.error_type   AS error_type,
        o.error_detail AS error_detail
    FROM operations o
    WHERE o.status = 'failed'
      AND (:profile IS NULL OR o.profile_name = :profile)
    ORDER BY o.started_at DESC
"""


def export_errors(
    *,
    db_path: Path,
    profile: str | None = None,
    older_than: timedelta | None = None,
) -> list[OperationErrorRow]:
    """Return ALL failed operations newest-first, for archival (#345).

    Like :func:`list_errors` but unbounded (no ``limit``/``offset``) and with an
    optional ``older_than`` age filter, so an operator can archive the exact set
    they are about to prune. Pure read; the CLI serializes the rows to JSONL.

    Args:
        db_path: Absolute path to the SQLite catalog.
        profile: If given, only return failures belonging to this profile name.
        older_than: If given, only return failures whose ``started_at`` is older
            than ``now - older_than``. When ``None``, every failure is returned.

    Returns:
        A list of :class:`OperationErrorRow` instances ordered newest-first.
    """
    with _safe_db(db_path) as conn:
        rows = conn.execute(_EXPORT_ERRORS_SQL, {"profile": profile}).fetchall()
    result = [_row_to_operation_error(r) for r in rows]
    if older_than is not None:
        cutoff = datetime.now(UTC) - older_than
        result = [row for row in result if _as_utc(row.started_at) < cutoff]
    return result


# ─── ProfileRow + list_profiles ───────────────────────────────────────────────


@dataclass(frozen=True)
class ProfileRow:
    profile_name: str
    last_used_at: datetime
    project_count: int
    image_count: int
    video_count: int


_LIST_PROFILES_SQL = """
    WITH catalog_activity AS (
        SELECT profile_name, MAX(created_at) AS last_used_at
          FROM projects
         GROUP BY profile_name
        UNION ALL
        SELECT profile_name, MAX(created_at) AS last_used_at
          FROM assets
         GROUP BY profile_name
    )
    SELECT
        profile_name,
        MAX(last_used_at) AS last_used_at,
        (SELECT COUNT(DISTINCT flow_project_id) FROM projects
          WHERE profile_name = ca.profile_name) AS project_count,
        (SELECT COUNT(*) FROM assets
          WHERE kind = 'image' AND profile_name = ca.profile_name) AS image_count,
        (SELECT COUNT(*) FROM assets
          WHERE kind = 'video' AND profile_name = ca.profile_name) AS video_count
    FROM catalog_activity ca
    GROUP BY profile_name
    ORDER BY last_used_at DESC
    LIMIT :limit OFFSET :offset
"""


def list_profiles(
    *,
    db_path: Path,
    limit: int,
    offset: int,
) -> list[ProfileRow]:
    """Return profiles with aggregate counts, sorted by most-recently-active.

    Only profiles with at least one project or one asset appear. There is no
    per-profile filter parameter — callers wanting a single profile can filter
    the returned list.

    Args:
        db_path: Absolute path to the SQLite catalog.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip (for pagination).

    Returns:
        A list of :class:`ProfileRow` instances ordered newest-first by
        last_used_at.
    """
    params = {"limit": limit, "offset": offset}
    with _safe_db(db_path) as conn:
        rows = conn.execute(_LIST_PROFILES_SQL, params).fetchall()
    return [
        ProfileRow(
            profile_name=str(r["profile_name"]),
            last_used_at=datetime.fromisoformat(str(r["last_used_at"])),
            project_count=int(r["project_count"]),
            image_count=int(r["image_count"]),
            video_count=int(r["video_count"]),
        )
        for r in rows
    ]


def list_project_media_assets(*, db_path: Path, project_id: str) -> list[dict[str, Any]]:
    """Return all media assets in the catalog for the specified project.

    Args:
        db_path: Absolute path to the SQLite catalog.
        project_id: Flow project ID.

    Returns:
        A list of dicts carrying media_id and display_name keys.
    """
    import json

    sql = "SELECT flow_media_id, metadata_json FROM assets WHERE flow_project_id = ?"
    params = (project_id,)
    with _safe_db(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    results: list[dict[str, Any]] = []
    for r in rows:
        meta: dict[str, Any] = {}
        if r["metadata_json"]:
            try:
                meta = json.loads(r["metadata_json"])
            except Exception:
                pass
        results.append(
            {
                "media_id": str(r["flow_media_id"]),
                "display_name": str(meta.get("display_name") or ""),
            }
        )
    return results
