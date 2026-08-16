from __future__ import annotations

import asyncio
import dataclasses
import functools
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from gflow_cli import json_output, profile_store
from gflow_cli._cli_helpers import run_with_handlers, safe_path_text
from gflow_cli.config import get_settings
from gflow_cli.data.models import AssetLookup
from gflow_cli.data.queries import (
    ImageRow,
    OperationErrorRow,
    ProfileRow,
    ProjectRow,
    VideoRow,
    export_errors,
    list_errors,
    list_images,
    list_profiles,
    list_projects,
    list_videos,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError, SyncPartialError
from gflow_cli.observability import resolves_to_json

# run_sync is bound in THIS module's namespace so tests can monkeypatch
# ``gflow_cli.cli_data.run_sync`` (same pattern as ``cli_doctor.run_all``).
from gflow_cli.services.catalog_sync import ensure_prompts_stored, run_sync

console = Console()

# DataStoreError ``route`` tag for `gflow data media` lookups (dedupe S1192).
_ROUTE_DATA_MEDIA = "data.media"

__all__ = [
    "_db_path",
    "_emit_projects_table",
    "_guard",
]

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


def _row_json(row: Any) -> str:
    """Serialize a frozen catalog row to one JSON line (datetimes → ISO, Paths → str)."""
    d = dataclasses.asdict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, Path):
            d[k] = str(v)
    return json.dumps(d)


def _emit_jsonl(rows: list[Any]) -> None:
    for row in rows:
        click.echo(_row_json(row))


def _emit_projects_table(rows: list[ProjectRow]) -> None:
    tbl = Table(show_header=True, header_style="bold")
    for col in ("PROJECT_ID", "TITLE", "PROFILE", "CREATED", "IMG", "VID"):
        tbl.add_column(col)
    for r in rows:
        tbl.add_row(
            r.project_id,
            _truncate(r.title),
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
        "COPIES",
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
            str(r.copy_count),
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
        "COPIES",
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
            str(r.copy_count),
            r.local_path or "",
        )
    Console().print(tbl)


