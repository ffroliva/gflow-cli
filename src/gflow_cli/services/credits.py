"""Read-only Flow credit inspection shared by CLI and MCP adapters."""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

import structlog

from gflow_cli import profile_store
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.credits import fetch_credits_http
from gflow_cli.api.dto import CreditsInfo
from gflow_cli.auth.renew import renew_profile
from gflow_cli.config import get_settings
from gflow_cli.errors import (
    AisandboxAuthError,
    AuthExpiredError,
    GFlowError,
    ProfileEngineDowngradeError,
    ProfileLockedError,
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


def _log_fallback(meta: profile_store.ProfileMeta, exc: Exception) -> None:
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


async def _fetch_once(meta: profile_store.ProfileMeta) -> dict[str, Any]:
    # #795. The browser fallback is for TRANSPORT failures. When it also catches a
    # verdict the server already gave, it re-derives that verdict with less information
    # and throws the better diagnosis away: it fails inside the SHARED aisandbox retry
    # helper (`api/client.py::_run_with_aisandbox_retry`), which is route-blind and so
    # carries the class-default "SAPISID missing, re-run `gflow auth login`" — advice
    # that cannot work on a migrated account and can roll its strategy marker back.
    verdict: AisandboxAuthError | None = None
    try:
        return _success(meta, await fetch_credits_http(meta.profile_dir))
    except SecurityError:
        raise
    except AisandboxAuthError as exc:
        if exc.route == "credits":
            # aisandbox-pa ANSWERED. The browser cannot overturn that: it asks the same
            # labs session endpoint for the same Bearer (`client.py::_fetch_access_token`)
            # and sends it to the same route. Measured on a migrated profile — it spent
            # ~7 s launching Chrome to collect the identical 401.
            raise
        # The other site — labs answering 200 with no token — is NOT proven unreachable
        # by the browser, and must not be assumed so: httpx sends labs.google cookies
        # only (`auth/cookies.py`, flow_only=True), while the browser carries the full
        # jar and bootstraps a real navigation, which can renew a NextAuth session httpx
        # cannot. Keep the fallback; just don't let the route-blind default overwrite
        # this verdict if the browser fails too.
        verdict = exc
        _log_fallback(meta, exc)
    except Exception as exc:  # noqa: BLE001 — browser fallback is the recovery boundary
        _log_fallback(meta, exc)

    try:
        async with FlowApiClient(
            profile_dir=meta.profile_dir,
            headless=get_settings().headless,
        ) as client:
            return _success(meta, await client.get_credits())
    except AisandboxAuthError:
        if verdict is None:
            raise
        raise verdict from None


async def _fetch(meta: profile_store.ProfileMeta, *, refresh: bool = False) -> dict[str, Any]:
    # `refresh` is not the browser fallback #795 retired. That one re-asked aisandbox-pa
    # with the same Bearer; this one loads Flow headlessly so Google and NextAuth rotate
    # the cookies on disk (`auth/renew.py`), and the retried fast path reads a NEW
    # session. Opt-in, because it costs a Chrome launch per expired profile.
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
        # channel is missing). The fast path's diagnosis is still the accurate one.
        log.info("credits.refresh_failed", profile=meta.name, error_type=type(exc).__name__)
        raise expired from exc
    if not result.renewed:
        # Revoked, or held at Flow's /about page: the fast path's diagnosis stands.
        log.info(
            "credits.refresh_failed",
            profile=meta.name,
            outcome=result.verdict.outcome.value,
            landing=result.landing,
            labs_stale=result.labs_stale,
        )
        raise expired
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
