"""Flow app-session verification — the single source of truth for
"is this profile signed in to the Flow app?".

A profile can hold Google SSO cookies (e.g. SAPISID) without holding the Flow
app's NextAuth session (`__Secure-next-auth.session-token`). Only the latter
authenticates Flow's tRPC API. This module probes the same surface
`FlowApiClient` authenticates on — the NextAuth session endpoint — so a login
is never reported successful unless a real, usable Flow session exists.

See docs/superpowers/specs/2026-05-17-issue-15-auth-verification-fix-design.md
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.config import get_settings
from gflow_cli.errors import SecurityError
from gflow_cli.paths import get_cookies_path

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import BrowserContext

logger = structlog.get_logger(__name__)

# The NextAuth session endpoint. Expected authenticated 200 body shape:
#   {"user": {"name": ..., "email": ..., "image": ...}, "expires": "..."}
# An unauthenticated request returns `200 {}`. This contract is pinned by the
# AUTHENTICATED_BODY fixture in tests/auth/test_verification.py — if Google
# changes the shape, that test fails rather than the change going silent.
SESSION_API_URL = "https://labs.google/fx/api/auth/session"

# Per-request timeout for the session probe (milliseconds).
_REQUEST_TIMEOUT_MS = 15_000
# Total fetch attempts (initial + retries) before giving up.
_MAX_ATTEMPTS = 3
# HTTP statuses worth retrying — transient server-side conditions only.
_RETRYABLE_STATUSES = frozenset({429, 503, 504})


class FlowSessionOutcome(StrEnum):
    """Mutually-exclusive results of probing a profile for a Flow session."""

    AUTHENTICATED = "authenticated"
    GOOGLE_SESSION_ONLY = "google_session_only"
    NO_SESSION = "no_session"
    VERIFICATION_ERROR = "verification_error"


_DETAIL_BY_OUTCOME: dict[FlowSessionOutcome, str] = {
    FlowSessionOutcome.AUTHENTICATED: "Flow app session verified.",
    FlowSessionOutcome.GOOGLE_SESSION_ONLY: "Signed in to Google, but not to the Flow app.",
    FlowSessionOutcome.NO_SESSION: "No sign-in detected.",
    FlowSessionOutcome.VERIFICATION_ERROR: "Could not verify the Flow session.",
}


@dataclass(frozen=True)
class FlowSessionStatus:
    """The verdict of a Flow-session probe.

    `detail` is a derived property — always one of the four fixed strings in
    `_DETAIL_BY_OUTCOME`, never built from response, cookie, or exception
    content. Deriving it (rather than storing a free string) makes it
    structurally impossible to leak a secret through this field.
    """

    outcome: FlowSessionOutcome
    user_email: str | None
    source: str  # caller-supplied log label ("chrome"/"internal"); never from response/cookie data

    @property
    def detail(self) -> str:
        return _DETAIL_BY_OUTCOME[self.outcome]

    @property
    def authenticated(self) -> bool:
        return self.outcome is FlowSessionOutcome.AUTHENTICATED


def evaluate_session_response(
    status_code: int,
    body: str,
    *,
    google_session: bool,
    source: str,
) -> FlowSessionStatus:
    """Map a raw /api/auth/session response to a FlowSessionStatus.

    Pure and total: no I/O, no exceptions raised or used for control flow.
    Every (status_code, body) maps to exactly one outcome. Fail-closed — only
    a 200 carrying a usable `user.email` yields AUTHENTICATED. Only `email` is
    read; `name`, `image`, and `expires` are ignored, and the parsed dict is
    never retained beyond this function.
    """

    def _result(outcome: FlowSessionOutcome, email: str | None = None) -> FlowSessionStatus:
        return FlowSessionStatus(outcome=outcome, user_email=email, source=source)

    if status_code != 200:
        return _result(FlowSessionOutcome.VERIFICATION_ERROR)

    try:
        parsed: Any = json.loads(body)
    except ValueError:
        # json.JSONDecodeError is a subclass of ValueError, so this catches both.
        return _result(FlowSessionOutcome.VERIFICATION_ERROR)

    if not isinstance(parsed, dict):
        return _result(FlowSessionOutcome.VERIFICATION_ERROR)

    parsed_dict = cast("dict[str, Any]", parsed)
    user = parsed_dict.get("user")
    if user is None or user == {}:
        # Authenticated-shaped endpoint reachable, but no Flow session.
        if google_session:
            return _result(FlowSessionOutcome.GOOGLE_SESSION_ONLY)
        return _result(FlowSessionOutcome.NO_SESSION)

    if not isinstance(user, dict):
        return _result(FlowSessionOutcome.VERIFICATION_ERROR)

    user_dict = cast("dict[str, Any]", user)
    email = user_dict.get("email")
    if isinstance(email, str) and email:
        return _result(FlowSessionOutcome.AUTHENTICATED, email)

    # `user` present but no usable email — unexpected shape (see spec §10).
    return _result(FlowSessionOutcome.VERIFICATION_ERROR)


async def _fetch_session(ctx: BrowserContext) -> tuple[int, str]:
    """Fetch /api/auth/session, retrying transient failures.

    Returns the final (status_code, body). Makes up to `_MAX_ATTEMPTS`
    attempts; an attempt is retried only on a network/timeout error or an
    HTTP status in `_RETRYABLE_STATUSES`, with exponential backoff (1s, 2s;
    capped at 8s). Re-raises the last error if no attempt produced a response.

    An explicit loop (rather than a `tenacity` decorator) is used so the final
    `(status_code, body)` survives — the caller logs the real status code as a
    durability signal (spec §10). The spec (§4.1) sanctions either form.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = await ctx.request.get(SESSION_API_URL, timeout=_REQUEST_TIMEOUT_MS)
            body = await resp.text()
        # A network/timeout error is retried below, or re-raised on the final attempt.
        except Exception as exc:
            last_exc = exc
            if attempt == _MAX_ATTEMPTS:
                raise
        else:
            if resp.status not in _RETRYABLE_STATUSES or attempt == _MAX_ATTEMPTS:
                return resp.status, body
        await asyncio.sleep(float(min(2 ** (attempt - 1), 8)))
    # Unreachable — the loop always returns or raises by the final attempt.
    raise last_exc or RuntimeError("session probe produced no response")