def _emit_errors_table(rows: list[OperationErrorRow]) -> None:
    tbl = Table(show_header=True, header_style="bold")
    for col in ("STARTED", "COMMAND", "MODE", "MODEL", "PROFILE", "ERROR_TYPE", "ERROR_DETAIL"):
        tbl.add_column(col)
    for r in rows:
        # Every column comes from the persisted DB, not live enums — escape
        # them all so bracketed content can't be parsed as Rich markup.
        tbl.add_row(
            r.started_at.strftime("%Y-%m-%d %H:%M"),
            escape(r.command or ""),
            escape(r.mode),
            escape(r.model or ""),
            escape(r.profile),
            escape(r.error_type or ""),
            escape(_truncate(r.error_detail)),
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


async def _run_media(*, profile: str | None, media_id: str) -> None:
    """Resolve ``media_id`` to its catalog row.

    When *profile* is given, the lookup is scoped to that profile (existing
    behaviour for explicit ``--profile``). When *profile* is ``None`` (the
    new default), every profile in the catalog is searched. Multiple matches
    across profiles raise a typed ``DataStoreError`` with a clear
    disambiguation hint. Closes #87.
    """
    import asyncio

    def _sync_query() -> AssetLookup:
        settings = get_settings()
        with DataStore.open(settings.resolved_db_path()) as store:
            repo = DataRepository(store)
            if profile is not None:
                scoped = repo.get_asset_by_flow_media_id(profile, media_id)
                if scoped is None:
                    raise DataStoreError(
                        detail=f"No local media record found: {media_id} (profile={profile!r})",
                        route=_ROUTE_DATA_MEDIA,
                    )
                return scoped

            matches = repo.find_assets_by_flow_media_id(media_id)
            if not matches:
                raise DataStoreError(
                    detail=f"No local media record found: {media_id}",
                    route=_ROUTE_DATA_MEDIA,
                )
            if len(matches) > 1:
                candidates = sorted({f"{m.profile_name} ({m.kind.value})" for m in matches})
                raise DataStoreError(
                    detail=(
                        f"Media {media_id!r} exists under multiple profiles: "
                        f"{candidates}. Pass --profile NAME to disambiguate."
                    ),
                    route=_ROUTE_DATA_MEDIA,
                )
            return matches[0]

    asset = await asyncio.to_thread(_sync_query)

    table = Table(title="gflow data media")
    table.add_column("field")
    table.add_column("value", overflow="fold")
    table.add_row("profile", asset.profile_name)
    table.add_row("media_id", asset.flow_media_id)
    table.add_row("project_id", asset.flow_project_id or "")
    table.add_row("kind", asset.kind.value)
    for idx, local_file in enumerate(asset.local_files, start=1):
        if local_file.path is not None:
            table.add_row(f"local_path_{idx}", safe_path_text(local_file.path))
        else:
            table.add_row(f"cloud_uri_{idx}", local_file.cloud_uri or "")
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


_ALL_COPIES_OPT = click.option(
    "--all-copies",
    "all_copies",
    is_flag=True,
    help="Show one row per local file instead of one row per asset.",
)


@list_group.command(name="images")
@_PROFILE_OPT
@_LIMIT_OPT
@_OFFSET_OPT
@_JSON_OPT
@_ALL_COPIES_OPT
@_guard
def list_images_cmd(
    profile: str | None,
    limit: int,
    offset: int,
    as_json: bool,
    all_copies: bool,
) -> None:
    """List images newest-first.

    By default shows one row per asset with a copy-count.  Use --all-copies to
    see every local path separately.
    """
    rows = list_images(
        db_path=_db_path(),
        profile=profile,
        limit=limit,
        offset=offset,
        all_copies=all_copies,
    )
    _emit(rows, as_json, _emit_images_table, "No images recorded.")


@list_group.command(name="videos")
@_PROFILE_OPT
@_LIMIT_OPT
@_OFFSET_OPT
@_JSON_OPT
@_ALL_COPIES_OPT
@_guard
def list_videos_cmd(
    profile: str | None,
    limit: int,
    offset: int,
    as_json: bool,
    all_copies: bool,
) -> None:
    """List videos newest-first.

    By default shows one row per asset with a copy-count.  Use --all-copies to
    see every local path separately.
    """
    rows = list_videos(
        db_path=_db_path(),
        profile=profile,
        limit=limit,
        offset=offset,
        all_copies=all_copies,
    )
    _emit(rows, as_json, _emit_videos_table, "No videos recorded.")


@list_group.command(name="errors")
@_PROFILE_OPT
@_LIMIT_OPT
@_OFFSET_OPT
@_JSON_OPT
@_guard
def list_errors_cmd(profile: str | None, limit: int, offset: int, as_json: bool) -> None:
    """List failed generation operations newest-first (#341).

    Shows when each failure happened, which command/mode/model it hit, and the
    stable error_type (waf-rejection, content-policy, ...) plus redacted
    detail — the dataset for WAF-cadence and reliability analysis.
    """
    rows = list_errors(db_path=_db_path(), profile=profile, limit=limit, offset=offset)
    _emit(rows, as_json, _emit_errors_table, "No failed operations recorded.")


@list_group.command(name="profiles")
@_LIMIT_OPT
@_OFFSET_OPT
@_JSON_OPT
@_guard
def list_profiles_cmd(limit: int, offset: int, as_json: bool) -> None:
    """List catalog-known profiles (not auth-known)."""
    rows = list_profiles(db_path=_db_path(), limit=limit, offset=offset)
    _emit(rows, as_json, _emit_profiles_table, "No profiles recorded.")


# ---------------------------------------------------------------------------
# `gflow data prune`
# ---------------------------------------------------------------------------


@data.command("prune")
@click.option("--dry-run", is_flag=True, help="Report dead rows without deleting them.")
@click.option("--profile", default=None, help="Limit scan to a specific profile.")
@_guard
def prune_cmd(dry_run: bool, profile: str | None) -> None:
    """Remove local_files rows whose paths no longer exist on disk.

    Useful after test runs that wrote files to temporary directories, or after
    manually deleting downloaded media.  Pass --dry-run to preview what would
    be removed.
    """
    db = _db_path()
    with DataStore.open(db) as store:
        rows = store.conn.execute(
            "SELECT id, path FROM local_files"
            " WHERE storage_provider IS NULL"
            " AND (:profile IS NULL OR profile_name = :profile)",
            {"profile": profile},
        ).fetchall()

        dead = [r for r in rows if not Path(str(r["path"])).exists()]

        if not dead:
            click.echo("No dead local_files rows found.")
            return

        if dry_run:
            for r in dead:
                click.echo(f"  [dead] {r['path']}")
            click.echo(f"{len(dead)} dead row(s) found. --dry-run: no changes made.")
            return

        dead_ids = [r["id"] for r in dead]
        # SQLite variable limit is usually 999; 500 is safe.
        chunk_size = 500
        pruned_count = 0
        with store.transaction(immediate=True):
            for i in range(0, len(dead_ids), chunk_size):
                chunk = dead_ids[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                store.conn.execute(f"DELETE FROM local_files WHERE id IN ({placeholders})", chunk)
                pruned_count += len(chunk)

        click.echo(f"Pruned {pruned_count} dead local_files row(s).")


# ---------------------------------------------------------------------------
# `gflow data errors` — bounded retention + export for failed history (#345)
# ---------------------------------------------------------------------------


_AGE_RE = re.compile(r"^\s*(\d+)\s*([dhm])\s*$")
_AGE_UNITS = {"d": "days", "h": "hours", "m": "minutes"}
_OLDER_THAN_HELP = "Age like 90d / 24h / 30m (d=days, h=hours, m=minutes)."


def _parse_older_than(text: str) -> timedelta:
    """Parse a retention age like ``90d`` / ``24h`` / ``30m`` → ``timedelta``.

    Units: ``d`` days, ``h`` hours, ``m`` minutes; the value must be a positive
    integer. Raises ``click.BadParameter`` on anything else so deletion stays an
    explicit, unambiguous operator choice.
    """
    match = _AGE_RE.match(text)
    if match is None:
        raise click.BadParameter(
            f"Invalid age {text!r}. Use <number><unit> with unit d/h/m (e.g. 90d, 24h, 30m)."
        )
    value = int(match.group(1))
    if value <= 0:
        raise click.BadParameter(f"Age must be a positive number, got {text!r}.")
    return timedelta(**{_AGE_UNITS[match.group(2)]: value})


@data.group(name="errors")
def errors_group() -> None:
    """Maintain the failed-operation history: export (archive) and prune (#345)."""


@errors_group.command(name="export")
@_PROFILE_OPT
@click.option(
    "--older-than",
    "older_than",
    default=None,
    metavar="AGE",
    help=f"Only export failures older than this. {_OLDER_THAN_HELP} Default: all.",
)
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write JSONL to this file. Default: stdout.",
)
@_guard
def errors_export_cmd(profile: str | None, older_than: str | None, output: Path | None) -> None:
    """Export failed operations as JSONL — archive history before pruning (#345).

    Unbounded (no --limit): dumps every failure newest-first. Pair with
    ``gflow data errors prune`` to reclaim space after archiving.
    """
    delta = _parse_older_than(older_than) if older_than else None
    rows = export_errors(db_path=_db_path(), profile=profile, older_than=delta)
    if output is None:
        _emit_jsonl(rows)
        return
    with output.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(_row_json(row) + "\n")
    click.echo(f"Exported {len(rows)} failed operation(s) to {output}.")


@errors_group.command(name="prune")
@_PROFILE_OPT
@click.option(
    "--older-than",
    "older_than",
    required=True,
    metavar="AGE",
    help=f"Delete failures older than this (required). {_OLDER_THAN_HELP}",
)
@click.option("--dry-run", is_flag=True, help="Report what would be deleted without deleting.")
@_guard
def errors_prune_cmd(profile: str | None, older_than: str, dry_run: bool) -> None:
    """Delete failed operations older than AGE — explicit retention (#345).

    No automatic/background pruning: deletion is always a deliberate operator
    action. Archive first with ``gflow data errors export`` if you need the
    history for offline analysis. ``--older-than`` is required.
    """
    delta = _parse_older_than(older_than)
    with DataStore.open(_db_path()) as store:
        count = DataRepository(store).prune_failed_operations(
            older_than=delta, profile=profile, dry_run=dry_run
        )
    if count == 0:
        click.echo("No failed operations older than the cutoff.")
    elif dry_run:
        click.echo(
            f"{count} failed operation(s) older than {older_than} would be deleted. "
            "--dry-run: no changes made."
        )
    else:
        click.echo(f"Pruned {count} failed operation(s) older than {older_than}.")


# ---------------------------------------------------------------------------
# `gflow data sync` — catalog name/presence reconciliation (#543)
# ---------------------------------------------------------------------------


def _logs_emit_json() -> bool:
    """True when structlog renders JSON on stderr (mirrors
    ``observability.configure_logging``'s AUTO resolution: TEXT on a stderr
    TTY, JSON otherwise). Gates the human progress echo — under TEXT logs the
    per-project ``sync.project_started`` events already narrate progress, and
    a second unconditional channel would break the single-channel rule."""
    return resolves_to_json(get_settings().log_format)


def _sync_profile_name(profile: str | None) -> str:
    """Resolve the profile whose catalog rows to sync.

    Same precedence as ``_resolve_profile`` (flag > env > config default >
    single discovered profile) but falls back to ``"default"`` instead of
    exiting: sync reads the local catalog first, and an unknown profile just
    yields zero nameless rows. Auth is enforced lazily at the first remote
    fetch (``_ThreadSafeListingClient._ensure``).
    """
    if profile:
        return profile
    try:
        return profile_store.resolve_profile(None)
    except (profile_store.NoProfilesError, profile_store.NoDefaultProfileError):
        return "default"


class _ThreadSafeListingClient:
    """Lazy bridge from the sync ``run_sync`` loop to the async FlowApiClient.

    ``run_sync`` is synchronous and runs in a worker thread
    (``asyncio.to_thread``) while the event loop stays on the main thread;
    each ``fetch_project_listing`` call is marshalled back onto the loop with
    ``run_coroutine_threadsafe``. The client is constructed and entered
    LAZILY on the first fetch — so a monkeypatched ``run_sync`` (unit tests)
    or an empty work list never launches a browser. ``aclose`` exits the
    client if it was ever entered.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, profile_name: str) -> None:
        self._loop = loop
        self._profile_name = profile_name
        self._client: Any = None

    async def _ensure(self) -> Any:
        if self._client is None:
            from gflow_cli._cli_helpers import _make_provider_dir
            from gflow_cli.api.client import FlowApiClient

            profile_dir = _make_provider_dir(self._profile_name)
            client = FlowApiClient(profile_dir=profile_dir, headless=get_settings().headless)
            await client.__aenter__()
            self._client = client
        return self._client

    def fetch_project_listing(self, project_id: str) -> Any:
        """Sync facade called by ``run_sync`` from the worker thread."""

        async def _fetch() -> Any:
            client = await self._ensure()
            return await client.fetch_project_listing(project_id)

        return asyncio.run_coroutine_threadsafe(_fetch(), self._loop).result()

    async def aclose(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.__aexit__(None, None, None)


def _echo_progress(event: Any) -> None:
    """Human progress line ("[i/N] project <id>") on stderr — stdout stays
    reserved for the summary / --json payload (single-channel rule)."""
    click.echo(str(event), err=True)


def _echo_sync_summary(summary: Any, *, dry_run: bool) -> None:
    prefix = "Sync (dry-run)" if dry_run else "Sync"
    click.echo(
        f"{prefix}: {summary.projects_visited} project(s) visited, "
        f"{summary.names_written} name(s) written, "
        f"{summary.ghosts_marked} ghost(s) marked, "
        f"{summary.rows_still_nameless} still nameless, "
        f"{len(summary.failures)} failure(s)."
    )


def _sync_payload(summary: Any, *, dry_run: bool) -> dict[str, Any]:
    return {
        "status": "ok",
        "dry_run": dry_run,
        "projects_visited": summary.projects_visited,
        "names_written": summary.names_written,
        "ghosts_marked": summary.ghosts_marked,
        "rows_still_nameless": summary.rows_still_nameless,
        "failures": [
            {"project_id": pid, "error": type(err).__name__} for pid, err in summary.failures
        ],
    }


async def _run_data_sync(
    *,
    profile: str | None,
    project_ids: tuple[str, ...],
    limit: int | None,
    since: datetime | None,
    max_projects: int,
    dry_run: bool,
    as_json: bool,
) -> None:
    settings = get_settings()
    # Privacy gate FIRST — before profile resolution, store open, or any
    # client construction (exit 11, remediation names the env var).
    ensure_prompts_stored(settings)
    profile_name = _sync_profile_name(profile)
    adapter = _ThreadSafeListingClient(asyncio.get_running_loop(), profile_name)
    on_progress = _echo_progress if _logs_emit_json() else None
    try:
        with DataStore.open(_db_path()) as store:
            repo = DataRepository(store)
            try:
                summary = await asyncio.to_thread(
                    run_sync,  # module-global lookup — tests monkeypatch cli_data.run_sync
                    adapter,
                    repo,
                    settings,
                    profile_name=profile_name,
                    dry_run=dry_run,
                    max_projects=max_projects,
                    limit=limit,
                    since=since,
                    project_ids=project_ids or None,
                    on_progress=on_progress,
                )
            except SyncPartialError as exc:
                # Show what DID land before the standard handler exits 34
                # (text mode only — --json emits exactly one error document).
                partial = getattr(exc, "summary", None)
                if partial is not None and not as_json:
                    _echo_sync_summary(partial, dry_run=dry_run)
                raise
    finally:
        await adapter.aclose()
    if as_json:
        json_output.emit(_sync_payload(summary, dry_run=dry_run))
    else:
        _echo_sync_summary(summary, dry_run=dry_run)


@data.command("sync")
@click.option(
    "--names",
    is_flag=True,
    required=True,
    help="Sync display names + presence (the only sync mode; explicit by design).",
)
@click.option(
    "--project",
    "project_ids",
    multiple=True,
    help="Limit the sweep to specific Flow project id(s). Repeatable.",
)
@click.option(
    "--limit",
    type=click.IntRange(1),
    default=None,
    help="Visit at most N nameless projects (newest first).",
)
@click.option(
    "--since",
    type=click.DateTime(),
    default=None,
    help="Only consider rows created at/after this time (e.g. 2026-08-01 or 2026-08-01T12:00:00).",
)
@click.option(
    "--all",
    "sweep_all",
    is_flag=True,
    help="Explicit full sweep of all nameless projects (this is also the default scope).",
)
@click.option(
    "--max-projects",
    type=click.IntRange(1),
    default=50,
    show_default=True,
    help="Hard cap on projects visited per run.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help=(
        "Fetch listings and preview what would be written (no DB writes). "
        "Without it, sync WRITES by default."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON summary instead of text.")
@click.option("--profile", default=None, help="Profile whose catalog rows to sync.")
def sync_cmd(
    names: bool,
    project_ids: tuple[str, ...],
    limit: int | None,
    since: datetime | None,
    sweep_all: bool,
    max_projects: int,
    dry_run: bool,
    as_json: bool,
    profile: str | None,
) -> None:
    """Reconcile nameless catalog rows against remote Flow project listings.

    Fetches each project's listing (credit-free, session-cookie auth), writes
    display names for rows that gained captions, and ghost-marks rows whose
    media no longer exists remotely — only when the listing is provably
    complete. Writes by default; pass --dry-run to preview.
    """
    del names, sweep_all  # required scope flag / explicit-default marker only
    run_with_handlers(
        lambda: _run_data_sync(
            profile=profile,
            project_ids=project_ids,
            limit=limit,
            since=since,
            max_projects=max_projects,
            dry_run=dry_run,
            as_json=as_json,
        ),
        cli_command="data sync",
        as_json=as_json,
    )
