"""Domain service for moving media to Flow's Trash and restoring it (#1363).

Resolves user-supplied identifiers (Flow media UUID, local catalog asset ID, or workflow UUID)
against the local DataStore, prevents cross-profile ambiguity, coordinates wire mutations via
Flow batchexecute RPC pGCYOe (direct HTTP when permitted, falling back to browser context),
and updates local catalog state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from gflow_cli.api import flow_wire
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.flow_wire import WireReadUnavailable
from gflow_cli.config import get_settings
from gflow_cli.data.models import AssetLookup
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class MediaTrashResult:
    """Result of moving media to Trash or restoring it."""

    media_id: str
    project_id: str
    workflow_id: str
    profile_name: str
    status: str  # "trashed" or "restored"
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


async def trash_media_service(
    target_id: str,
    *,
    profile: str | None = None,
    project_id: str | None = None,
    route: str = "media.trash",
) -> MediaTrashResult:
    """Move media item to Flow's Trash over the wire."""
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

    asset: AssetLookup | None = None
    if project_id:
        from gflow_cli import profile_store

        media_id = clean_id
        resolved_project_id = project_id.strip()
        profile_name = profile if profile else profile_store.resolve_profile(None)
        with DataStore.open(settings.resolved_db_path()) as store:
            repo = DataRepository(store)
            asset = repo.get_asset_by_any_id(profile_name, media_id)
        workflow_id = asset.flow_workflow_id if asset and asset.flow_workflow_id else ""
    else:
        asset = resolve_media_trash_target(clean_id, profile=profile, route=route)
        profile_name = asset.profile_name
        media_id = asset.flow_media_id
        resolved_project_id = asset.flow_project_id
        if not resolved_project_id:
            raise DataStoreError(
                detail=f"Asset {media_id!r} has no associated flow_project_id in the catalog.",
                route=route,
                charged="no",
                remediation_hint="Pass --project PROJECT_ID to specify Flow project ID.",
            )
        workflow_id = asset.flow_workflow_id or ""

    profile_dir = settings.profile_subdir(profile_name)

    wire_success = False
    wire_res: dict[str, Any] | None = None
    if settings.wire_reads:
        try:
            wire_res = await flow_wire.direct_trash_media(
                profile_dir, media_id, resolved_project_id
            )
            wire_success = True
        except WireReadUnavailable as exc:
            log.info("media.trash_wire_fallback", reason=exc.reason.value, media_id=media_id)

    if not wire_success or wire_res is None:
        async with FlowApiClient(profile_dir=profile_dir, headless=settings.headless) as client:
            wire_res = await client.trash_media(media_id, resolved_project_id)

    if asset is not None:
        with DataStore.open(settings.resolved_db_path()) as store:
            repo = DataRepository(store)
            repo.update_asset_status(asset.profile_name, media_id, "trashed")

    restore_cmd = f"gflow media restore {media_id}"
    if project_id:
        restore_cmd += f" --project {resolved_project_id}"
    if profile:
        restore_cmd += f" --profile {profile}"

    return MediaTrashResult(
        media_id=media_id,
        project_id=resolved_project_id,
        workflow_id=wire_res.get("workflow_id") or workflow_id,
        profile_name=profile_name,
        status="trashed",
        restore_hint=f"To restore this item, run: {restore_cmd}",
    )


async def restore_media_service(
    target_id: str,
    *,
    profile: str | None = None,
    project_id: str | None = None,
    route: str = "media.restore",
) -> MediaTrashResult:
    """Restore media item from Flow's Trash over the wire."""
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

    asset: AssetLookup | None = None
    if project_id:
        from gflow_cli import profile_store

        media_id = clean_id
        resolved_project_id = project_id.strip()
        profile_name = profile if profile else profile_store.resolve_profile(None)
        with DataStore.open(settings.resolved_db_path()) as store:
            repo = DataRepository(store)
            asset = repo.get_asset_by_any_id(profile_name, media_id)
        workflow_id = asset.flow_workflow_id if asset and asset.flow_workflow_id else ""
    else:
        asset = resolve_media_trash_target(clean_id, profile=profile, route=route)
        profile_name = asset.profile_name
        media_id = asset.flow_media_id
        resolved_project_id = asset.flow_project_id
        if not resolved_project_id:
            raise DataStoreError(
                detail=f"Asset {media_id!r} has no associated flow_project_id in the catalog.",
                route=route,
                charged="no",
                remediation_hint="Pass --project PROJECT_ID to specify Flow project ID.",
            )
        workflow_id = asset.flow_workflow_id or ""

    profile_dir = settings.profile_subdir(profile_name)

    wire_success = False
    wire_res: dict[str, Any] | None = None
    if settings.wire_reads:
        try:
            wire_res = await flow_wire.direct_restore_media(
                profile_dir, media_id, resolved_project_id
            )
            wire_success = True
        except WireReadUnavailable as exc:
            log.info("media.restore_wire_fallback", reason=exc.reason.value, media_id=media_id)

    if not wire_success or wire_res is None:
        async with FlowApiClient(profile_dir=profile_dir, headless=settings.headless) as client:
            wire_res = await client.restore_media(media_id, resolved_project_id)

    if asset is not None:
        with DataStore.open(settings.resolved_db_path()) as store:
            repo = DataRepository(store)
            repo.update_asset_status(asset.profile_name, media_id, "ready")

    return MediaTrashResult(
        media_id=media_id,
        project_id=resolved_project_id,
        workflow_id=wire_res.get("workflow_id") or workflow_id,
        profile_name=profile_name,
        status="restored",
    )
