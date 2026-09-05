"""Fast, browser-free HTTP client for Flow's read-only credit balance."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import httpx

from gflow_cli.api import routes
from gflow_cli.api.dto import CreditsInfo
from gflow_cli.auth.cookies import get_chrome_cookie_snapshot
from gflow_cli.auth.verification import SESSION_API_URL
from gflow_cli.errors import AisandboxAuthError, AuthExpiredError, FlowApiError, WireFormatError

if TYPE_CHECKING:
    from pathlib import Path

_SESSION_HEADERS = {
    "accept": "*/*",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "referer": "https://labs.google/fx/tools/flow",
}


def _json_object(response: httpx.Response, *, route: str) -> dict[str, Any]:
    try:
        value: Any = response.json()
    except json.JSONDecodeError as exc:
        raise WireFormatError(
            detail="non-JSON response",
            status=response.status_code,
            route=route,
        ) from exc
    if not isinstance(value, dict):
        raise WireFormatError(
            detail="unexpected response shape: expected an object",
            status=response.status_code,
            route=route,
        )
    return cast("dict[str, Any]", value)


async def fetch_credits_http(profile_dir: Path) -> CreditsInfo:
    """Fetch credits with cookies + HTTP first; cookie extraction may use Chrome as fallback.

    The saved Flow cookies authenticate the labs.google session request. Its short-lived
    ``access_token`` is then sent only to the aisandbox credits endpoint. Neither credential
    is returned, persisted, or logged.
    """

    snapshot = await get_chrome_cookie_snapshot(profile_dir)
    async with httpx.AsyncClient(
        cookies=snapshot.httpx_cookies,
        follow_redirects=False,
        timeout=15.0,
    ) as client:
        session = await client.get(SESSION_API_URL, headers=_SESSION_HEADERS)
        if session.status_code in {401, 403}:
            raise AuthExpiredError(
                detail=f"HTTP {session.status_code}",
                status=session.status_code,
                route="auth/session",
            )
        if session.status_code != 200:
            raise FlowApiError(
                detail=f"HTTP {session.status_code}",
                status=session.status_code,
                route="auth/session",
            )
        token = _json_object(session, route="auth/session").get("access_token")
        if not isinstance(token, str) or not token:
            raise AisandboxAuthError(
                detail="no access_token in Flow session",
                status=session.status_code,
                route="auth/session",
            )

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
            return CreditsInfo.from_response(_json_object(response, route="credits"))
        except ValueError as exc:
            raise WireFormatError(detail=str(exc), route="credits") from exc
