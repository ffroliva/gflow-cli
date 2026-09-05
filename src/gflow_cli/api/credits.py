"""Fast, browser-free HTTP client for Flow's read-only credit balance."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog

from gflow_cli.api import routes
from gflow_cli.api.dto import CreditsInfo
from gflow_cli.auth.verification import fetch_flow_session_httpx
from gflow_cli.errors import AisandboxAuthError, AuthExpiredError, FlowApiError, WireFormatError

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger(__name__)
_CREDITS_RESPONSE_KEYS = frozenset(
    {"credits", "subscriptionCredits", "userPaygateTier", "serviceTier", "sku"}
)


def _json_object(body: str, *, status: int, route: str) -> dict[str, Any]:
    try:
        value: Any = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WireFormatError(
            detail="non-JSON response",
            status=status,
            route=route,
        ) from exc
    if not isinstance(value, dict):
        raise WireFormatError(
            detail="unexpected response shape: expected an object",
            status=status,
            route=route,
        )
    return cast("dict[str, Any]", value)


async def fetch_credits_http(profile_dir: Path) -> CreditsInfo:
    """Fetch credits with cookies + HTTP first; cookie extraction may use Chrome as fallback.

    The saved Flow cookies authenticate the labs.google session request. Its short-lived
    ``access_token`` is then sent only to the aisandbox credits endpoint. Neither credential
    is returned, persisted, or logged.
    """

    session_status, session_body, _ = await fetch_flow_session_httpx(profile_dir)
    if session_status in {401, 403}:
        raise AuthExpiredError(
            detail=f"HTTP {session_status}",
            status=session_status,
            route="auth/session",
        )
    if session_status != 200:
        raise FlowApiError(
            detail=f"HTTP {session_status}",
            status=session_status,
            route="auth/session",
        )
    token = _json_object(session_body, status=session_status, route="auth/session").get(
        "access_token"
    )
    if not isinstance(token, str) or not token:
        raise AisandboxAuthError(
            detail="no access_token in Flow session",
            status=session_status,
            route="auth/session",
        )

    async with httpx.AsyncClient(follow_redirects=False, timeout=15.0) as client:
        response = await client.get(
            routes.CREDITS,
            headers={
                "accept": "*/*",
                "authorization": f"Bearer {token}",
                "origin": "https://labs.google",
                "referer": "https://labs.google/",
            },
        )
        if response.status_code in {401, 403}:
            raise AisandboxAuthError(
                detail=f"credits endpoint returned {response.status_code}",
                status=response.status_code,
                route="credits",
            )
        if response.status_code != 200:
            raise FlowApiError(
                detail=f"HTTP {response.status_code}",
                status=response.status_code,
                route="credits",
            )
        try:
            payload = _json_object(
                response.text,
                status=response.status_code,
                route="credits",
            )
            info = CreditsInfo.from_response(payload)
        except ValueError as exc:
            raise WireFormatError(
                detail=str(exc),
                status=response.status_code,
                route="credits",
            ) from exc
        response_keys = sorted(_CREDITS_RESPONSE_KEYS.intersection(payload))
        log.info(
            "credits.http_fast_path_succeeded",
            status_code=response.status_code,
            response_keys=response_keys,
            unknown_key_count=len(payload) - len(response_keys),
        )
        return info
