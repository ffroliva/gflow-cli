"""Overlay dismissal, settled navigation and editor entry for UiAutomationTransport.

Mixed into `UiAutomationTransport` via `PageNavigationMixin` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from gflow_cli.api.transports._common import (
    await_url_settled,
    raise_if_known_landing,
)
from gflow_cli.api.transports._ui_common import (
    _BLOCK_PROBE_JS,
    _CHANGELOG_SCOPED_CLOSE,
    _EVT_OVERLAY_DISMISSED,
    _GENERIC_CLOSE,
    _NEW_PROJECT_PROBE_TIMEOUT_MS,
    _PROJECT_URL_FRAGMENT,
    CHANGELOG_IFRAME_SELECTORS,
    FLOW_URL,
    NEW_PROJECT_SELECTORS,
    OVERLAY_CLOSE_BUTTON_SELECTORS,
    TOP_BANNER_SELECTORS,
    WELCOME_SCREEN_SELECTORS,
    _capture_debug_screenshot,
    _jitter_ms,
    _try_iframe_close_button,
)
from gflow_cli.api.transports._ui_session import UiSessionMixin
from gflow_cli.api.transports.ui_automation_video import (
    screenshot_clause,
    selector_drift_detail,
)
from gflow_cli.errors import UiSelectorDriftError

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Page


log = structlog.get_logger(__name__)


class PageNavigationMixin(UiSessionMixin):
    """Overlay clearing, route settling, and project editor entry."""

    @staticmethod
    async def _probe_page_block(page: Page) -> dict[str, Any] | None:
        """Read whether the app behind any overlay is clickable. ``None`` = unreadable.

        Measured live on 2026-08-27 (#593): while Flow's announcement modal is up the
        body carries ``pointer-events: none`` and is neither ``aria-hidden`` nor
        ``inert`` — so every control reads visible and enabled yet never receives a
        click, and Playwright's actionability wait runs to timeout with no message.

        This is the only overlay signal that needs no selector: it is a property of
        being blocked, not of any particular announcement, so it holds for whatever
        Flow ships next and in any locale.
        """
        try:
            return await page.evaluate(_BLOCK_PROBE_JS)
        except Exception:  # noqa: BLE001 — a diagnostic must never break a run
            return None

    @classmethod
    async def _overlay_blocks_page(cls, page: Page) -> bool:
        """True when the app behind is unclickable. False also covers 'unreadable'."""
        state = await cls._probe_page_block(page)
        return bool(state and state.get("pointerEvents") == "none")

    @staticmethod
    async def _changelog_overlay_present(page: Page) -> bool:
        """True only for an *announcement* overlay — never for Flow's own surfaces.

        Narrower than :meth:`_detect_overlay` on purpose. A blocked body says
        something is covering the app, but Flow's own Radix menus and popovers set
        ``pointer-events: none`` on the body exactly the same way while they are open.
        Anything that acts on "blocked" alone would therefore fire mid-menu and close
        the panel it was meant to work in — the #395 shape, rediscovered.

        These two anchors cannot match a menu: both require a changelog link or the
        gstatic changelog iframe.
        """
        for sel in (*CHANGELOG_IFRAME_SELECTORS, "[role='dialog']:has(a[href*='changelog'])"):
            try:
                if await page.locator(sel).first.is_visible():
                    return True
            except Exception:  # noqa: BLE001 — a probe must never break a run
                continue
        return False

    @staticmethod
    async def _detect_overlay(page: Page) -> bool:
        """Return True if any changelog iframe, welcome screen, or top banner is visible."""
        for sel in CHANGELOG_IFRAME_SELECTORS + WELCOME_SCREEN_SELECTORS + TOP_BANNER_SELECTORS:
            try:
                if await page.locator(sel).first.is_visible(timeout=1500):
                    log.info("ui_automation.overlay_detected", selector=sel)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _try_page_close_button(
        page: Page,
        selectors: tuple[str, ...] = OVERLAY_CLOSE_BUTTON_SELECTORS,
    ) -> bool:
        """Try each close selector at page level. Returns True on success.

        ``selectors`` is split by the caller (#593): the changelog-scoped anchor runs
        unconditionally, the generic ones only once the page is known to be blocked.
        """
        for close_sel in selectors:
            try:
                loc = page.locator(close_sel).first
                if await loc.is_visible(timeout=500):
                    await loc.click(force=True)
                    await page.wait_for_timeout(_jitter_ms(1000))
                    log.info(
                        _EVT_OVERLAY_DISMISSED,
                        selector=close_sel,
                        method="close_button_page",
                    )
                    return True
            except Exception:
                continue
        return False

    async def _require_unblocked(
        self,
        page: Page,
        out_dir: Path | None,
        *,
        epoch: str,
    ) -> None:
        """Abort pre-submit, at $0, when an overlay still covers the app (#593).

        The failure this replaces: every control reads visible and enabled, the click
        is dispatched, and Playwright's actionability wait runs to timeout — a bare
        ``TimeoutError`` naming nothing. A real ``gflow image t2i`` run died that way
        three navigations deep before the modal was dismissed by hand.

        Only a *persistent* block raises. Flow's own menus and dropdowns are Radix
        surfaces that set the same property while open, so a single reading would
        turn a transient into a hard failure; the re-probe after a settle is what
        separates "a menu is open" from "the app is unreachable".
        """
        if not await self._overlay_blocks_page(page):
            return
        await page.wait_for_timeout(_jitter_ms(1000))
        if not await self._overlay_blocks_page(page):
            return
        # One more dismissal before giving up. The boundary attempt can have run
        # BEFORE the dialog existed — Flow hydrates late — in which case the detector
        # saw nothing, dismissal was a no-op, and raising here would fail a run for a
        # modal that a single retry clears.
        await self._dismiss_blocking_overlays(page, out_dir)
        if not await self._overlay_blocks_page(page):
            return
        shot = await _capture_debug_screenshot(page, out_dir, "debug_overlay_still_blocking.png")
        raise UiSelectorDriftError(
            selector_drift_detail(
                "overlay_close_button",
                (
                    f"A Flow overlay is still covering the app after dismissal ({epoch}). "
                    "Controls below it read visible and enabled but cannot receive a "
                    "click. Open the project once in Chrome, dismiss the announcement, "
                    "then re-run — the dismissal persists on your account."
                ),
                shot,
            )
        )

    @classmethod
    async def _verify_overlay_cleared(cls, page: Page, *, method: str) -> bool:
        """Confirm the dismissal actually unblocked the app; report honestly if not.

        Before #593 this helper returned ``True`` the moment a click landed, so a
        dismissal that changed nothing still logged ``overlay_dismissed`` and the run
        then timed out somewhere unrelated — the success event lied, and two canary
        REDs were read as the wrong failure because of it.

        An unreadable probe counts as cleared: the pre-#593 optimism is the safer
        default when we genuinely cannot tell, and the caller still has its own waits.
        """
        state = await cls._probe_page_block(page)
        if state is None or state.get("pointerEvents") != "none":
            return True
        log.warning(
            "ui_automation.overlay_postmortem",
            method=method,
            pointer_events=state.get("pointerEvents"),
            dialogs=state.get("dialogs"),
            note=(
                "an overlay was dismissed but the app is still unclickable — the "
                "control the next step wants is covered, not missing"
            ),
        )
        return False

    @classmethod
    async def _dismiss_blocking_overlays(
        cls,
        page: Page,
        out_dir: Path | None = None,
    ) -> bool:
        """Dismiss Flow changelog / "What's new" iframes and any blocking overlays.

        Called at stable interaction boundaries (after editor navigation, before
        UI interactions that could be intercepted by a changelog popup).

        Strategy:
        1. Check whether any changelog iframe is currently visible.
        2. If none found, return False immediately (cheap; no log noise).
        3. If found, try each close-button selector in OVERLAY_CLOSE_BUTTON_SELECTORS.
           On first visible match: force-click it, log the selector used, return True.
        4. If no close button is discoverable, press Escape as a fallback and return True.
        5. If Escape raises (extremely rare — keyboard unavailable), capture a debug
           screenshot (if out_dir provided) and return False so the caller can decide
           how to proceed. The structured warning carries enough info to identify the
           blocking element.

        Returns True if a dismissal action was taken, False if the page was
        clear (no overlay) or if dismissal could not be confirmed.
        """
        if not await cls._detect_overlay(page):
            return False

        # The changelog-scoped anchor is safe on ANY page: it cannot match a dialog
        # that carries no changelog link, so it is tried before the block probe.
        if await cls._try_page_close_button(page, _CHANGELOG_SCOPED_CLOSE):
            return await cls._verify_overlay_cleared(page, method="close_button_page")

        # #395 guard, structural rather than advisory. Everything below is generic
        # enough to match Flow's own chrome: `button:has(i:text('close'))` fits the
        # character composer's own close button, and Escape is what actually caused
        # #395 — it closed the composer and the generation went out without
        # `entityContext`, billed and silently wrong.
        #
        # A page we can positively see is still clickable is not blocked by anything,
        # so none of it may run there. Only a positive reading disables the cascade:
        # when the probe is unreadable we keep the pre-#593 behaviour rather than
        # trading a known-good dismissal for a mystery timeout.
        #
        # Deliberate trade-off: an overlay that covers a control WITHOUT blocking the
        # body (a non-modal banner) is no longer auto-closed by the generic selectors.
        # That is the price of making #395 impossible, and it is the right side of the
        # trade — #395 spent real credits, whereas KNOWN_ISSUES rates the banner case
        # Low and transient.
        state = await cls._probe_page_block(page)
        if state is not None and state.get("pointerEvents") != "none":
            log.info(
                "ui_automation.overlay_skipped_page_clickable",
                pointer_events=state.get("pointerEvents"),
                dialogs=state.get("dialogs"),
                note="detector matched but the app is reachable — not an overlay to clear",
            )
            return False

        if await cls._try_page_close_button(page, _GENERIC_CLOSE):
            return await cls._verify_overlay_cleared(page, method="close_button_page")

        if await _try_iframe_close_button(page):
            return await cls._verify_overlay_cleared(page, method="close_button_frame")

        # Escape fallback (regression test case: iframe present, no close button).
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(_jitter_ms(1000))
            log.info(
                _EVT_OVERLAY_DISMISSED,
                selector="<none>",
                method="escape",
            )
            return await cls._verify_overlay_cleared(page, method="escape")
        except Exception as exc:
            shot_path = await _capture_debug_screenshot(
                page,
                out_dir,
                "debug_overlay_dismiss_failed.png",
            )
            log.warning(
                "ui_automation.overlay_dismiss_failed",
                error=str(exc),
                screenshot=str(shot_path),
                note=(
                    "A blocking overlay was detected but could not be dismissed. "
                    "Manual intervention may be needed."
                ),
            )
            return False

    async def _settle_if_redirecting(self, page: Any) -> str | None:
        """Settle a navigation only on an account Flow actually redirects (#587).

        No resolved locale means the bootstrap probe saw Flow serve the bare URL
        without redirecting, so there is nothing to wait for and
        :func:`await_url_settled` would burn its full timeout on EVERY navigation.
        Four call sites need this; guarding only the editor entry left the others
        paying 4 s each.
        """
        if self._account_locale is None:
            return None
        return await await_url_settled(page)

    async def _enter_editor(
        self,
        page: Page,
        out_dir: Path | None = None,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
    ) -> None:
        """Create a fresh project OR navigate to an existing one.

        If ``project_id`` is provided, navigates directly to that project's
        editor URL. Otherwise clicks "+ New project" on the gallery and
        waits for ``/project/`` navigation.

        When creating a new project and the URL already contains
        ``/project/`` (Flow's PWA restored the previous project on browser
        launch), this navigates back to the gallery first, then falls
        through to the "+ New project" click — the alternative (returning
        early) would reuse the restored project and accumulate images
        across CLI invocations.
        """
        from gflow_cli.api import routes

        if project_id:
            # #580: the ACCOUNT's locale, resolved from where Flow landed at
            # bootstrap — never a hardcoded default. None omits the segment.
            url = routes.project_editor_url(self._account_locale, project_id)
            log.info("ui_automation.entering_existing_project", project_id=project_id, url=url)
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            # #580: settle BEFORE any DOM work. Logging the before/after pair is
            # how a residual redirect stays visible in the field — and it is the
            # signal the e2e gate asserts on.
            # Only wait when this account is actually redirected. The bootstrap
            # probe already settled that question: no resolved locale means Flow
            # served the bare URL without redirecting, so there is nothing to wait
            # for and the wait would be pure dead time on EVERY navigation.
            before = page.url
            settled = await self._settle_if_redirecting(page)
            if settled and settled != before:
                log.warning(
                    "ui_automation.url_redirected_after_goto",
                    requested=url,
                    was=before,
                    now=settled,
                )
            else:
                log.info(
                    "ui_automation.url_stable_after_goto",
                    url=settled or before,
                    settle_skipped=self._account_locale is None,
                )
            await self._dismiss_blocking_overlays(page, out_dir)
            await self._require_unblocked(page, out_dir, epoch="project editor")
            return

        if _PROJECT_URL_FRAGMENT in page.url:
            # Flow's PWA restores the last-visited project URL on next browser
            # launch (persistent context). Returning early here would reuse the
            # old project, accumulating images across CLI invocations instead of
            # starting fresh. Navigate back to the gallery first, then fall
            # through to the "+ New project" click below.
            # Do NOT use wait_until="networkidle" — PWAs re-render incrementally
            # and networkidle is flaky. The selector wait_for below is the real
            # readiness gate.
            log.info("ui_automation.navigating_to_gallery", restored_url=page.url)
            await page.goto(FLOW_URL, timeout=45_000)
            # #584: FLOW_URL is the bare form, which Flow redirects to the
            # account's locale AFTER goto returns. `_bypass_onboarding` clicks
            # real buttons — running it mid-redirect clicks a page that is
            # leaving. The wait_for_timeout below would absorb it, but it comes
            # after this call, not before.
            await self._settle_if_redirecting(page)
            await self._bypass_onboarding(page)

        await page.wait_for_timeout(3000)
        # #593: this branch reached the "+ New project" sweep with no overlay check at
        # all — the one navigation epoch that had none. An announcement modal here
        # costs 18 selectors x Playwright's 30 s default click timeout and then
        # reports "Could not find 'New project' CTA", which is the wrong error about
        # the wrong thing. Dismiss BEFORE `_bypass_onboarding`, which force-clicks and
        # would otherwise dispatch straight into the overlay.
        await self._dismiss_blocking_overlays(page, out_dir)
        await self._bypass_onboarding(page)
        # AFTER onboarding, not before: consent/CMP dialogs block the body too, and
        # `_bypass_onboarding` is what clears those. Gating first would abort a fresh
        # profile with "dismiss the announcement" when the real blocker is a cookie
        # banner that the very next line would have accepted.
        await self._require_unblocked(page, out_dir, epoch="gallery")
        for selector in NEW_PROJECT_SELECTORS:
            try:
                loc = page.locator(selector).first
                await loc.wait_for(state="visible", timeout=_NEW_PROJECT_PROBE_TIMEOUT_MS)
                log.info("ui_automation.clicking_new_project", selector=selector)
                # Explicit timeout: a covered-but-visible CTA passes the wait above and
                # then blocks on the default 30 s, inside `except: continue`. The
                # element is already known visible, so 5 s is generous for a real click
                # and bounds the sweep at seconds instead of minutes.
                await loc.click(timeout=5000)
                try:
                    await page.wait_for_url(
                        lambda url: _PROJECT_URL_FRAGMENT in url,
                        timeout=15_000,
                    )
                    log.info("ui_automation.entered_editor", url=page.url)
                    return
                except Exception:
                    log.warning(
                        "ui_automation.new_project_click_did_not_navigate",
                        selector=selector,
                    )
            except Exception:
                continue

        # Before blaming the anchor, ask the prior question: is this the gallery at all?
        # NextAuth mounts Flow's OAuth routes on labs.google itself, so a signed-out
        # session sits at `/fx/api/auth/signin?error=Callback` and passes every host
        # check this file makes — `auth/internal_chromium.py` has known that since #767;
        # no transport could see it. The 2026-09-10 RED canary is this exact page,
        # reported as a missing CTA. Consulted only HERE, after the sweep has already
        # run: an early bail would delete the DOM evidence that corrects a wrong
        # absence claim, which is how #739 shipped one (see the note below).
        await raise_if_known_landing(page, requested="the Flow gallery", at="labs.enter_editor")

        shot_path = await _capture_debug_screenshot(page, out_dir, "debug_new_project.png")
        # NO migrated-host branch here. #739 added one asserting that
        # flow.google.com "renders no '+ New project' control gflow can drive". That was an
        # UNPROVEN NEGATIVE, generalised from a sweep of two other surfaces, and a live run
        # disproved it in one click: the CTA is there, carries the `add` ligature, and the
        # Tier-1 entry above now matches it. Reaching this line on a migrated host means the
        # anchor missed — which is selector drift, exactly what the message says.
        msg = (
            f"Could not find 'New project' CTA on Flow gallery. "
            f"URL: {page.url}.{screenshot_clause(shot_path)}"
        )
        raise RuntimeError(
            msg,
        )
