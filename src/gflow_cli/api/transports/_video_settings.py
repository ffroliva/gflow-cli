"""Model, duration, sub-mode, count and aspect settings.

A part of `VideoGenerationMixin`, moved byte-identical from
`ui_automation_video.py` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from gflow_cli.api.transports._common import close_menu, count_visible, offered_menu_labels
from gflow_cli.api.transports._video_common import (
    MODEL_PICKER_TRIGGER,
    VIDEO_ASPECT_TAB_SELECTORS,
    VIDEO_MODEL_OPTION_SELECTORS,
    VIDEO_SUBMODE_SELECTORS,
    _capture_debug_screenshot,
    screenshot_clause,
    selector_drift_detail,
)
from gflow_cli.api.transports._video_editor_mode import EditorModeMixin
from gflow_cli.api.transports.migrated_composer import _duration
from gflow_cli.api.video import Aspect, VideoModel
from gflow_cli.errors import UiSelectorDriftError, VideoModelSelectionError

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin

log = structlog.get_logger(__name__)


class VideoSettingsMixin(EditorModeMixin):
    """Model, duration, sub-mode, count and aspect settings."""

    @staticmethod
    async def _set_output_count(page: Page, n: int, *, out_dir: Path | None = None) -> None:
        """Set the output count to `n` (1-4). Flow defaults to x2 (two videos =
        double credits — spec §10.5). Disambiguated by label text, NOT
        id-suffix — '-trigger-4' collides with the DURATION 4s tab (exact
        text match keeps '4s' from ever colliding with 'x4'). BOTH affix
        orders are probed for every digit ('x1' current / '1x' legacy — the
        issue #404 rename class), so the next affix flip degrades to the
        fallback selector instead of a miss.

        Fatal on miss: count is a credit MULTIPLIER (every
        ``GenerateVideoRequest`` carries a definite 1-4 value), and a miss
        hands the run to Flow's sticky default — typically x2, silently
        DOUBLING spend for count=1 and under-delivering for count=3/4. This
        runs before frame attach and submit, so refusing spends nothing —
        the same pre-spend contract as the duration probe (#288) and the
        required i2v model select (#125)."""
        labels = (f"x{n}", f"{n}x")
        selectors = tuple(f"[role='tab']:text-is('{label}')" for label in labels) + tuple(
            f"[role='tab']:has-text('{label}')" for label in labels
        )
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "count_tab",
            selectors,
        )
        if tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_count_tab.png")
            raise UiSelectorDriftError(
                selector_drift_detail(
                    "count_tab",
                    f"the output-count tab for count={n} (label 'x{n}' or '{n}x') was "
                    f"not found on the Flow editor; refusing to proceed — Flow's "
                    f"sticky default (typically x2) would silently change how many "
                    f"clips this run generates and bills for. No credits were spent.",
                    shot,
                )
            )
        await tab.click()
        await page.wait_for_timeout(400)
        log.info("ui_automation_video.output_count_set", count=n)

    @staticmethod
    async def _select_video_model(
        page: Page,
        model: VideoModel,
        *,
        out_dir: Path | None,
    ) -> None:
        """Open the model picker and select *model*, or FAIL before spending.

        Every miss is FATAL (changed 2026-08-26). This previously refused only
        when ``required=True`` (i2v with frames, issue #125); a plain t2v miss
        logged "Flow default model applies" and returned, so the run generated on
        whatever model Flow last had selected and CHARGED FOR THAT TIER. Video is
        the credit-bearing arm — veo-quality costs 100 against veo-lite's 10.

        Refusing is unambiguously correct here, unlike the image panel-miss,
        which stayed a warning. ``configure_video_settings`` calls this **only**
        when ``effective_model is not None``, and ``--model`` defaults to
        ``None`` on every video command, so reaching this function means a model
        was EXPLICITLY requested. There is no "default or asked-for?" ambiguity.

        AMBIGUOUS is a failure, not a ``.first`` guess: ``.first`` resolves by
        DOM order, so an ambiguous selector silently picks whichever Flow renders
        first and changes behaviour with no code change on our side.
        ``has-text`` is a SUBSTRING match and ``Veo 3.1 - Lite`` is a prefix of
        ``Veo 3.1 - Lite [Lower Priority]``, so this is a live hazard, not a
        theoretical one.

        Reliability (issue #125): the trigger click occasionally does not open
        the menu. We click the trigger and probe up to two times, pressing
        Escape between attempts to reset the dropdown state.

        Runs before frame attach and submit, so a refusal spends nothing.
        """
        option_sel = VIDEO_MODEL_OPTION_SELECTORS.get(model)
        if option_sel is None:
            # Unreachable for the registered models — the governance test pins
            # that every VideoModel has a selector — but keeps the contract if
            # one is added without one.
            raise VideoModelSelectionError(
                detail=f"no model-picker selector registered for {model.value!r}",
                route="model_option",
            )
        trigger = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "model_picker_trigger",
            (MODEL_PICKER_TRIGGER,),
        )
        if trigger is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_model_picker.png")
            raise VideoModelSelectionError(
                detail=(
                    f"model picker trigger not found; cannot select {model.value!r}. "
                    f"Refusing to proceed — generating on whatever model Flow last "
                    f"had selected would spend credits on a tier that was not "
                    f"requested (refs #125). No credits were spent."
                    f"{screenshot_clause(shot)}"
                ),
                route="model_picker_trigger",
            )

        offered: list[str] = []
        for attempt in (1, 2):
            await trigger.click()
            await page.wait_for_timeout(600)
            offered = await offered_menu_labels(page)
            matches, first_visible = await count_visible(page, option_sel)
            if matches > 1:
                await close_menu(page)
                raise VideoModelSelectionError(
                    detail=(
                        f"selector for {model.value!r} is AMBIGUOUS — {matches} entries "
                        f"match {option_sel!r}. Selecting .first would pick by DOM order "
                        f"and could charge a different credit tier than requested. "
                        f"Flow offered: {offered}. No credits were spent."
                    ),
                    route="model_option",
                )
            if matches == 1:
                await first_visible.click()
                await page.wait_for_timeout(800)
                log.info("ui_automation_video.model_selected", model=model.value, via=option_sel)
                return
            # The menu may not have opened (trigger click raced) or rendered the
            # option late. Escape closes only the dropdown (the settings popover
            # underneath stays open), then we retry the trigger click once.
            log.debug(
                "ui_automation_video.model_option_retry",
                model=model.value,
                attempt=attempt,
                offered=offered,
            )
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)

        log.warning(
            "ui_automation_video.model_option_not_found", model=model.value, offered=offered
        )
        # Screenshot BEFORE closing: the whole point of this capture is what the
        # menu was rendering, and `close_menu` dismisses it.
        shot = await _capture_debug_screenshot(page, out_dir, "debug_model_option_not_found.png")
        await close_menu(page)
        raise VideoModelSelectionError(
            detail=(
                f"video model {model.value!r} is not selectable — no picker entry "
                f"matched after 2 attempts. Flow offered: {offered}. Refusing to "
                f"generate on a different model than requested: the wrong tier "
                f"costs different credits, caps duration differently, and only "
                f"Veo 3.1 supports an end frame (refs #125). No credits were spent."
                f"{screenshot_clause(shot)}"
            ),
            route="model_option",
        )

    @staticmethod
    async def _select_video_duration(page: Page, seconds: int, *, out_dir: Path | None) -> None:
        """Click the duration tab for `seconds` (4/6/8, or 10 for omni_flash).
        Disambiguated by the tab's NUMBER as a whole token, NOT id-suffix
        (collides with count). Must run AFTER model select — the 10s tab only
        exists once omni_flash is chosen. Fatal on miss (issue #288): this
        only runs for an explicit --duration, and duration is a contract
        parameter — a silent fall-through to Flow's default corrupts
        downstream timeline math.

        The unit is translated (`8s`, `8 giây`) while the number is not, so
        the match is `migrated_composer._duration`'s rule (#963, #1273): the
        number, then no digit (refuses `480p`, `1080p`) and no resolution
        suffix (refuses `4K`) or count suffix (refuses `4x`). The exact token
        `8s` goes first, on the tab or on a child of it (an icon + `<span>8s`
        label, which the whole-text regex cannot match): it is a format token,
        not a translated word."""
        # json.dumps doubles the backslashes, which a CSS string token needs.
        token = json.dumps(_duration(f"{seconds}s").pattern)
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "duration_tab",
            (
                f"[role='tab']:text-is('{seconds}s')",
                f"[role='tab']:has(:text-is('{seconds}s'))",
                *(
                    f"{role}:text-matches({token})"
                    for role in (
                        "[role='tab']",
                        "[role='button']",
                        "button",
                        "[role='option']",
                        "[role='menuitem']",
                    )
                ),
            ),
        )

        if tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_duration_tab.png")
            raise UiSelectorDriftError(
                selector_drift_detail(
                    "duration_tab",
                    f"the {seconds}s duration tab was not found on the Flow editor; "
                    f"refusing to proceed — Flow's default duration (typically 8s) "
                    f"would silently replace the requested value (issue #288). Omit "
                    f"--duration to accept Flow's default.",
                    shot,
                )
            )
        await tab.click()
        await page.wait_for_timeout(400)
        log.info("ui_automation_video.duration_set", seconds=seconds)

    @staticmethod
    async def _switch_video_sub_mode(page: Page, sub: str, *, out_dir: Path | None) -> None:
        """Switch the video sub-mode tab: 'frames' (I2V) or 'references' (R2V).
        Must run while the settings panel is open (after _switch_to_video_mode)."""
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            f"video_submode_{sub}",
            VIDEO_SUBMODE_SELECTORS[sub],
        )
        if tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, f"debug_no_submode_{sub}.png")
            raise UiSelectorDriftError(
                selector_drift_detail(
                    f"video_submode_{sub}",
                    f"video sub-mode tab {sub!r} not found on the Flow editor.",
                    shot,
                )
            )
        await tab.click()
        await page.wait_for_timeout(900)
        log.info("ui_automation_video.video_submode_entered", sub=sub)

    @staticmethod
    async def _select_video_aspect(page: Page, aspect: Aspect) -> None:
        """Click the aspect-ratio tab for `aspect` in the open mode dropdown.
        Non-fatal on miss — generation proceeds with Flow's default ratio."""
        candidates = VIDEO_ASPECT_TAB_SELECTORS.get(aspect)
        if candidates is None:
            log.warning("ui_automation_video.aspect_unsupported", aspect=aspect.value)
            return
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "video_aspect_tab",
            candidates,
        )
        if tab is None:
            log.warning("ui_automation_video.aspect_not_set", aspect=aspect.value)
            return
        await tab.click()
        await page.wait_for_timeout(400)
        log.info("ui_automation_video.aspect_set", aspect=aspect.value)
