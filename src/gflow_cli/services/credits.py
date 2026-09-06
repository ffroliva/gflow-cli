"""Read-only Flow credit inspection shared by CLI and MCP adapters."""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

import structlog

from gflow_cli import profile_store
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.credits import fetch_credits_http
from gflow_cli.api.dto import CreditsInfo
from gflow_cli.config import get_settings
from gflow_cli.errors import GFlowError, SecurityError

log = structlog.get_logger(__name__)


def _profile_meta(name: str) -> profile_store.ProfileMeta:
    for meta in profile_store.list_profiles():
        if meta.name == name:
            return meta
    return profile_store.ProfileMeta(
        name=name,
        profile_dir=get_settings().profile_subdir(name),
        cookies_present=False,
        last_used_at=None,
        is_default=(profile_store.get_default_profile() == name),
    )


def _success(meta: profile_store.ProfileMeta, info: CreditsInfo) -> dict[str, Any]:
    return {
        "status": "ok",
        "profile": meta.name,
        "is_default": meta.is_default,
        "email": meta.google_account,
        "authenticated": True,
        **asdict(info),
    }


async def _fetch(meta: profile_store.ProfileMeta) -> dict[str, Any]:
    try:
        return _success(meta, await fetch_credits_http(meta.profile_dir))
    except SecurityError:
        raise
    except Exception as exc:  # noqa: BLE001 — browser fallback is the recovery boundary
        # Log only the class and profile label. Exception messages can include
        # upstream response material and are intentionally excluded.
        error_metadata: dict[str, object] = {}
        if isinstance(exc, GFlowError):
            error_metadata = {"status_code": exc.status, "route": exc.route}
        log.info(
            "credits.http_fallback_to_browser",
            profile=meta.name,
            error_type=type(exc).__name__,
            **error_metadata,
        )
    async with FlowApiClient(
        profile_dir=meta.profile_dir,
        headless=get_settings().headless,
    ) as client:
        return _success(meta, await client.get_credits())


async def inspect_profile(profile: str | None) -> dict[str, Any]:
    """Inspect one profile selected with the normal CLI precedence chain."""

    resolved = profile_store.resolve_profile(profile)
    return await _fetch(_profile_meta(resolved))


def _failure(meta: profile_store.ProfileMeta, exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, GFlowError):
        error = exc.title
        error_type = type(exc).__name__
    else:
        error_type = type(exc).__name__
        error = f"Unexpected {error_type}"
    return {
        "status": "error",
        "profile": meta.name,
        "is_default": meta.is_default,
        "email": meta.google_account,
        "authenticated": False,
        **{field.name: None for field in fields(CreditsInfo)},
        "error": error,
        "error_type": error_type,
    }


async def inspect_all_profiles() -> dict[str, Any]:
    """Inspect all saved profiles sequentially, preserving partial results."""

    snapshots: list[dict[str, Any]] = []
    for meta in profile_store.list_profiles():
        try:
            snapshots.append(await _fetch(meta))
        except SecurityError:
            raise
        except Exception as exc:  # noqa: BLE001 — preserve partial cross-profile results
            snapshots.append(_failure(meta, exc))
    successful = [item for item in snapshots if item["authenticated"]]
    return {
        "status": "ok" if len(successful) == len(snapshots) else "partial",
        "profiles": snapshots,
        "total_credits": sum(int(item["credits"]) for item in successful),
        "count": len(snapshots),
    }
