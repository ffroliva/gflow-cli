"""Read-only Flow credit inspection shared by CLI and MCP adapters."""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

import structlog

from gflow_cli import profile_store
from gflow_cli.api import flow_wire
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import CreditsInfo
from gflow_cli.auth.renew import renew_profile
from gflow_cli.config import get_settings
from gflow_cli.errors import (
    AuthExpiredError,
    GFlowError,
    NetworkError,
    ProfileEngineDowngradeError,
    ProfileLockedError,
    RateLimitError,
    SecurityError,
)

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


async def _read_wire(meta: profile_store.ProfileMeta) -> CreditsInfo | None:
    """The balance read with no browser (#1229), or None when the page path must answer."""
    try:
        return await flow_wire.read_credits(meta.profile_dir)
    except (SecurityError, RateLimitError, NetworkError):
        # Refused (a profile outside GFLOW_CLI_HOME), or back-pressure from Flow: the page
        # would send the same RPCs to a throttled account, so neither goes to the browser.
        raise
    except flow_wire.WireReadUnavailable as exc:
        log.info("credits.wire_fallback", profile=meta.name, reason=exc.reason.value)
    # A wire bug must never fail a read the page can answer.
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "credits.wire_fallback",
            profile=meta.name,
            reason=flow_wire.WireFallbackReason.UNEXPECTED.value,
            error_type=type(exc).__name__,
        )
    return None


async def _fetch_once(meta: profile_store.ProfileMeta) -> dict[str, Any]:
    # #795: the balance is flow.google.com's own `nzlxg` reply. The wire asks for it with the
    # profile's saved cookies BEFORE any FlowApiClient exists, because building one launches
    # Chrome; only when that fails does the browser load the app and read it (#1229).
    settings = get_settings()
    if settings.wire_reads and (info := await _read_wire(meta)) is not None:
        log.info("credits.read", profile=meta.name, source="wire")
        return _success(meta, info)
    async with FlowApiClient(profile_dir=meta.profile_dir, headless=settings.headless) as client:
        info = await client.get_credits()
    log.info("credits.read", profile=meta.name, source="page")
    return _success(meta, info)


async def _fetch(meta: profile_store.ProfileMeta, *, refresh: bool = False) -> dict[str, Any]:
    # `refresh` loads Flow headlessly so Google rotates the cookies on disk
    # (`auth/renew.py`), and the retried read sees the NEW session. Opt-in, because it
    # costs one more Chrome launch per signed-out profile.
    try:
        return await _fetch_once(meta)
    except AuthExpiredError as exc:
        if not refresh:
            raise
        expired = exc
    log.info("credits.refresh_renewing", profile=meta.name, error_type=type(expired).__name__)
    try:
        result = await renew_profile(meta.profile_dir, source="credits_refresh")
    except (ProfileLockedError, ProfileEngineDowngradeError):
        raise  # typed and actionable on their own: "in use", "newer browser"
    except Exception as exc:  # noqa: BLE001 — a renewal that never ran changes nothing
        # The browser could not start (the user's own Chrome holds the profile, the
        # channel is missing). The first read's diagnosis is still the accurate one.
        log.info("credits.refresh_failed", profile=meta.name, error_type=type(exc).__name__)
        raise expired from exc
    # `labs_unverified` still gets the re-ask: labs answers 410 to everyone now, and the
    # Flow visit may have rotated what the read needs even when labs said nothing.
    if not result.renewed and result.result != "labs_unverified":
        # Revoked, or held at Flow's /about page: the first read's diagnosis stands.
        log.info(
            "credits.refresh_failed",
            profile=meta.name,
            outcome=result.verdict.outcome.value,
            landing=result.landing,
            labs_stale=result.labs_stale,
        )
        raise expired
    # Flow verified the session (labs is irrelevant to the direct read): one more wire try.
    flow_wire.session_recovered(meta.profile_dir)
    return await _fetch_once(meta)


async def inspect_profile(profile: str | None, *, refresh: bool = False) -> dict[str, Any]:
    """Inspect one profile selected with the normal CLI precedence chain.

    With ``refresh``, an expired session is renewed headlessly and asked again.
    """

    resolved = profile_store.resolve_profile(profile)
    return await _fetch(_profile_meta(resolved), refresh=refresh)


def _failure(meta: profile_store.ProfileMeta, exc: BaseException) -> dict[str, Any]:
    remediation: str | None = None
    if isinstance(exc, GFlowError):
        error = exc.title
        error_type = type(exc).__name__
        # #795: the title alone is the generic class name ("aisandbox-pa authentication
        # failed"). Without the hint, `credits list` and the all_profiles MCP call are
        # the one surface that still cannot tell the user why, or that re-login will not
        # help — the exact gap the single-profile path was just fixed for.
        remediation = exc.remediation_hint or None
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
        "remediation_hint": remediation,
    }


async def inspect_all_profiles(*, refresh: bool = False) -> dict[str, Any]:
    """Inspect all saved profiles sequentially, preserving partial results.

    With ``refresh``, each expired session is renewed headlessly and asked again.
    """

    snapshots: list[dict[str, Any]] = []
    for meta in profile_store.list_profiles():
        try:
            snapshots.append(await _fetch(meta, refresh=refresh))
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