async def verify_flow_session(
    profile_dir: Path,
    *,
    channel: str = "chrome",
    source: str = "chrome",
) -> FlowSessionStatus:
    """Headlessly probe `profile_dir` for a usable Flow app session.

    Launches a headless persistent context on the profile, reads cookies, and
    calls the NextAuth session endpoint. Fail-closed: any failure — boundary
    violation aside — yields VERIFICATION_ERROR, never AUTHENTICATED.

    Precondition: `profile_dir` must resolve inside GFLOW_CLI_HOME. The check
    uses `strict=True` (the directory exists by the time verification runs);
    `RealChromeStrategy.login`'s own pre-`mkdir` check deliberately stays
    `strict=False` — see the design spec §4.2.
    """
    home = get_settings().home.resolve()
    try:
        profile_dir.resolve(strict=True).relative_to(home)
    except (ValueError, OSError):
        msg = f"Profile directory {profile_dir} is outside of GFLOW_CLI_HOME ({home})."
        raise SecurityError(
            msg,
        ) from None

    # Lazy import — a top-level `from .strategies import ...` would create the
    # cycle strategies -> real_chrome -> verification -> strategies.
    from .strategies import async_playwright

    status_code: int
    body: str
    try:
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel=channel,
                headless=True,
                args=["--password-store=basic"],
            )
            try:
                cookies = await ctx.cookies()
                google_session = any(c.get("name") == "SAPISID" for c in cookies)
                status_code, body = await _fetch_session(ctx)
            finally:
                await ctx.close()
    # Fail-closed: any failure here yields VERIFICATION_ERROR, never AUTHENTICATED.
    except Exception as exc:
        logger.warning("auth_flow_session_probe_error", source=source, error=type(exc).__name__)
        return FlowSessionStatus(
            outcome=FlowSessionOutcome.VERIFICATION_ERROR,
            user_email=None,
            source=source,
        )

    result = evaluate_session_response(
        status_code,
        body,
        google_session=google_session,
        source=source,
    )
    if result.outcome is FlowSessionOutcome.VERIFICATION_ERROR:
        # Observable durability signal — distinguishes a moved/changed endpoint
        # from a flaky link. The status code is safe to log; the body is not.
        logger.warning(
            "auth_flow_session_unexpected_response",
            source=source,
            status_code=status_code,
        )
    return result


async def verify_flow_profile(
    profile_dir: Path,
    *,
    source: str = "chrome",
) -> FlowSessionStatus:
    home = get_settings().home.resolve()
    try:
        profile_dir.resolve(strict=True).relative_to(home)
    except (ValueError, OSError):
        msg = f"Profile directory {profile_dir} is outside of GFLOW_CLI_HOME ({home})."
        raise SecurityError(
            msg,
        ) from None

    status_code: int
    body: str
    try:
        # type: ignore[import-untyped]
        import browser_cookie3  # pyright: ignore[reportMissingTypeStubs]
        import httpx

        cookies_file = get_cookies_path(profile_dir)
        # Unable to get key for cookie decryption
        key_file = profile_dir.joinpath(r"Local State")
        _cookies = browser_cookie3.chrome(
            cookie_file=cookies_file,
            key_file=key_file if key_file.exists() else None,
        )

        google_session = any(c.name == "SAPISID" for c in _cookies)
        headers = {
            "accept": "*/*",
            "accept-language": "ar,en-US;q=0.9,en;q=0.8",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "referer": "https://labs.google/fx/ar/tools/flow",
        }

        async with httpx.AsyncClient(
            cookies=_cookies,
            headers=headers,
            follow_redirects=True,
        ) as client:
            response = await client.get(SESSION_API_URL)
            status_code = response.status_code
            body = response.text

    # Fail-closed: any failure here yields VERIFICATION_ERROR, never AUTHENTICATED.
    except Exception as exc:
        logger.warning("auth_flow_session_probe_error", source=source, error=type(exc).__name__)
        return FlowSessionStatus(
            outcome=FlowSessionOutcome.VERIFICATION_ERROR,
            user_email=None,
            source=source,
        )

    result = evaluate_session_response(
        status_code,
        body,
        google_session=google_session,
        source=source,
    )
    if result.outcome is FlowSessionOutcome.VERIFICATION_ERROR:
        # Observable durability signal — distinguishes a moved/changed endpoint
        # from a flaky link. The status code is safe to log; the body is not.
        logger.warning(
            "auth_flow_session_unexpected_response",
            source=source,
            status_code=status_code,
        )
    return result
