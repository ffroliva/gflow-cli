"""Headless session renewal — keep a saved profile's Google/Flow cookies fresh.

Google rotates its session cookies (SID/SAPISID family) and Flow re-mints its
NextAuth session only when a real browser visits the app. A profile that sits
unused for weeks drifts towards ``AuthExpiredError`` even though nothing about
the account changed. ``gflow auth login`` fixes that, but it needs a human at a
headed window. This module does the unattended half: open the profile headless,
load the Flow app the way generation does, let the browser persist whatever
cookies Google and NextAuth rotated, then prove the result with the same
oracle ``gflow auth status`` uses.

It cannot resurrect a session Google has actually revoked (password change,
"sign out of all devices", an identity re-check). Those land on
accounts.google.com, the verdict comes back unauthenticated, and the caller
points the user at ``gflow auth login``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.profile_lease import ProfileLease

from .verification import FlowSessionOutcome, FlowSessionStatus, verify_flow_profile

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

_NAVIGATION_TIMEOUT_MS = 60_000
#: Upper bound on waiting for the app's own session fetches to finish after the
#: DOM is up. Flow keeps long-polling connections open on some accounts, so
#: ``networkidle`` may never arrive; running out of this budget is not a failure.
_SETTLE_TIMEOUT_MS = 15_000
#: labs.google's own NextAuth. `FLOW_URL` 308-redirects to flow.google.com on accounts
#: Flow serves there, so the Flow visit never runs labs' NextAuth. Measured live
#: 2026-10-14: eight stale profiles renewed by the Flow visit alone all verified, and
#: aisandbox-pa still refused every Bearer labs minted for them. A stale labs session
#: answers 200 with an expired token and ``error: ACCESS_TOKEN_REFRESH_NEEDED``; the labs
#: landing page's own JS then signs in again silently (signin/google -> OAuth 302 ->
#: callback), ~2.5 s after load. The credits fast path (`api/credits.py`) reads that
#: cookie, so renewal loads the page and polls the session until the error clears.
_LABS_HOME_URL = "https://labs.google/fx/"
_LABS_SESSION_URL = "https://labs.google/fx/api/auth/session"
_LABS_REFRESH_NEEDED = "ACCESS_TOKEN_REFRESH_NEEDED"
_LABS_REFRESH_TIMEOUT_S = 20.0
_LABS_POLL_INTERVAL_S = 1.0
#: Verdicts observed on accounts Flow holds at /about (2026-10-14/15): the verifier
#: says fine, or Google-but-not-Flow. Only these are re-labelled `public_landing`.
_ABOUT_VERDICTS = (FlowSessionOutcome.AUTHENTICATED, FlowSessionOutcome.GOOGLE_SESSION_ONLY)


@dataclass(frozen=True)
class RenewResult:
    """What a renewal observed, not only what the verifier concluded afterwards.

    The verifier alone is not enough: on an account Flow sends to its public `/about`
    page it still reports the session as fine (KNOWN_ISSUES, #902), and that is exactly
    the account a renewal cannot fix. So the two observations made on the way are kept.
    """

    verdict: FlowSessionStatus
    #: `flow_landing_kind` of the Flow visit — ``"public"`` is the `/about` page.
    landing: str | None
    #: Whether labs' session still said ACCESS_TOKEN_REFRESH_NEEDED at the end.
    #: ``None`` when that was never observed: the labs step failed, or labs answered
    #: something other than a 200 JSON object. Unknown is not fresh (#982).
    labs_stale: bool | None

    @property
    def result(self) -> str:
        """The `gflow auth renew` row value — the one place the renewal rule lives.

        A public landing wins over the two verdicts measured on /about accounts
        (authenticated, google_session_only); any other verdict is reported as itself.
        """
        outcome = self.verdict.outcome
        if self.landing == "public" and outcome in _ABOUT_VERDICTS:
            return "public_landing"
        if outcome is not FlowSessionOutcome.AUTHENTICATED:
            return self.verdict.outcome.value
        if self.labs_stale is None:
            return "labs_unverified"
        if self.labs_stale:
            return "labs_stale"
        return FlowSessionOutcome.AUTHENTICATED.value

    @property
    def renewed(self) -> bool:
        return self.result == FlowSessionOutcome.AUTHENTICATED.value


def _session_stale(body: str) -> bool | None:
    """Whether the labs session body says ACCESS_TOKEN_REFRESH_NEEDED; ``None`` when it
    is not a JSON object. Only ``error`` is read, never the token."""
    try:
        data: object = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return cast("dict[str, object]", data).get("error") == _LABS_REFRESH_NEEDED


async def _await_labs_refresh(page: Any, source: str) -> bool | None:
    """Poll labs' session until its silent re-sign-in lands or the budget runs out.

    Returns whether the session is still stale, or ``None`` when labs did not answer
    with a readable session. Each request is capped at the budget left (floor 1 s),
    so the whole wait stays within ~_LABS_REFRESH_TIMEOUT_S.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _LABS_REFRESH_TIMEOUT_S
    while True:
        remaining_ms = max(deadline - loop.time(), 1.0) * 1000
        resp = await page.request.get(_LABS_SESSION_URL, timeout=remaining_ms)
        stale = _session_stale(await resp.text()) if resp.status == 200 else None
        if not stale or loop.time() >= deadline:
            # Status and the verdict only — the body carries the access token.
            logger.info("auth_renew_labs_session", source=source, status=resp.status, stale=stale)
            return stale
        await asyncio.sleep(_LABS_POLL_INTERVAL_S)


async def renew_profile(profile_dir: Path, *, source: str = "renew") -> RenewResult:
    """Refresh ``profile_dir``'s session headlessly and report what was observed.

    Raises ``ProfileLockedError`` when another gflow process holds the profile
    and ``ProfileEngineDowngradeError`` when the profile was written by a newer
    browser than the one that would open it — both before any browser starts.
    A launch failure (the user's own Chrome holds the profile, the channel is not
    installed) raises Playwright's ``Error``; callers record it per profile.
    A navigation failure is logged and NOT raised: the verification that
    follows is the verdict, and it is fail-closed on its own.
    """
    from playwright.async_api import Error as PlaywrightError

    from gflow_cli.api.transports._common import FLOW_URL, flow_host_kind, flow_landing_kind
    from gflow_cli.browser_manager import channel_for_profile, ensure_profile_engine_compatible

    # Through the strategies module so tests patch one seam for every auth launch.
    from .strategies import async_playwright

    channel = channel_for_profile(profile_dir)
    landing: str | None = None
    labs_stale: bool | None = None
    # Lease first, then the #477 guard: a check made before the lease could
    # validate a 'Last Version' the previous holder rewrites as it releases.
    async with ProfileLease(profile_dir):
        ensure_profile_engine_compatible(profile_dir, channel)
        async with async_playwright() as pw:
            # Same flags as the generation context (api/client.py): the basic
            # password store is how every gflow launch seals and reads cookies,
            # and the automation flags would mark the visit as a bot.
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel=channel,
                headless=True,
                ignore_default_args=["--enable-automation", "--no-sandbox"],
                args=[
                    "--password-store=basic",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            try:
                await ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})",
                )
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                try:
                    await page.goto(
                        FLOW_URL, wait_until="domcontentloaded", timeout=_NAVIGATION_TIMEOUT_MS
                    )
                    try:
                        await page.wait_for_load_state("networkidle", timeout=_SETTLE_TIMEOUT_MS)
                    except PlaywrightError:
                        pass  # see _SETTLE_TIMEOUT_MS
                    # Host/landing labels only — never the URL, which can carry
                    # OAuth state on an accounts.google.com hop.
                    landing = flow_landing_kind(page.url)
                    logger.info(
                        "auth_renew_landed",
                        source=source,
                        host=flow_host_kind(page.url),
                        landing=landing,
                    )
                except PlaywrightError as exc:
                    logger.warning(
                        "auth_renew_navigation_error", source=source, error=type(exc).__name__
                    )
                try:
                    await page.goto(
                        _LABS_HOME_URL,
                        wait_until="domcontentloaded",
                        timeout=_NAVIGATION_TIMEOUT_MS,
                    )
                    labs_stale = await _await_labs_refresh(page, source)
                except PlaywrightError as exc:
                    logger.warning(
                        "auth_renew_labs_session_error", source=source, error=type(exc).__name__
                    )
            finally:
                # Closing the persistent context is what flushes the rotated
                # cookies to the profile's on-disk store.
                await ctx.close()

    # Outside the lease: the verifier's cookie-decrypt fallback takes its own,
    # and a same-process double acquire fails fast by design.
    verdict = await verify_flow_profile(profile_dir, source=source)
    return RenewResult(verdict=verdict, landing=landing, labs_stale=labs_stale)
