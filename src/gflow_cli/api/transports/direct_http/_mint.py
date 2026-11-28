# pyright: reportUnusedFunction=false, reportPrivateUsage=false
"""The single browser step: a reCAPTCHA token minted on an armed flow.google.com page."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog

from gflow_cli.api._engine import mint_evaluate_kwargs
from gflow_cli.api.recaptcha import TokenMinter
from gflow_cli.api.submit_policy import needs_rearm, not_sent
from gflow_cli.api.transports._common import flow_host_kind
from gflow_cli.api.transports.direct_http._gateway import _ORIGIN
from gflow_cli.errors import RecaptchaError

if TYPE_CHECKING:
    from playwright.async_api import Page

log = structlog.get_logger(__name__)

_MINT_BUDGET_S = 10.0  # how long a freshly navigated page gets to load reCAPTCHA
_MINT_RETRY_S = 0.5
_NAVIGATE_MS = 45_000  # the composer's own budget for reaching a project
_PROJECT_PREFIX = f"{_ORIGIN}/project/"


async def _arm_page(page: Page, project_id: str) -> None:
    """Put ``page`` on a flow.google.com project; a page already on one is not navigated.

    A token minted on one project's page is accepted for another (measured 3/3), and
    ``localStorage`` is the profile's, so any project page serves the mint and the selection
    read. An account Flow serves on labs.google is redirected there, and the direct path
    does not serve it: ``NotSent``.
    """
    try:
        if not page.url.startswith(_PROJECT_PREFIX):
            await page.goto(
                f"{_PROJECT_PREFIX}{project_id}",
                wait_until="domcontentloaded",
                timeout=_NAVIGATE_MS,
            )
    except Exception as exc:
        raise not_sent(f"navigation_failed:{type(exc).__name__}") from exc
    if flow_host_kind(page.url) != "migrated":
        raise not_sent("served_labs")


async def _mint(page: Page, project_id: str, action: str) -> str:
    """The single browser step: a reCAPTCHA token for ``action``, minted on a project page.

    The page is armed first (:func:`_arm_page`). Right after a navigation the enterprise
    script is not loaded yet and the mint raises ``RecaptchaError`` (measured 2026-11-23 on
    an i2i run with ``--project``). A mint spends nothing, so it is retried, every attempt
    bounded, until the script answers or the budget runs out.
    """
    await _arm_page(page, project_id)
    minter = TokenMinter(page, mint_evaluate_kwargs=mint_evaluate_kwargs())
    deadline = time.monotonic() + _MINT_BUDGET_S  # a bounded wait, not the re-arm clock
    while True:
        try:
            remaining = max(deadline - time.monotonic(), 0.1)
            token = await asyncio.wait_for(minter.mint(action), remaining)
            log.info("direct.minted", action=action)
            return token
        except RecaptchaError as exc:
            if time.monotonic() >= deadline:
                raise not_sent("mint_failure:RecaptchaError") from exc
            await asyncio.sleep(_MINT_RETRY_S)
        except Exception as exc:
            raise not_sent(f"mint_failure:{type(exc).__name__}") from exc


class PageTokenSource:
    """Mints on one transport's armed page; builds the per-attempt ``ProjectTokens``.

    It does not implement the ``RecaptchaTokenSource`` port itself: ``for_project`` builds the
    ``ProjectTokens`` objects that do, one per attempt.

    Only the mint holds the page: ``lock`` (the transport's page lock, also held by the UI
    path) covers re-arm, navigation-if-needed and the mint, never the POST that follows.
    The page stays armed until a wall-clock gap since the last mint passes the re-arm
    threshold or a refusal discards it; either way the next mint opens a new page in the
    same context. ``clock`` is wall time: monotonic time counts through sleep on Windows.
    """

    def __init__(
        self, page: Page, *, lock: asyncio.Lock, clock: Callable[[], float] = time.time
    ) -> None:
        self._page = page
        self._owned = False  # the first page is the transport's pool page, never closed here
        self._lock = lock
        self._clock = clock
        self._last_mint_wall: float | None = None
        self._discarded = False

    @property
    def page(self) -> Page:
        """The currently armed page; read it under ``lock``, which a re-arm also holds."""
        return self._page

    def for_project(self, project_id: str) -> ProjectTokens:
        """One attempt's token source: navigates (only if needed) to ``project_id``."""
        return ProjectTokens(self, project_id)

    async def mint_on(self, project_id: str, action: str) -> tuple[Page, str]:
        """A token for ``action`` and the page that minted it."""
        async with self._lock:
            if self._discarded:
                await self._replace_page("refused")
            elif needs_rearm(last_mint_wall=self._last_mint_wall, now_wall=self._clock()):
                await self._replace_page("wall_gap")
            page = self._page
            token = await _mint(page, project_id, action)
            self._last_mint_wall = self._clock()
            return page, token

    def discard(self, page: Page) -> None:
        """Mark ``page`` refused: a refused page stays refused while fresh pages pass."""
        if page is self._page:
            self._discarded = True

    async def _replace_page(self, reason: str) -> None:
        old, old_owned = self._page, self._owned
        try:
            self._page = await old.context.new_page()
        except Exception as exc:
            raise not_sent(f"rearm_failed:{type(exc).__name__}") from exc
        # A fresh page has no mint yet: a failed first mint on it must not re-arm again.
        self._owned, self._discarded, self._last_mint_wall = True, False, None
        log.info("mint.rearmed", reason=reason)
        if old_owned:
            try:
                await old.close()
            except Exception as exc:  # best-effort: the new page already serves the mint
                log.warning("direct.page_close_failed", error_type=type(exc).__name__)


class ProjectTokens:
    """``RecaptchaTokenSource`` for one attempt; remembers the page its token came from."""

    def __init__(self, source: PageTokenSource, project_id: str) -> None:
        self._source = source
        self._project_id = project_id
        self._minted_on: Page | None = None

    async def mint(self, action: str) -> str:
        self._minted_on, token = await self._source.mint_on(self._project_id, action)
        return token

    def discard(self) -> None:
        """Discard the page whose token this attempt sent, if it is still the armed one."""
        if self._minted_on is not None:
            self._source.discard(self._minted_on)
