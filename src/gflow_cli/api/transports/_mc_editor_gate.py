"""Editor readiness gate, dialog and cookie-bar dismissal, and click diagnostics.

A part of `MigratedComposer`, moved byte-identical from
`migrated_composer.py` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.transports._common import (
    raise_if_known_landing,
    safe_page_url,
)
from gflow_cli.api.transports._mc_agent_mode import AgentModeMixin
from gflow_cli.api.transports._mc_common import (
    _CLICK_POSTMORTEM_JS,
    AGENT_MODE_REMEDIATION,
    AGENT_ONLY_EARLY_S,
    AGENT_ONLY_RELOAD_WAIT_S,
    AGENT_ONLY_RELOADS,
    AGENT_RECOVERY_S,
    COOKIE_BAR,
    COOKIE_BAR_ACCEPT,
    COOKIE_BAR_REJECT,
    DIALOG,
    DIALOG_CLOSE,
    MIGRATED_PROJECT_URL,
    MIGRATED_SETTINGS_CONTRACT,
    READY_ANCHOR,
    ComposerKind,
    _recovery_detail,
    is_at,
)
from gflow_cli.errors import UiSelectorDriftError
from gflow_cli.redaction import redact_sensitive_text

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page


log = structlog.get_logger(__name__)


async def _visible_now(locator: Locator) -> bool:
    """One visibility read that never raises: an unreadable page is not a visible one."""
    try:
        return await locator.is_visible()
    except Exception:  # noqa: BLE001 - the caller treats "unknown" as "not visible"
        return False


class EditorGateMixin(AgentModeMixin):
    """Editor readiness gate, dialog and cookie-bar dismissal, and click diagnostics.

    Agent mode (the toggle, the agent panel, #799's agent-only check) is
    :class:`~gflow_cli.api.transports._mc_agent_mode.AgentModeMixin`.
    """

    async def _ui_failure_detail(
        self,
        page: Page,
        *,
        phase: str,
        selector: str,
        detail: str,
        error: BaseException | str,
    ) -> str:
        """The drift detail, stamped with the settings contract.

        No files: the incident bundle the raise produces already holds the screenshot
        and the structural DOM, inside its `sensitive/` review boundary and retention.
        A second copy in the user's output directory sat outside both (#890).
        """
        del page  # kept so every raise site reads the same; the bundle captures it
        log.warning(
            "migrated.ui_failure",
            phase=phase,
            selector=selector,
            error=error if isinstance(error, str) else type(error).__name__,
        )
        return f"{detail} [contract={MIGRATED_SETTINGS_CONTRACT}]"

    # --- readiness ------------------------------------------------------------

    async def ensure_editor_for(
        self, page: Page, project_id: str, *, needs_classic: bool
    ) -> ComposerKind:
        """:meth:`ensure_editor`, then up to ``AGENT_ONLY_RELOADS`` free reloads while the
        agent-only composer is served to a request only the classic one can take (#1142)."""
        kind = await self.ensure_editor(page, project_id)
        if not needs_classic:
            return kind
        for attempt in range(1, AGENT_ONLY_RELOADS + 1):
            if kind == "classic":
                break
            log.info("migrated.agent_only_reload", attempt=attempt, issue_ref="#1142")
            await asyncio.sleep(AGENT_ONLY_RELOAD_WAIT_S)
            try:
                await page.reload(wait_until="domcontentloaded", timeout=45_000)
                kind = await self.ensure_editor(page, project_id)
            except (PlaywrightError, UiSelectorDriftError) as exc:
                # A reload or re-check that fails spends its attempt; real drift already
                # surfaced on the first check above, so this ends in the usual exit 36.
                log.warning("migrated.agent_only_reload_failed", error=str(exc)[:200])
        return kind

    async def ensure_editor(
        self, page: Page, project_id: str, *, timeout_s: float = 30.0
    ) -> ComposerKind:
        """Land on ``flow.google.com/project/<id>`` (direct — no labs.google visit
        needed on either kind of account), wait for the settings trigger, and report
        which composer is there: ``"classic"``, or #799's ``"agent_only"``."""
        target = MIGRATED_PROJECT_URL.format(project_id=project_id)
        if not is_at(getattr(page, "url", ""), target):
            log.info("migrated.navigate", url=target)
            await page.goto(target, wait_until="domcontentloaded", timeout=45_000)
        await self._dismiss_dialog(page)
        # The glue cookie banner intercepts pointer events over the composer
        # (2026-09-09 incident 8dc6c020); clear it before anything clicks.
        await self._dismiss_cookie_bar(page)
        trigger = page.locator(READY_ANCHOR).first
        early_s = min(AGENT_ONLY_EARLY_S, timeout_s)
        try:
            await trigger.wait_for(state="visible", timeout=int(early_s * 1000))
        except Exception as e:
            # Before blaming the anchor, ask the prior question: is this even the page
            # we asked for? Flow answers a project navigation with its public /about
            # landing when it will not open that project for this session (#756), and
            # `flow_host_kind` cannot see it — /about and /project/<id> share an origin.
            # Reaching this line on a landing page means the trigger was never going to
            # be here, so probing for the agent chip below is meaningless too.
            await raise_if_known_landing(page, requested=target, at="migrated.ensure_editor")
            # Only now look for agent mode. Probing for the chip BEFORE this wait raced
            # the SPA: `goto` returns on `domcontentloaded` and Angular mounts the
            # composer seconds later, so the chip was reliably absent at that point, the
            # recovery no-opped, and the run failed exactly as it did before the fix
            # (caught by this change's own e2e, 2026-09-08). Waiting first also costs a
            # healthy run nothing — no extra query is issued unless the gate has failed.
            state, click_error = await self._exit_agent_mode(page)
            if state == "absent":
                return await self._absent_chip_verdict(
                    page, project_id, trigger, timeout_s=timeout_s, early_s=early_s, error=e
                )
            if state == "blocked":
                # Another page on this profile may have turned agent mode off between our
                # read and our click: the toggle is gone, and the trigger is back (#1374).
                if await _visible_now(trigger):
                    log.info("migrated.agent_mode_left_elsewhere", issue_ref="#1374")
                    log.info("migrated.editor_ready", url=safe_page_url(page.url))
                    return "classic"
                # Nothing was toggled, so the trigger cannot have changed — spending
                # AGENT_RECOVERY_S here buys a verdict already in hand. The click's own
                # exception IS the message: a modal eating the click is a different bug
                # from a pinned mode, and only that exception says which one this is.
                raise UiSelectorDriftError(
                    detail=(
                        f"migrated host: the account is in Flow's agent mode, which hides "
                        f"the settings trigger, and gflow could not leave it on "
                        f"{safe_page_url(page.url)} after {timeout_s:.0f}s (host=migrated): "
                        f"the agent panel's close button or the Agent switch could not be "
                        f"clicked (the click error below names which); "
                        f"{AGENT_MODE_REMEDIATION}. Readiness gate: {e}. Click: {click_error}"
                    ),
                ) from click_error
            try:
                await trigger.wait_for(state="visible", timeout=int(AGENT_RECOVERY_S * 1000))
            except Exception as e2:
                # Which of these two it is decides who can fix it, so the chip is read
                # BACK rather than assumed. Reporting both as "the mode may be pinned"
                # hides genuine selector drift behind a mode the driver already left.
                pressed = await self._agent_chip_pressed(page)
                raise UiSelectorDriftError(detail=_recovery_detail(page, pressed, e2)) from e2
        # One count() on the happy path, for the cohort that would render the agent prompt
        # box while LEAVING the trigger visible: `send_prompt` would type into the agent
        # composer ([contenteditable='true'].first) and nothing downstream would notice.
        #
        # It OBSERVES and does not act, which is the whole point. Clicking here would
        # mutate a server-remembered account setting on a run that is otherwise healthy,
        # and it would do it in the one window where that is wrong: right after a
        # successful recovery, where a chip still reporting `aria-pressed='true'` for a
        # frame would toggle the account straight back INTO agent mode. `AGENT_MODE_CHIP`
        # being self-guarding only holds while the click is reserved for a trigger that
        # did NOT come up. No cohort has been measured here, so a log line is the honest
        # instrument — the next occurrence is then diagnosable from a run instead of a
        # re-run (the same reason #719 asks for telemetry before a fix).
        if await self._agent_chip_pressed(page):
            log.warning("migrated.agent_mode_chip_pressed_while_ready", issue_ref="#752")
        log.info("migrated.editor_ready", url=page.url)
        return "classic"

    async def _absent_chip_verdict(
        self,
        page: Page,
        project_id: str,
        trigger: Locator,
        *,
        timeout_s: float,
        early_s: float,
        error: Exception,
    ) -> ComposerKind:
        """No agent-mode chip and no visible trigger: agent-only, a slow mount, or drift."""
        # #799. Before calling this drift, separate the third cohort from our own
        # bug, because the DOM they leave is the same one: the trigger present
        # under a bare `hidden`. With a pressed chip that is agent mode and the
        # branch above recovers it. With NO chip in the page at all, there is no
        # classic arm to return to — the account's only composer is the agent
        # panel, so model/aspect/count live in Agent settings as defaults and
        # nothing gflow drives is on the page. A trigger that has LEFT the DOM is
        # the opposite finding, a renamed selector, and stays drift.
        if await self._is_agent_only_composer(page, trigger):
            log.info("migrated.agent_only_composer", issue_ref="#799", url=page.url)
            return "agent_only"
        if timeout_s > early_s:
            # Neither composer yet — a slow mount. Spend the rest of the gate on a
            # full re-entry, so the chip and the discriminator get asked again.
            return await self.ensure_editor(page, project_id, timeout_s=timeout_s - early_s)
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: the settings trigger ({READY_ANCHOR}) did not "
                f"become visible within {timeout_s:.0f}s on {safe_page_url(page.url)} "
                f"(host=migrated): {error}"
            ),
        ) from error

    @staticmethod
    async def _dismiss_dialog(page: Page) -> None:
        """One attempt at the modal the host shows over a fresh editor (the "Get
        started" changelog on a first visit, #593's twin): its ``close`` icon button,
        else Escape. Best-effort — a dialog that stays is reported by whichever
        click it blocks next, with the overlay named there."""
        dialog = page.locator(DIALOG).first
        try:
            if not await dialog.is_visible():
                return
            close = page.locator(DIALOG_CLOSE).first
            via = "close" if await close.count() else "escape"
            if via == "close":
                await close.click(timeout=3000)
            else:
                await page.keyboard.press("Escape")
            log.info("migrated.dialog_dismissed", via=via)
        except Exception as e:  # noqa: BLE001 - best-effort, never the failure itself
            log.warning("migrated.dialog_not_dismissed", error=str(e)[:200])

    # --- clicking, and saying why a click did not land ---------------------------

    async def _click(self, page: Page, locator: Any, *, named: str, timeout: int) -> None:
        """Click, and when it expires report what was actually TRUE — never why.

        Playwright's actionability gate has four conditions — visible, stable, receives
        events, enabled — and a click that fails any of them expires as a bare
        ``TimeoutError`` carrying no locator, no exit code and no cause. That is #776:
        `editor_ready`, five seconds, exit 1, nothing to act on.

        **This reads; it does not diagnose.** Two candidate causes (#593, #752 finding #7)
        were unmeasurable on this host as of the 2026-09-10 spike, and a guard built on
        either would sometimes answer confidently and wrong (#770). So it states
        observations; "every reading was healthy" is one of them.

        Costs nothing on a healthy run — the read happens only in the except branch, which
        is also the rule :func:`_common.raise_if_known_landing` already states: a guard
        placed ahead of the probe deletes the evidence that would correct it.

        Only a Playwright timeout is reinterpreted. A closed page or a detached frame is a
        different failure and travels unchanged.
        """
        try:
            await locator.click(timeout=timeout)
        except PlaywrightTimeoutError as e:
            raise UiSelectorDriftError(
                detail=await self._why_the_click_missed(page, locator, named, timeout)
            ) from e

    async def _why_the_click_missed(
        self, page: Page, locator: Any, named: str, timeout: int
    ) -> str:
        """The message for a click that expired: locator first, observations after.

        Locator first is not cosmetic. ``redact_sensitive_text`` truncates to 500 chars
        at this raise site (``data/redaction.py``), so every surface sees the same cap and
        anything variable-length — the occluder's class list — has to sit behind the one
        part that must always survive it.
        """
        head = f"migrated host: {named} did not accept a click within {timeout} ms"
        try:
            # Through the LOCATOR, not a selector string. Half this driver's controls are
            # built by filtering on a ligature (`button` + `arrow_forward`), which no
            # `document.querySelector` can express — and the submit button, where losing
            # attribution costs the most, is one of them.
            state: dict[str, Any] = await locator.evaluate(_CLICK_POSTMORTEM_JS)
        except Exception as e:  # noqa: BLE001 - a diagnostic never replaces the failure
            # Detached, cross-origin, or the document replaced under us. All three are
            # "we could not look", and none of them may swallow the failure itself.
            return redact_sensitive_text(
                f"{head} — it could not be read back ({str(e)[:120]}) (host=migrated)"
            )

        seen: list[str] = []
        # Agent mode first: it is the one cause here with a user action attached, and
        # it hides the trigger with a bare `hidden` that touches neither the body's
        # pointer-events nor the hit-test — so nothing else below would notice it.
        if await self._agent_chip_pressed(page):
            seen.append(
                f"the account is in Flow's agent mode, which hides it — {AGENT_MODE_REMEDIATION}"
            )
        # One chain, because these are competing readings of the SAME question — can a
        # pointer reach it — ordered most specific first. The JS only hit-tests
        # `if (box.width && box.height)`, so an unrendered element always reports no hit
        # test too; as independent `if`s that said "it is not rendered" and "it answers no
        # hit test" about one fact.
        if state.get("hidden_attr"):
            seen.append("it carries a bare `hidden` attribute")
        elif not state.get("visible"):
            seen.append("it is not rendered (display, visibility, or a zero-sized box)")
        elif state.get("occluder"):
            seen.append(f"it is covered by {state['occluder']}")
        elif not state.get("hit_testable"):
            # Rendered, nothing named itself: whatever is on top is outside the document.
            # Not "healthy" — letting it fall through would claim hit-testable of an
            # element that had just failed the hit test.
            seen.append("it answers no hit test at its own centre")

        # Separate axes: an element can be disabled, or the whole page blocked, whatever
        # the chain above found.
        if not state.get("enabled"):
            seen.append("it is disabled")
        if state.get("body_blocked"):
            seen.append("the page is accepting no pointer events at all")

        if not seen:
            # Every readable condition is healthy. Saying so eliminates three of
            # Playwright's four checks instead of inventing one of them.
            seen.append(
                "it was visible, enabled and hit-testable at the moment the click "
                "expired, so nothing readable on the page explains it — Playwright also "
                "requires a stable bounding box, so the control was most likely still "
                "moving or being re-rendered"
            )
        # Belt and braces over the JS allowlist above.
        return redact_sensitive_text(f"{head} — {'; '.join(seen)} (host=migrated)")

    async def _dismiss_cookie_bar(self, page: Page) -> None:
        """Clear Google's consent bar, which sits on top of the controls we click.

        Called from :meth:`_open_pane` — the one place both the image and the video path
        take their FIRST click, and early enough that consent is stored before anything
        else on the page is touched. The bar was already up 114 ms after
        ``domcontentloaded`` in every measured sample, so nothing here has to wait for it.

        **Best-effort on purpose.** A bar that refuses to go is not turned into a second
        error path: the click one line later fails through :meth:`_click`, whose
        post-mortem now names ``div.glue-cookie-notification-bar`` as the occluder. One
        attributed failure beats two competing ones, and it costs no code.
        """
        bar = page.locator(COOKIE_BAR).first
        try:
            if not await bar.is_visible():
                return
            reject = bar.locator(COOKIE_BAR_REJECT)
            button = reject if await reject.count() else bar.locator(COOKIE_BAR_ACCEPT)
            await button.first.click(timeout=3000)
            await bar.wait_for(state="hidden", timeout=3000)
        except Exception as e:  # noqa: BLE001 - the click post-mortem reports what is left
            log.warning("migrated.cookie_bar_not_dismissed", error=str(e)[:120])
            return
        log.info("migrated.cookie_bar_dismissed")


async def clear_overlays(page: Page) -> None:
    """Dismiss the first-visit dialog and the cookie bar over a freshly loaded page.

    The composer's own best-effort dismissals, for flow.google.com drivers that are not
    the composer (the character editor)."""
    await EditorGateMixin._dismiss_dialog(page)  # pyright: ignore[reportPrivateUsage]
    await EditorGateMixin()._dismiss_cookie_bar(page)  # pyright: ignore[reportPrivateUsage]
