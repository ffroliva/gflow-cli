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
        # #795: this is the labs session BFF answering 200 with no token — most
        # often an account Google has migrated to flow.google.com, for which it
        # never mints one. aisandbox-pa has not been contacted at this point, and
        # SAPISID is present and fine (it is what made the session probe report a
        # Google session at all), so the class default remediation would send the
        # user to re-authenticate something that is not broken.
        raise AisandboxAuthError(
            detail="the labs.google session returned no access token",
            status=session_status,
            route="auth/session",
            remediation_hint=(
                "Flow's labs.google session carries no API token for this account. "
                "On accounts Google has migrated to flow.google.com this is expected "
                "and re-authenticating will not help — generation still works, but "
                "`gflow credits` reads a labs-only endpoint. See issue #795."
            ),
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
            # #795: labs minted a token and aisandbox-pa refused it. SAPISID is not the
            # cause — it is what let labs mint that token at all — so the class default
            # sends the user to re-authenticate a credential that is working. Say what
            # was actually rejected, and do not name a fix we cannot stand behind: on an
            # account Google has moved to flow.google.com, `gflow auth login` can roll
            # the profile's browser-strategy marker back and start the #791 spiral.
            raise AisandboxAuthError(
                detail=f"credits endpoint returned {response.status_code}",
                status=response.status_code,
                route="credits",
                remediation_hint=(
                    "Flow's labs.google session issued an API token and aisandbox-pa "
                    "rejected it. Your Google sign-in is not the problem — minting that "
                    "token is what proves it works. Most commonly this is an account "
                    "Google has migrated to flow.google.com, which loses the aisandbox-pa "
                    "read endpoints while generation keeps working; `gflow credits` has no "
                    "migrated-host equivalent yet. A 403 can also be an entitlement or "
                    "region refusal. See issue #795."
                ),
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
