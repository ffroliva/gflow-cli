from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from gflow_cli._cli_helpers import _resolve_profile, run_with_handlers, safe_path_text
from gflow_cli.config import get_settings
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError

console = Console()


@click.group()
def data() -> None:
    """Read local gflow media history."""


@data.command("media")
@click.argument("media_id")
@click.option("--profile", default=None, help="Profile name (overrides default).")
def media(media_id: str, profile: str | None) -> None:
    """Show local metadata for a media asset by its Flow media ID."""
    profile_name = _resolve_profile(profile)
    run_with_handlers(
        lambda: _run_media(profile_name=profile_name, media_id=media_id),
        cli_command="data media",
    )


async def _run_media(*, profile_name: str, media_id: str) -> None:  # NOSONAR S7503
    settings = get_settings()
    with DataStore.open(settings.resolved_db_path()) as store:
        repo = DataRepository(store)
        asset = repo.get_asset_by_flow_media_id(profile_name, media_id)
        if asset is None:
            raise DataStoreError(
                detail=f"No local media record found: {media_id}",
                route="data.media",
            )
        table = Table(title="gflow data media")
        table.add_column("field")
        table.add_column("value", overflow="fold")
        table.add_row("profile", profile_name)
        table.add_row("media_id", asset.flow_media_id)
        table.add_row("project_id", asset.flow_project_id or "")
        table.add_row("kind", asset.kind.value)
        for idx, local_file in enumerate(asset.local_files, start=1):
            table.add_row(f"local_path_{idx}", safe_path_text(local_file.path))
        console.print(table)
