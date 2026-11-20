"""Flow's agent mode on the migrated composer: the toggle, the agent panel, the agent-only check.

A part of `MigratedComposer`, split out of `_mc_editor_gate.py` (#1374, #1384) so the editor
gate keeps readiness while reading the Agent toggle, closing the agent panel, leaving agent
mode and naming #799's agent-only composer live in one place. Moved byte-identical.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from gflow_cli.api.transports._mc_common import (
    AGENT_MODE_CHIP,
    AGENT_MODE_CHIP_ANY,
    AGENT_ONLY_ANCHOR,
    AGENT_ONLY_SETTLE_S,
    AGENT_PANEL,
    AGENT_PANEL_CLOSE,
    AGENT_PANEL_SETTLE_S,
    AgentModeExit,
)
from gflow_cli.api.transports._mc_media import MediaMixin
from gflow_cli.errors import UiSelectorDriftError

if TYPE_CHECKING:
    from playwright.async_api import Page


log = structlog.get_logger(__name__)


class AgentModeMixin(MediaMixin):
    """Read Flow's Agent toggle, close its agent panel, leave agent mode, name #799's composer."""

    @staticmethod
    async def _agent_chip_pressed(page: Page) -> bool | None:
        """Is Flow's agent-mode chip pressed right now? One count, and never the failure.

        ``None`` — the query did not answer at all — is a third answer, not a quiet
        ``False``. Both of the claims this feeds are unsafe to make on an unanswered
        probe: "you were in agent mode" sends a user to fix a mode they may never have
        been in, and its negation, "the mode is off, so file a drift bug", sends a user
        whose account really is pinned to file a bug about a healthy frontend. A caller
        that only needs the safe direction can use the falsiness; one that reports on the
        absence has to look at ``None``.
        """
        try:
            return bool(await page.locator(AGENT_MODE_CHIP).first.count())
        except Exception as e:  # noqa: BLE001 - an unreadable page is not an answer
            log.warning("migrated.agent_mode_probe_failed", error=str(e)[:200])
            return None

    @staticmethod
    async def _is_agent_only_composer(page: Page, trigger: Any) -> bool:
        """True when the page is #799's agent-only cohort rather than selector drift.

        Two facts, and it takes both. The readiness anchor is **in the DOM but not
        visible** — a trigger that is simply gone is a renamed selector, our bug, and
        must keep saying so. And **no agent-mode chip exists at all**, pressed or not:
        the chip is what makes a hidden trigger recoverable (#749), so its absence is
        what says this account has no classic arm to go back to.

        An un-pressed chip is deliberately excluded from the claim, and measuring it is
        what makes the rest of this worth doing: on a live healthy migrated composer
        (ffroliva, 2026-09-13, $0) the chip IS present and un-pressed while the trigger
        is visible — so a normal account on this host has a chip, and having none is the
        anomaly. An un-pressed chip beside a HIDDEN trigger is the state never observed;
        it contradicts the measured mechanism (pressed ⇒ hidden) and falls through to
        drift rather than being asserted as a cohort.

        Both are absences, so the verdict also needs one presence — the agent composer's own
        settings button — and is asked a second time after ``AGENT_ONLY_SETTLE_S``: a slow
        mount can put the trigger in the DOM seconds before the chip, and a classic account
        called agent-only would get account defaults written to it.

        Fail-closed: an unreadable page is not evidence of a cohort.
        """
        try:
            for attempt in range(2):
                if attempt:
                    await asyncio.sleep(AGENT_ONLY_SETTLE_S)
                if not await trigger.count() or await trigger.is_visible():
                    return False
                # An open agent panel hides the toggle (#1374): a panel that mounted after
                # the close looked is not this cohort. The gate re-enters and closes it.
                if await page.locator(AGENT_PANEL).first.is_visible():
                    return False
                if await page.locator(AGENT_MODE_CHIP_ANY).first.count():
                    return False
                if not await page.locator(AGENT_ONLY_ANCHOR).first.count():
                    return False
            return True
        except Exception as exc:  # noqa: BLE001 - an unreadable page is not an answer
            log.warning("migrated.agent_only_probe_failed", error=str(exc)[:200])
            return False

    @classmethod
    async def _exit_agent_mode(cls, page: Page) -> tuple[AgentModeExit, Exception | None]:
        """Turn Flow's agent mode off if it is on.

        Agent mode `hidden`s the settings trigger this driver waits on (#749), and Flow
        remembers the chip per account — so without this, a single click in a browser
        leaves every later run failing as selector drift. :data:`AGENT_MODE_CHIP` matches
        only ``aria-pressed='true'``, so on a healthy composer this is one cheap count.

        The three outcomes are not interchangeable, which is why this is not a bool:
        ``"clicked"`` means the page was asked to change and may still be re-rendering,
        ``"blocked"`` means it was never asked — there is nothing to wait for — and
        ``"absent"`` means the mode was not the problem. ``"blocked"`` carries the click's
        own exception, so the caller can chain it instead of truncating it into a warning
        nobody reads.

        The agent panel comes first (#1374, #1384): while it is open the toggle is not in
        the page at all, on both of Flow's builds, so looking for the toggle first read a
        recoverable page as #799's agent-only composer, or as drift. The panel is only
        open in agent mode, so closing it cannot push a healthy composer INTO agent mode;
        and this runs only after the readiness gate has failed. A close that does not
        land is ``"blocked"``. A close that lands but brings no toggle back leaves the
        verdict to the checks that follow, unchanged.
        """
        closed, close_error = await cls._close_agent_panel(page)
        if close_error is not None:
            return "blocked", close_error
        if closed:
            # The toggle, or the agent-only composer's own `tune`, whichever renders first:
            # an account with no classic arm never shows a toggle, and waiting the whole
            # settle for one would tax every such run.
            try:
                await page.locator(f"{AGENT_MODE_CHIP_ANY}, {AGENT_ONLY_ANCHOR}").first.wait_for(
                    state="visible", timeout=int(AGENT_PANEL_SETTLE_S * 1000)
                )
            except Exception as e:  # noqa: BLE001 - the read below decides, not the wait
                log.info("migrated.agent_toggle_not_back", error=str(e)[:200])

        if not await cls._agent_chip_pressed(page):
            return "absent", None

        try:
            await page.locator(AGENT_MODE_CHIP).first.click(timeout=5000)
        except Exception as e:  # noqa: BLE001 - the caller decides what it means
            log.warning("migrated.agent_mode_exit_failed", error=str(e)[:200])
            return "blocked", e
        # A warning, not info: gflow just changed a setting Flow keeps across reloads.
        log.warning(
            "migrated.agent_mode_exited",
            issue_ref="#749",
            note="Flow keeps this across reloads: agent mode stays off until switched on again",
        )
        return "clicked", None

    @staticmethod
    async def _close_agent_panel(page: Page) -> tuple[bool, Exception | None]:
        """Close Flow's agent panel if it is open: ``(closed, error)``.

        One click per call, and only when the panel is visible: once closed it stays
        closed, so a re-entry of the gate does not click again.
        """
        panel = page.locator(AGENT_PANEL).first
        try:
            if not await panel.is_visible():
                return False, None
            # #1273's rule: several matches is a miss, never a `.first` guess. A wrong click
            # here could remove an attachment or press an unrelated control.
            close = page.locator(AGENT_PANEL_CLOSE)
            matches = await close.count()
            if matches != 1:
                raise UiSelectorDriftError(
                    detail=(
                        f"migrated host: the agent panel's close button ({AGENT_PANEL_CLOSE}) "
                        f"matched {matches} elements, so none was clicked (host=migrated)"
                    )
                )
            await close.click(timeout=5000)
            await panel.wait_for(state="hidden", timeout=int(AGENT_PANEL_SETTLE_S * 1000))
        except Exception as e:  # noqa: BLE001 - the caller reports it as drift
            log.warning("migrated.agent_panel_close_failed", error=str(e)[:200])
            return False, e
        log.info("migrated.agent_panel_closed", issue_ref="#1374")
        return True, None
