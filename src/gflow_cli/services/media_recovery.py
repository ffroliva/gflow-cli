"""Fetch an already-generated asset by media id and record it locally (#865, #871).

Joins three pieces that already exist: the catalog row an orphaned asset always has,
the per-clip recovery in :mod:`gflow_cli.api.transports.migrated_recover`, and the
``local_files`` write that stops ``gflow data list`` reporting ``copy_count: 0`` for a
file that is now on disk.

Spends no credits — the generation it retrieves has already been billed.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

import structlog

from gflow_cli.config import get_settings
from gflow_cli.data.models import AssetKind, AssetLookup, LocalFileRecord
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import ConfigurationError, DataStoreError

log = structlog.get_logger(__name__)

_ROUTE = "data.download"


@dataclass(frozen=True)
class DownloadedMedia:
    """Result of a recovery run, for the CLI and the MCP tool to report."""

    media_id: str
    workflow_id: str
    profile_name: str
    project_id: str
    path: Path
    bytes: int


def resolve_asset(media_id: str, *, profile: str | None, route: str) -> AssetLookup:
    """The catalog row for *media_id*, scoped to *profile* when given.

    Shared by ``gflow data media`` and ``gflow data download`` so the two agree on what
    "no such media" and "ambiguous across profiles" mean. ``route`` tags the raised
    :class:`DataStoreError` with the caller's command.
    """
    settings = get_settings()
    with DataStore.open(settings.resolved_db_path()) as store:
        repo = DataRepository(store)
        if profile is not None:
            scoped = repo.get_asset_by_flow_media_id(profile, media_id)
            if scoped is None:
                raise DataStoreError(
                    detail=f"No local media record found: {media_id} (profile={profile!r})",
                    route=route,
                )
            return scoped

        matches = repo.find_assets_by_flow_media_id(media_id)
        if not matches:
            raise DataStoreError(
                detail=f"No local media record found: {media_id}",
                route=route,
            )
        if len(matches) > 1:
            candidates = sorted({f"{m.profile_name} ({m.kind.value})" for m in matches})
            raise DataStoreError(
                detail=(
                    f"Media {media_id!r} exists under multiple profiles: "
                    f"{candidates}. Pass --profile NAME to disambiguate."
                ),
                route=route,
            )
        return matches[0]


def record_local_file(asset: AssetLookup, path: Path, size: int) -> None:
    """Write the ``local_files`` row for a recovered asset.

    Without this the file is on disk and the catalog still reports ``copy_count: 0``,
    which is the state that made the orphan invisible in the first place.
    """
    settings = get_settings()
    with DataStore.open(settings.resolved_db_path()) as store:
        DataRepository(store).upsert_local_file(
            LocalFileRecord(
                id=str(uuid.uuid4()),
                profile_name=asset.profile_name,
                asset_id=asset.id,
                path=path,
                media_type="video/mp4",
                bytes=size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )


async def download_media(
    *,
    media_id: str,
    profile: str | None,
    out_dir: Path | None,
) -> DownloadedMedia:
    """Recover *media_id* to disk and record it in the catalog.

    Raises :class:`DataStoreError` when the catalog has no row for *media_id* (or the id
    is ambiguous across profiles), and the transport's own typed errors when Flow does
    not serve the asset.
    """
    from gflow_cli._cli_helpers import _make_provider_dir  # noqa: PLC0415 - import cycle
    from gflow_cli.api.client import FlowApiClient  # noqa: PLC0415 - import cycle
    from gflow_cli.api.transports.migrated_recover import recover_clip  # noqa: PLC0415

    asset = resolve_asset(media_id, profile=profile, route=_ROUTE)
    if asset.kind is not AssetKind.VIDEO:
        # #877: the signed URL comes from the `as29s` record a clip route emits, which
        # is a video mechanism — an image's route never emits one. Accepting the id
        # anyway cost 45 s in a browser and then produced three guesses (wrong project,
        # trashed clip, "retry with a simpler prompt") for a condition this row states
        # outright. Refuse here, on what the catalog already knows.
        raise ConfigurationError(
            detail=(
                f"Media {media_id!r} is {asset.kind.value}, and `gflow data download` "
                "recovers video only. The signed URL it needs comes from the record "
                "Flow emits when a clip's own route loads, and an image's route does "
                "not carry one. Tracking the image path in issue #877."
            ),
            remediation_hint=(
                "Nothing to retry — this is a capability gap, not a fault. Open the "
                "image in Flow and save it from there, or pass a video media id. "
                "`gflow data list images` shows which rows are images."
            ),
            route=_ROUTE,
        )
    if not asset.flow_project_id:
        raise DataStoreError(
            detail=(
                f"Media {media_id!r} has no project id in the catalog, so its clip route "
                "cannot be built. Re-run `gflow data sync` for the owning project."
            ),
            route=_ROUTE,
        )

    settings = get_settings()
    target_dir = out_dir or settings.output_dir
    profile_dir = _make_provider_dir(asset.profile_name)

    async with FlowApiClient(profile_dir=profile_dir, headless=settings.headless) as client:
        clip = await recover_clip(
            client.page,
            project_id=asset.flow_project_id,
            media_id=media_id,
            out_dir=target_dir,
        )

    record_local_file(asset, clip.path, clip.bytes)
    log.info(
        "data.download.recovered",
        media_id=media_id,
        profile=asset.profile_name,
        path=str(clip.path),
        bytes=clip.bytes,
    )
    return DownloadedMedia(
        media_id=media_id,
        workflow_id=clip.workflow_id,
        profile_name=asset.profile_name,
        project_id=asset.flow_project_id,
        path=clip.path,
        bytes=clip.bytes,
    )
