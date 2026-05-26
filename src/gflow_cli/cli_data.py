from __future__ import annotations

import dataclasses
import functools
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from gflow_cli._cli_helpers import run_with_handlers, safe_path_text
from gflow_cli.config import get_settings
from gflow_cli.data.queries import (
    ImageRow,
    ProfileRow,
    ProjectRow,
    VideoRow,
    list_images,
    list_profiles,
    list_projects,
    list_videos,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError

console = Console()


# ---------------------------------------------------------------------------
# Helpers for `gflow data list` output
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    """Resolve the catalog DB path: ``GFLOW_CLI_DB_PATH`` env first, then
    the canonical resolver used by the rest of the codebase.

    The env-var check is direct (not via ``Settings``) so test
    ``monkeypatch.setenv`` calls take effect even when ``get_settings()`` is
    already cached.  When the env var is absent, delegates to
    ``paths.database_path(home)`` via ``Settings.resolved_db_path`` — the
    SAME resolver as ``data media`` and the recorder, so all subcommands
    agree on the path.  The prior platformdirs lookup here used the wrong
    appauthor + filename and resolved to a non-existent path on Windows
    (``AppData\\Local\\gflow-cli\\gflow-cli\\data.db`` vs the real
    ``AppData\\Local\\ffroliva\\gflow-cli\\gflow.db``).
    """
    if env := os.environ.get("GFLOW_CLI_DB_PATH"):
        return Path(env)
    return get_settings().resolved_db_path()


def _truncate(s: str | None, n: int = 40) -> str:
    if s is None:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _emit_jsonl(rows: list[Any]) -> None:
    for row in rows:
        d = dataclasses.asdict(row)
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
            elif isinstance(v, Path):
                d[k] = str(v)
        click.echo(json.dumps(d))


def _emit_projects_table(rows: list[ProjectRow]) -> None:
    tbl = Table(show_header=True, header_style="bold")
    for col in ("PROJECT_ID", "PROFILE", "CREATED", "IMG", "VID"):
        tbl.add_column(col)
    for r in rows:
        tbl.add_row(
            r.project_id,
            r.profile,
            r.created_at.strftime("%Y-%m-%d %H:%M"),
            str(r.image_count),
            str(r.video_count),
        )
    Console().print(tbl)


def _emit_images_table(rows: list[ImageRow]) -> None:
    tbl = Table(show_header=True, header_style="bold")
    for col in (
        "MEDIA_ID",
        "PROFILE",
        "PROJECT_ID",
        "PROMPT",
        "ASPECT",
        "MODEL",
        "CREATED",
        "LOCAL_PATH",
    ):
        tbl.add_column(col)
    for r in rows:
        tbl.add_row(
            r.media_id,
            r.profile,
            r.project_id,
            _truncate(r.prompt),
            r.aspect,
            r.model,
            r.created_at.strftime("%Y-%m-%d %H:%M"),
            r.local_path or "",
        )
    Console().print(tbl)


def _emit_videos_table(rows: list[VideoRow]) -> None:
    tbl = Table(show_header=True, header_style="bold")
    for col in (
        "MEDIA_ID",
        "PROFILE",
        "PROJECT_ID",
        "PROMPT",
        "ASPECT",
        "MODEL",
        "DURATION",
        "CREATED",
        "LOCAL_PATH",
    ):
        tbl.add_column(col)
    for r in rows:
        tbl.add_row(
            r.media_id,
            r.profile,
            r.project_id,
            _truncate(r.prompt),
            r.aspect,
            r.model,
            f"{r.duration:g}s" if r.duration is not None else "",
            r.created_at.strftime("%Y-%m-%d %H:%M"),
            r.local_path or "",
        )
    Console().print(tbl)


def _emit_profiles_table(rows: list[ProfileRow]) -> None:
    tbl = Table(show_header=True, header_style="bold")
    for col in ("PROFILE_NAME", "LAST_USED", "PROJECTS", "IMAGES", "VIDEOS"):
        tbl.add_column(col)
    for r in rows:
        tbl.add_row(
            r.profile_name,
            r.last_used_at.strftime("%Y-%m-%d %H:%M"),
            str(r.project_count),
            str(r.image_count),
            str(r.video_count),
        )
    Console().print(tbl)


def _emit(
    rows: list[Any],
    as_json: bool,
    table_fn: Any,
    empty_msg: str,
) -> None:
    if not rows:
        if not as_json and sys.stdout.isatty():
            click.echo(empty_msg)
        return
    if as_json or not sys.stdout.isatty():
        _emit_jsonl(rows)
    else:
        table_fn(rows)


def _guard(fn: Any) -> Any:
    """Decorator: map DataStoreError → exit 16."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except DataStoreError as exc:
            click.echo(f"Data store error: {exc}", err=True)
            raise click.exceptions.Exit(16) from None

    return wrapper


# Shared option factories
_PROFILE_OPT = click.option("--profile", default=None, help="Filter by profile name.")
_LIMIT_OPT = click.option(
    "--limit",
    type=click.IntRange(1, 1000),
    default=20,
    show_default=True,
    help="Maximum number of rows to return.",
)
_OFFSET_OPT = click.option(
    "--offset",
    type=click.IntRange(0),
    default=0,
    show_default=True,
    help="Number of rows to skip (pagination).",
)
_JSON_OPT = click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit JSONL instead of a Rich table.",
)


@click.group()
def data() -> None:
    """Read local gflow media history."""


@data.command("media")
@click.argument("media_id")
@click.option(
    "--profile",
    default=None,
    help="Scope the lookup to a specific profile. Default: search all profiles.",
)
def media(media_id: str, profile: str | None) -> None:
    """Show local metadata for a media asset by its Flow media ID.

    Without ``--profile`` the lookup spans every profile in the catalog —
    matching the cross-profile default of ``gflow data list``. Pass
    ``--profile NAME`` to disambiguate the rare case where the same Flow
    media ID exists under multiple profiles.
    """
    run_with_handlers(
        lambda: _run_media(profile=profile, media_id=media_id),
        cli_command="data media",
    )


async def _run_media(*, profile: str | None, media_id: str) -> None:  # NOSONAR S7503
    """Resolve ``media_id`` to its catalog row.

    When *profile* is given, the lookup is scoped to that profile (existing
    behaviour for explicit ``--profile``). When *profile* is ``None`` (the
    new default), every profile in the catalog is searched. Multiple matches
    across profiles raise a typed ``DataStoreError`` with a clear
    disambiguation hint. Closes #87.
    """
    settings = get_settings()
    with DataStore.open(settings.resolved_db_path()) as store:
        repo = DataRepository(store)
        if profile is not None:
            scoped = repo.get_asset_by_flow_media_id(profile, media_id)
            asset = scoped
            if asset is None:
                raise DataStoreError(
                    detail=f"No local media record found: {media_id} (profile={profile!r})",
                    route="data.media",
                )
        else:
            matches = repo.find_assets_by_flow_media_id(media_id)
            if not matches:
                raise DataStoreError(
                    detail=f"No local media record found: {media_id}",
                    route="data.media",
                )
            if len(matches) > 1:
                # Include each match's kind in the hint so an image/video
                # collision under the same media_id is visible at a glance.
                candidates = sorted({f"{m.profile_name} ({m.kind.value})" for m in matches})
                raise DataStoreError(
                    detail=(
                        f"Media {media_id!r} exists under multiple profiles: "
                        f"{candidates}. Pass --profile NAME to disambiguate."
                    ),
                    route="data.media",
                )
            asset = matches[0]
        table = Table(title="gflow data media")
        table.add_column("field")
        table.add_column("value", overflow="fold")
        table.add_row("profile", asset.profile_name)
        table.add_row("media_id", asset.flow_media_id)
        table.add_row("project_id", asset.flow_project_id or "")
        table.add_row("kind", asset.kind.value)
        for idx, local_file in enumerate(asset.local_files, start=1):
            table.add_row(f"local_path_{idx}", safe_path_text(local_file.path))
        console.print(table)


# ---------------------------------------------------------------------------
# `gflow data list` subgroup
# ---------------------------------------------------------------------------


@data.group(name="list")
def list_group() -> None:
    """List entries from the catalog."""


@list_group.command(name="projects")
@_PROFILE_OPT
@_LIMIT_OPT
@_OFFSET_OPT
@_JSON_OPT
@_guard
def list_projects_cmd(profile: str | None, limit: int, offset: int, as_json: bool) -> None:
    """List projects newest-first."""
    rows = list_projects(db_path=_db_path(), profile=profile, limit=limit, offset=offset)
    _emit(rows, as_json, _emit_projects_table, "No projects recorded.")


@list_group.command(name="images")
@_PROFILE_OPT
@_LIMIT_OPT
@_OFFSET_OPT
@_JSON_OPT
@_guard
def list_images_cmd(profile: str | None, limit: int, offset: int, as_json: bool) -> None:
    """List images newest-first."""
    rows = list_images(db_path=_db_path(), profile=profile, limit=limit, offset=offset)
    _emit(rows, as_json, _emit_images_table, "No images recorded.")


@list_group.command(name="videos")
@_PROFILE_OPT
@_LIMIT_OPT
@_OFFSET_OPT
@_JSON_OPT
@_guard
def list_videos_cmd(profile: str | None, limit: int, offset: int, as_json: bool) -> None:
    """List videos newest-first."""
    rows = list_videos(db_path=_db_path(), profile=profile, limit=limit, offset=offset)
    _emit(rows, as_json, _emit_videos_table, "No videos recorded.")


@list_group.command(name="profiles")
@_LIMIT_OPT
@_OFFSET_OPT
@_JSON_OPT
@_guard
def list_profiles_cmd(limit: int, offset: int, as_json: bool) -> None:
    """List catalog-known profiles (not auth-known)."""
    rows = list_profiles(db_path=_db_path(), limit=limit, offset=offset)
    _emit(rows, as_json, _emit_profiles_table, "No profiles recorded.")
