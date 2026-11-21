"""Domain service for moving media to Flow's Trash and restoring it (#1363).

Resolves user-supplied identifiers (Flow media UUID, local catalog asset ID, or workflow UUID)
against the local DataStore, prevents cross-profile ambiguity, coordinates wire mutations via
Flow batchexecute RPC pGCYOe (direct HTTP when permitted, falling back to browser context),
and updates local catalog state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from gflow_cli.api import flow_wire
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.flow_wire import WireReadUnavailable
from gflow_cli.config import get_settings
from gflow_cli.data.models import AssetLookup
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError, WireFormatError

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MediaTrashResult:
    """Result of moving media to Trash or restoring it."""

    media_id: str
    project_id: str
    workflow_id: str
    profile_name: str
    status: str  # "trashed" or "restored"
    archived: bool = False
    restore_hint: str | None = None
    charged: str = "no"

    def as_json(self) -> dict[str, Any]:
        """Dictionary formatted for --json output."""
        data: dict[str, Any] = {
            "media_id": self.media_id,
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "profile": self.profile_name,
            "status": self.status,
            "archived": self.archived,
            "charged": self.charged,
        }
        if self.restore_hint is not None:
            data["restore_hint"] = self.restore_hint
        return data


def resolve_media_trash_target(target_id: str, *, profile: str | None, route: str) -> AssetLookup:
    """Resolve user-supplied ID to an AssetLookup, enforcing profile scoping and ambiguity checks.

    Accepts flow_media_id, local catalog ID, or flow_workflow_id.
    """
    if not target_id or not target_id.strip():
        raise DataStoreError(
            detail="Media identifier must not be empty or blank.",
            route=route,
            charged="no",
            remediation_hint=(
                "Provide a valid Flow media ID, local catalog ID, or workflow UUID, "
                "or pass --project PROJECT_ID."
            ),
        )
    clean_id = target_id.strip()
    settings = get_settings()
    with DataStore.open(settings.resolved_db_path()) as store:
        repo = DataRepository(store)
        if profile is not None:
            matches = repo.find_assets_by_any_id(clean_id, profile_name=profile)
            if not matches:
                raise DataStoreError(
                    detail=f"No local media record found: {clean_id} (profile={profile!r})",
                    route=route,
                    charged="no",
                    remediation_hint=(
                        "Check the identifier or profile name, or pass --project PROJECT_ID "
                        "if the item is not cataloged locally."
                    ),
                )
            if len(matches) > 1:
                candidates = sorted({f"{m.flow_media_id} ({m.kind.value})" for m in matches})
                raise DataStoreError(
                    detail=(
                        f"Multiple media records match {clean_id!r} under profile {profile!r}: "
                        f"{candidates}. Pass a specific media id to disambiguate."
                    ),
                    route=route,
                    charged="no",
                )
            return matches[0]

        matches = repo.find_assets_by_any_id(clean_id)
        if not matches:
            raise DataStoreError(
                detail=f"No local media record found for identifier: {clean_id!r}",
                route=route,
                charged="no",
                remediation_hint=(
                    "Provide a valid Flow media ID, local catalog ID, or workflow UUID, "
                    "or pass --project PROJECT_ID."
                ),
            )

        if len(matches) > 1:
            profiles = sorted({m.profile_name for m in matches})
            if len(profiles) > 1:
                candidates = sorted(
                    {f"{m.profile_name} ({m.kind.value}: {m.flow_media_id})" for m in matches}
                )
                raise DataStoreError(
                    detail=(
                        f"Media {clean_id!r} exists under multiple profiles: "
                        f"{candidates}. Pass --profile NAME to disambiguate."
                    ),
                    route=route,
                    charged="no",
                )
            candidates = sorted({f"{m.flow_media_id} ({m.kind.value})" for m in matches})
            raise DataStoreError(
                detail=(
                    f"Multiple media records match {clean_id!r}: "
                    f"{candidates}. Pass a specific media id to disambiguate."
                ),
                route=route,
                charged="no",
            )
        return matches[0]


def _resolve_target_info(
    clean_id: str,
    profile: str | None,
    project_id: str | None,
    route: str,
    db_path: Path,
) -> tuple[str, str, str, str]:
    """Return (media_id, project_id, workflow_id, profile_name)."""
    if project_id:
        from gflow_cli import profile_store

        resolved_project_id = project_id.strip()
        profile_name = profile if profile else profile_store.resolve_profile(None)
        with DataStore.open(db_path) as store:
            repo = DataRepository(store)
            asset = repo.get_asset_by_any_id(profile_name, clean_id)
        workflow_id = asset.flow_workflow_id if asset and asset.flow_workflow_id else ""
        return clean_id, resolved_project_id, workflow_id, profile_name

    asset = resolve_media_trash_target(clean_id, profile=profile, route=route)
    if not asset.flow_project_id:
        raise DataStoreError(
            detail=(
                f"Asset {asset.flow_media_id!r} has no associated flow_project_id in the catalog."
            ),
            route=route,
            charged="no",
            remediation_hint="Pass --project PROJECT_ID to specify Flow project ID.",
        )
    return (
        asset.flow_media_id,
        asset.flow_project_id,
        asset.flow_workflow_id or "",
        asset.profile_name,
    )


async def _dispatch_mutation(
    profile_dir: Path,
    media_id: str,
    project_id: str,
    *,
    archived: bool,
    action: str,
    wire_reads: bool,
    headless: bool,
) -> dict[str, Any]:
    wire_res: dict[str, Any] | None = None
    if wire_reads:
        try:
            fn = flow_wire.direct_trash_media if archived else flow_wire.direct_restore_media
            wire_res = await fn(profile_dir, media_id, project_id)
        except WireReadUnavailable as exc:
            log.info(f"media.{action}_wire_fallback", reason=exc.reason.value, media_id=media_id)

    if wire_res is None:
        async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
            client_fn = client.trash_media if archived else client.restore_media
            wire_res = await client_fn(media_id, project_id)
    return wire_res


async def set_archived_media_service(
    target_id: str,
    *,
    archived: bool,
    profile: str | None = None,
    project_id: str | None = None,
    route: str | None = None,
) -> MediaTrashResult:
    """Trash or restore a media item over the wire (RPC pGCYOe)."""
    action = "trash" if archived else "restore"
    resolved_route = route or f"media.{action}"
    if not target_id or not target_id.strip():
        raise DataStoreError(
            detail="Media identifier must not be empty or blank.",
            route=resolved_route,
            charged="no",
            remediation_hint=(
                "Provide a valid Flow media ID, local catalog ID, or workflow UUID, "
                "or pass --project PROJECT_ID."
            ),
        )
    clean_id = target_id.strip()
    settings = get_settings()

    media_id, resolved_project_id, workflow_id, profile_name = _resolve_target_info(
        clean_id, profile, project_id, resolved_route, settings.resolved_db_path()
    )
    profile_dir = settings.profile_subdir(profile_name)

    wire_res = await _dispatch_mutation(
        profile_dir,
        media_id,
        resolved_project_id,
        archived=archived,
        action=action,
        wire_reads=settings.wire_reads,
        headless=settings.headless,
    )

    res_archived = wire_res.get("archived")
    if archived:
        if res_archived is not True:
            raise WireFormatError(
                f"pGCYOe response did not set archived flag to True: {res_archived!r}",
                charged="no",
            )
        final_archived = True
    else:
        final_archived = bool(res_archived) if res_archived is not None else False

    restore_hint = None
    if archived:
        restore_cmd = f"gflow media restore {media_id}"
        if project_id:
            restore_cmd += f" --project {resolved_project_id}"
        if profile:
            restore_cmd += f" --profile {profile}"
        restore_hint = f"To restore this item, run: {restore_cmd}"

    return MediaTrashResult(
        media_id=media_id,
        project_id=resolved_project_id,
        workflow_id=wire_res.get("workflow_id") or workflow_id,
        profile_name=profile_name,
        status="trashed" if archived else "restored",
        archived=final_archived,
        restore_hint=restore_hint,
    )


async def trash_media_service(
    target_id: str,
    *,
    profile: str | None = None,
    project_id: str | None = None,
    route: str = "media.trash",
) -> MediaTrashResult:
    """Move media item to Flow's Trash over the wire."""
    return await set_archived_media_service(
        target_id, profile=profile, project_id=project_id, route=route, archived=True
    )


async def restore_media_service(
    target_id: str,
    *,
    profile: str | None = None,
    project_id: str | None = None,
    route: str = "media.restore",
) -> MediaTrashResult:
    """Restore media item from Flow's Trash over the wire."""
    return await set_archived_media_service(
        target_id, profile=profile, project_id=project_id, route=route, archived=False
    )
