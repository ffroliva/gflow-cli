"""Selector cascade, agent mode and Flow-crash detection.

A part of `VideoGenerationMixin`, moved byte-identical from
`ui_automation_video.py` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from gflow_cli.api.transports._common import (
    flow_host_kind,
    raise_if_known_landing,
    raise_if_migrated,
)
from gflow_cli.api.transports._video_common import (
    _EDITOR_READY_ANCHOR,
    _PROBE_TIMEOUT_MS,
    AGENTIC_UI_INDICATORS,
    MODE_SWITCH_TRIGGER_SELECTORS,
    NON_CLASSIC_COHORT_INDICATORS,
    VIDEO_TAB_IN_MENU_SELECTORS,
    _capture_debug_screenshot,
    capture_ui_diagnostics,
    selector_drift_detail,
)
from gflow_cli.api.transports._video_network import FlowNetworkMixin
from gflow_cli.errors import (
    FlowAgentUiError,
    FlowAppError,
    FlowHostMigratedError,
    UiSelectorDriftError,
)

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

    from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin

log = structlog.get_logger(__name__)


class EditorModeMixin(FlowNetworkMixin):
    """Selector cascade, agent mode and Flow-crash detection."""

    @staticmethod
    async def _probe_selector_cascade(
        page: Page,
        label: str,
        candidates: tuple[str, ...],
        *,
        timeout_ms: int | None = None,
        overlay_retry: bool = True,
    ) -> Locator | None:
        """Try each selector in order; return the first visible match or None.
        Logs every attempt so a failed probe is diagnosable from the structured
        log alone.

        A total miss is checked against the overlay state before it is believed
        (#593). Flow mounts its announcement dialog on its own schedule — after
        hydration, well past the ``domcontentloaded`` boundary where dismissal runs —
        so a modal can appear *between* the navigation gate and this probe. It then
        covers the control while leaving it visible and enabled, and this cascade
        reports selector drift for an element that is present and perfectly fine.

        Proven live 2026-08-27: ``image_mode_tab`` failed with the 360p Omni
        announcement on screen and no ``overlay_detected`` anywhere in the log.

        Every selector probe routes through here, so one recovery covers all ten call
        sites instead of each caller re-deriving it.
        """
        if timeout_ms is None:
            timeout_ms = _PROBE_TIMEOUT_MS
        for selector in candidates:
            try:
                loc = page.locator(selector).first
                await loc.wait_for(state="visible", timeout=timeout_ms)
                log.info("ui_automation_video.selector_matched", probe=label, selector=selector)
                return loc
            except Exception:
                log.debug("ui_automation_video.selector_miss", probe=label, selector=selector)
        log.warning("ui_automation_video.selector_probe_failed", probe=label)
        if not overlay_retry:
            return None
        # Local import: ui_automation imports this module, so a top-level import would
        # be circular. Only a failed probe pays for it.
        from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415
            UiAutomationTransport,
        )

        if not await UiAutomationTransport._overlay_blocks_page(page):  # type: ignore[reportPrivateUsage]
            return None
        # Blocked is NOT enough to act on here. Several callers probe with Flow's own
        # settings dropdown open (`_switch_to_image_mode`, `_set_output_count`,
        # `_select_video_duration`), and a Radix popover sets `pointer-events: none`
        # on the body exactly like the announcement does. Acting on "blocked" alone
        # would close the very panel the probe is working in — #395 all over again.
        # Requiring the changelog anchor keeps this to real announcements.
        if not await UiAutomationTransport._changelog_overlay_present(page):  # type: ignore[reportPrivateUsage]
            log.debug("ui_automation_video.probe_miss_not_an_announcement", probe=label)
            return None
        log.warning(
            "ui_automation_video.probe_blocked_by_overlay",
            probe=label,
            note="the control is covered, not missing — dismissing and re-probing once",
        )
        # Re-probe regardless of the return value: it now means "dismissed AND
        # verified cleared", and an unmount that has not settled yet would otherwise
        # skip the retry and report drift for a control that just became reachable.
        await UiAutomationTransport._dismiss_blocking_overlays(page)  # type: ignore[reportPrivateUsage]
        return await VideoGenerationMixin._probe_selector_cascade(
            page,
            label,
            candidates,
            timeout_ms=timeout_ms,
            overlay_retry=False,
        )

    @staticmethod
    async def _media_panel_present(page: Page) -> bool:
        """True if the media-generation panel is mounted.

        Keyed on the locale-stable ``crop_*`` settings trigger
        (:data:`MODE_SWITCH_TRIGGER_SELECTORS`) — the same anchor the mode
        switches probe. Its presence is the signal that the composer is in media
        (Image/Video) mode rather than Agent mode, which removes the panel.
        """
        for sel in MODE_SWITCH_TRIGGER_SELECTORS:
            if await page.locator(sel).count() > 0:
                return True
        return False

    @staticmethod
    async def _dismiss_agent_affordances(page: Page, *, allow_reload: bool = False) -> bool:
        """Bring the composer back to classic media mode; return True if it acted.

        Delegates to the robust :func:`mode_control.ensure_media_mode`, which is
        **state-aware**: it reads the Agent toggle's ``aria-pressed`` (the
        locale-invariant source of truth) and clicks it OFF only when actually
        on, and closes the expanded chat sidebar (X) first — replacing the older
        blind single pill-click that gave up on a still-open composer. Does not
        raise; the caller (:meth:`_exit_agent_mode`) re-checks the media panel
        and escalates via :meth:`_check_forced_agentic_ui` only if it never
        returned.

        ``allow_reload`` passes through to ``ensure_media_mode``'s pinned-arm
        reload rescue — only the pre-bind ``get_ui_driver`` path may set it (a
        reload re-rolls the cohort arm; see the mode_control docstring).
        """
        # Local import keeps mode_control a leaf and avoids any import cycle.
        from gflow_cli.api.transports import mode_control

        return await mode_control.ensure_media_mode(page, allow_reload=allow_reload)

    @staticmethod
    async def _check_forced_agentic_ui(page: Page, out_dir: Path | None) -> None:
        """Raise ``FlowAgentUiError`` if any forced Agentic UI indicator is present."""
        for sel in AGENTIC_UI_INDICATORS:
            if await page.locator(sel).count() > 0:
                log.warning(
                    "ui_automation_video.forced_agentic_ui_detected",
                    indicator=sel,
                    note="Forced Agentic UI detected; classic media panel is not recoverable.",
                )
                shot_path = await _capture_debug_screenshot(
                    page, out_dir, "debug_forced_agent_ui.png"
                )
                raise FlowAgentUiError(
                    detail=(
                        f"Agentic UI detected via indicator {sel!r}. "
                        f"Viewport screenshot: {shot_path}"
                    )
                )

    @staticmethod
    async def _detect_non_classic_cohort(page: Page) -> str | None:
        """Return the first :data:`NON_CLASSIC_COHORT_INDICATORS` selector present,
        else ``None``. Called at the mode-switch RAISE site — after ``crop_*`` is
        confirmed absent AND the ~24s crop cascade has already elapsed, so the page
        is fully rendered and one scan is decisive: the known agentic/media-library
        cohort (#174/#183 → a clean :class:`FlowAgentUiError`) vs genuine drift."""
        for sel in NON_CLASSIC_COHORT_INDICATORS:
            try:
                if await page.locator(sel).count() > 0:
                    return sel
            except Exception:  # noqa: BLE001  # NOSONAR — best-effort probe
                continue
        return None

    @staticmethod
    async def _is_flow_app_crash(page: Page) -> bool:
        """True if Flow's web app rendered its client-side-exception error boundary
        (a transient Flow crash) instead of the editor — keyed on the (English,
        Next.js-hardcoded) error-page title. Best-effort; never raises."""
        try:
            title = (await page.title()) or ""
        except Exception:  # noqa: BLE001  # NOSONAR — best-effort probe
            return False
        return "application error" in title.lower()

    @staticmethod
    async def _mode_switch_error(
        page: Page, out_dir: Path | None, *, media: str
    ) -> FlowAppError | FlowHostMigratedError | FlowAgentUiError | UiSelectorDriftError:
        """Build (do NOT raise — the caller raises) the right error for a
        ``mode_switch_trigger`` miss on the ``image``/``video`` path: dump DOM
        diagnostics, then classify — a transient :class:`FlowAppError` if Flow's app
        crashed, a clean retryable :class:`FlowAgentUiError` for the known
        agentic/media-library A/B cohort (#174/#183), else :class:`UiSelectorDriftError`
        for genuine drift. Returning the exception (vs raising here) keeps the
        None-path terminating *visibly* at the two call sites. Shared by
        ``_switch_to_image_mode`` and ``_switch_to_video_mode``."""
        diag = await capture_ui_diagnostics(page, out_dir, "diag_mode_switch_miss")
        diag_clause = f" Diagnostics: {diag}" if diag is not None else ""
        if await VideoGenerationMixin._is_flow_app_crash(page):
            return FlowAppError(
                detail=(
                    "Flow's web app crashed (client-side exception) before the editor "
                    f"rendered, so there is no {media} generation control to drive."
                    f"{diag_clause}"
                )
            )
        # #639: checked BEFORE the cohort probe, because the cohort indicators are
        # themselves ligature selectors — on the migrated frontend they miss for the
        # same reason everything else does, so their silence proves nothing here.
        if flow_host_kind(page.url) == "migrated":
            return FlowHostMigratedError(
                detail=(
                    "Flow served this project from flow.google.com — the origin Google "
                    "is migrating the app onto — whose frontend renders none of the "
                    f"controls gflow drives, so there is no {media} generation control "
                    "here. This is not selector drift, and it is not transient: the handoff is a "
                    "per-account setting, so retrying will not land the old frontend."
                    f"{diag_clause}"
                )
            )
        cohort = await VideoGenerationMixin._detect_non_classic_cohort(page)
        if cohort is not None:
            return FlowAgentUiError(
                detail=(
                    "Flow opened this project in its new media-library / agentic "
                    f"composer (server-side A/B cohort, issues #174/#183; matched "
                    f"{cohort!r}) — there is no classic aspect/mode control for gflow to "
                    f"drive {media} generation here. This cohort flaps; retrying in a few "
                    f"minutes often lands the classic UI.{diag_clause}"
                )
            )
        return UiSelectorDriftError(
            selector_drift_detail(
                "mode_switch_trigger",
                "no matching element found on the Flow editor. No known Flow "
                "cohort matched either — the editor may be a new Flow UI layout "
                "this gflow-cli version does not recognize yet (issue #493).",
                None,
            )
            + diag_clause
        )

    @staticmethod
    async def _exit_agent_mode(
        page: Page, *, out_dir: Path | None = None, allow_reload: bool = False
    ) -> bool:
        """Ensure the composer is in media (Image/Video) mode, not Agent mode.

        Flow's "Agent" mode hides the media-generation panel — the ``crop_*``
        settings trigger that :meth:`_switch_to_image_mode`,
        :meth:`_switch_to_video_mode`, and ``_configure_generation_settings``
        probe disappears — so generation fails with "mode-switch dropdown trigger
        not found". Agent mode shows up in TWO shapes, and which one appears on a
        given project open is non-deterministic (Flow A/B):

        1. **In-composer pill** (:data:`COMPOSER_AGENT_TOGGLE_SELECTOR`) — an
           ``Agent`` toggle next to the prompt; clicking it returns to media mode.
        2. **Chat side-panel** (:data:`AGENT_CHAT_PANEL_CLOSE_SELECTOR`) — a docked
           "Untitled session" chat on the right; while it is up the pill is not in
           the DOM at all, so it must be dismissed (its X) first, after which the
           pill reappears (usually still active) and step 1 applies.

        This drives a small fixed-iteration loop: while the media panel is absent,
        dismiss whichever Agent affordance is present (chat panel first, then
        pill) and re-check. The loop is keyed on the OUTCOME (``crop_*`` is back),
        never on assuming which control exists — so it covers pill-only,
        panel-only, panel-then-pill, and neither (older UI) without special-casing.

        Returns ``True`` only when it actually brought the media panel back,
        ``False`` otherwise (already in media mode, nothing to act on, or the
        clicks did not re-mount the panel). Best-effort, locale-invariant
        (Material Symbols ligatures + structural anchors, no UI text, no ARIA),
        and raises FlowAgentUiError if a forced Agentic UI cohort is encountered.
        """
        try:
            # Common case: the media panel is already mounted → media mode,
            # nothing to do. Cheap no-op on every normal generation.
            if await VideoGenerationMixin._media_panel_present(page):
                return False

            # #639: the media panel is missing. Before concluding that means Agent
            # mode and spending ~10.6 s dismissing affordances that are not there,
            # ask whether this is a host we can drive at all. `_media_panel_present`
            # uses `.count()` and does not wait, but it is still the first DOM work
            # after navigation — by here a post-goto redirect to flow.google.com has
            # usually landed, which the caller's entry check was too early to see.
            raise_if_migrated(page, at="exit_agent_mode")

            acted = await VideoGenerationMixin._dismiss_agent_affordances(
                page, allow_reload=allow_reload
            )

            if await VideoGenerationMixin._media_panel_present(page):
                if acted:
                    log.info("ui_automation_video.exited_agent_mode")
                return True

            # If the media panel is not present after the exit attempts, check if
            # any of the unique Agentic UI indicators are present.
            await VideoGenerationMixin._check_forced_agentic_ui(page, out_dir)

            if acted:
                # We clicked something but the panel never came back — don't claim
                # a false "exited"; warn and let the caller's own trigger probe
                # fail loudly (with a screenshot) so the real cause surfaces.
                log.warning(
                    "ui_automation_video.exit_agent_mode_no_panel",
                    note="dismissed Agent affordance(s) but the media panel did not re-mount",
                )
            return False
        except (FlowAgentUiError, FlowHostMigratedError):
            # #639: the migrated-host abort is a real verdict, not a probe failure.
            # Falling into the handler below would log it at debug and return False,
            # which is how a fast-fail becomes invisible.
            raise
        except Exception as e:  # noqa: BLE001
            log.debug("ui_automation_video.agent_toggle_probe_failed", error=str(e)[:80])
            return False

    @staticmethod
    async def _switch_to_video_mode(page: Page, *, out_dir: Path | None) -> None:
        """Open the 2-step mode dropdown and switch to Video mode. The menu
        stays open afterward so the caller can also set aspect + count."""
        # New Flow UI: if the composer is in Agent mode the generation panel is
        # absent — return to media mode first so the trigger probe can find the
        # crop_* dropdown.
        await VideoGenerationMixin._exit_agent_mode(page, out_dir=out_dir)
        trigger = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "mode_switch_trigger",
            MODE_SWITCH_TRIGGER_SELECTORS,
        )
        if trigger is None:
            # Same gap as the image path: on the labs arm with --project, `_enter_editor`
            # returned before any readiness gate, so this is the first place that can ask
            # whether the account can reach Flow at all.
            await raise_if_known_landing(
                page, requested="the Flow editor", at="labs.switch_to_video_mode"
            )
            raise await VideoGenerationMixin._mode_switch_error(page, out_dir, media="video")
        await trigger.click()
        await page.wait_for_timeout(800)
        video_tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "video_mode_tab",
            VIDEO_TAB_IN_MENU_SELECTORS,
        )
        if video_tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_video_tab.png")
            raise UiSelectorDriftError(
                selector_drift_detail(
                    "video_mode_tab",
                    "Video tab not found in the mode dropdown.",
                    shot,
                )
            )
        await video_tab.click()
        await page.wait_for_timeout(1200)
        log.info("ui_automation_video.video_mode_entered")

    @staticmethod
    async def _wait_video_editor_ready(page: Page) -> None:
        """Wait for the editor SPA to mount before probing video controls. The
        /project/ URL nav fires before the UI renders — the Phase 0 spike found
        probes taken right after it see only the page shell. The prompt textbox
        is the ready anchor. Non-fatal on timeout (the cascade probes still
        have their own per-selector waits)."""
        try:
            await page.locator(_EDITOR_READY_ANCHOR).first.wait_for(state="visible", timeout=20_000)
            await page.wait_for_timeout(1000)
            log.info("ui_automation_video.editor_ready")
        except Exception as e:
            log.warning("ui_automation_video.editor_ready_timeout", error=str(e))
