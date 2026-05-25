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
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from gflow_cli.errors import DataStoreError


@contextmanager
def _safe_db(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Open the catalog DB and yield a connection, mapping sqlite3 errors to DataStoreError."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        raise DataStoreError(f"Failed to open catalog at {db_path}: {exc}") from exc
    try:
        yield conn
    except sqlite3.Error as exc:
        raise DataStoreError(f"Catalog query failed: {exc}") from exc
    finally:
        conn.close()


@dataclass(frozen=True)
class ProjectRow:
    project_id: str
    profile: str
    created_at: datetime
    image_count: int
    video_count: int


_LIST_PROJECTS_SQL = """
    SELECT
        p.flow_project_id AS project_id,
        p.profile_name    AS profile,
        p.created_at      AS created_at,
        (SELECT COUNT(*) FROM assets
         WHERE kind = 'image' AND flow_project_id = p.flow_project_id) AS image_count,
        (SELECT COUNT(*) FROM assets
         WHERE kind = 'video' AND flow_project_id = p.flow_project_id) AS video_count
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
        )
        for r in rows
    ]


# ─── ImageRow + list_images ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ImageRow:
    media_id: str
    profile: str
    project_id: str
    prompt: str | None
    aspect: str
    model: str
    created_at: datetime
    local_path: str | None


_LIST_IMAGES_SQL = """
    SELECT
        a.flow_media_id  AS media_id,
        a.profile_name   AS profile,
        a.flow_project_id AS project_id,
        o.prompt         AS prompt,
        a.aspect_ratio   AS aspect,
        a.model          AS model,
        a.created_at     AS created_at,
        lf.path          AS local_path
    FROM assets a
    LEFT JOIN operation_assets oa ON oa.asset_id = a.id AND oa.role = 'output'
    LEFT JOIN operations o ON o.id = oa.operation_id
    LEFT JOIN local_files lf ON lf.asset_id = a.id
    WHERE a.kind = 'image'
      AND (:profile IS NULL OR a.profile_name = :profile)
    ORDER BY a.created_at DESC
    LIMIT :limit OFFSET :offset
"""


def list_images(
    *,
    db_path: Path,
    profile: str | None,
    limit: int,
    offset: int,
) -> list[ImageRow]:
    """Return image assets newest-first, filtered by profile if given.

    prompt and local_path are NULLable (LEFT JOINs): prompt is absent for
    uploaded reference images; local_path is absent for assets not yet
    downloaded.

    Args:
        db_path: Absolute path to the SQLite catalog.
        profile: If given, only return images belonging to this profile name.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip (for pagination).

    Returns:
        A list of :class:`ImageRow` instances ordered newest-first.
    """
    params = {"profile": profile, "limit": limit, "offset": offset}
    with _safe_db(db_path) as conn:
        rows = conn.execute(_LIST_IMAGES_SQL, params).fetchall()
    return [
        ImageRow(
            media_id=str(r["media_id"]),
            profile=str(r["profile"]),
            project_id=str(r["project_id"]),
            prompt=str(r["prompt"]) if r["prompt"] is not None else None,
            aspect=str(r["aspect"]),
            model=str(r["model"]),
            created_at=datetime.fromisoformat(str(r["created_at"])),
            local_path=str(r["local_path"]) if r["local_path"] is not None else None,
        )
        for r in rows
    ]


# ─── VideoRow + list_videos ───────────────────────────────────────────────────


@dataclass(frozen=True)
class VideoRow:
    media_id: str
    profile: str
    project_id: str
    prompt: str | None
    aspect: str
    model: str
    duration: float | None
    created_at: datetime
    local_path: str | None


_LIST_VIDEOS_SQL = """
    SELECT
        a.flow_media_id   AS media_id,
        a.profile_name    AS profile,
        a.flow_project_id AS project_id,
        o.prompt          AS prompt,
        a.aspect_ratio    AS aspect,
        a.model           AS model,
        a.duration_seconds AS duration,
        a.created_at      AS created_at,
        lf.path           AS local_path
    FROM assets a
    LEFT JOIN operation_assets oa ON oa.asset_id = a.id AND oa.role = 'output'
    LEFT JOIN operations o ON o.id = oa.operation_id
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
) -> list[VideoRow]:
    """Return video assets newest-first, filtered by profile if given.

    prompt and local_path are NULLable (LEFT JOINs). duration maps to the
    duration_seconds REAL column in the schema, which is also nullable
    (e.g. omni-flash currently returns no duration in its t2v response, and
    smoke-test fixtures may insert rows without it).

    Args:
        db_path: Absolute path to the SQLite catalog.
        profile: If given, only return videos belonging to this profile name.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip (for pagination).

    Returns:
        A list of :class:`VideoRow` instances ordered newest-first.
    """
    params = {"profile": profile, "limit": limit, "offset": offset}
    with _safe_db(db_path) as conn:
        rows = conn.execute(_LIST_VIDEOS_SQL, params).fetchall()
    return [
        VideoRow(
            media_id=str(r["media_id"]),
            profile=str(r["profile"]),
            project_id=str(r["project_id"]),
            prompt=str(r["prompt"]) if r["prompt"] is not None else None,
            aspect=str(r["aspect"]),
            model=str(r["model"]),
            duration=float(r["duration"]) if r["duration"] is not None else None,
            created_at=datetime.fromisoformat(str(r["created_at"])),
            local_path=str(r["local_path"]) if r["local_path"] is not None else None,
        )
        for r in rows
    ]


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
