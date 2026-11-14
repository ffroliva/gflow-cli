"""Generation settings panel, image model selection, aspect ratio and count.

Mixed into `UiAutomationTransport` via `GenerationSettingsMixin` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

from gflow_cli.api.image import Model
from gflow_cli.api.transports._common import close_menu, count_visible, offered_menu_labels
from gflow_cli.api.transports._ui_common import (
    _ASPECT_TAB_CANDIDATES,
    _COUNT_TAB_TEXT_RE,
    _DIAGNOSTICS_SUBDIR,
    _EVT_COUNT_SETTER_COMPLETED,
    _SUPPORTED_COUNTS,
    GEN_SETTINGS_BUTTON_SELECTORS,
    IMAGE_MODEL_OPTION_SELECTORS,
    IMAGE_MODEL_PICKER_TRIGGER,
    _capture_debug_screenshot,
    _count_tab_locator_for,
    _count_tabs_locator,
)
from gflow_cli.api.transports._ui_prompt import PromptInputMixin
from gflow_cli.api.transports.ui_automation_video import (
    selector_drift_detail,
)
from gflow_cli.errors import UiSelectorDriftError

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Page

    from gflow_cli.api.transports.ui_automation import UiAutomationTransport


log = structlog.get_logger(__name__)


class GenerationSettingsMixin(PromptInputMixin):
    """Generation settings panel, image model selection, aspect ratio and count."""

    @staticmethod
    async def _open_gen_settings_panel(page: Page) -> bool:
        """Try selectors in order to open the per-generation settings panel.

        Returns True on success, False if no selector matched (non-fatal —
        caller falls back to Flow's current defaults).
        """
        for sel in GEN_SETTINGS_BUTTON_SELECTORS:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=3_000)
                await btn.click()
                await page.wait_for_timeout(600)
                log.info("ui_automation.gen_settings_opened", via=sel)
                return True
            except Exception:
                continue
        return False

    @staticmethod
    async def _read_displayed_count(page: Page) -> int | None:
        """Read the currently-displayed image count from the settings panel.

        Locale-invariant: filters ``[aria-selected="true"]`` tabs by
        :data:`_COUNT_TAB_TEXT_RE` so only count tabs (both label cohorts,
        e.g. "1x"/"x1", "x2") are considered.  Mode tabs ("image\\nImagem") and Aspect tabs
        ("16:9", etc.) are selected simultaneously in the Radix tablist DOM
        and would poison the old unfiltered ``[aria-selected="true"]`` query.

        Returns the integer count (1–4) extracted from the matched tab's text,
        or ``None`` when no count tab is selected / visible.
        """
        try:
            selected = page.locator('[role="tab"][aria-selected="true"]').filter(
                has_text=_COUNT_TAB_TEXT_RE,
            )
            if await selected.count() == 0:
                return None
            text = (await selected.first.text_content(timeout=500) or "").strip()
            m = re.search(r"\d", text)
            return int(m.group()) if m else None
        except Exception:
            return None

    @staticmethod
    async def _is_settings_panel_open(page: Page) -> bool:
        """True if the generation-settings panel count tabs are currently visible.

        Uses :func:`_count_tabs_locator` — the panel is open when at least one
        count tab (text matching either label cohort, e.g. ``1x``/``x1``,
        ``x2``) is visible.
        This is locale-invariant and immune to Mode/Aspect tab false-positives.
        """
        try:
            return await _count_tabs_locator(page).first.is_visible()
        except Exception:
            return False

    @staticmethod
    async def _select_image_model(page: Page, model: Model) -> None:
        """Click the image model picker and select *model*, or FAIL.

        Must run with the gen-settings panel open.

        Fails loudly by design (changed 2026-08-26). This previously swallowed
        every failure, logged a WARNING, and let the generation proceed on Flow's
        UI-default model — so a stale selector meant the user asked for one model,
        silently received another, and was BILLED for it.

        That was not hypothetical. Flow's picker on 2026-08-26 offers
        ``Nano Banana Pro`` / ``Nano Banana 2`` / ``Nano Banana 2 Lite``;
        ``Model.IMAGEN_3_5``'s ``has-text('Imagen 4')`` matches nothing, and
        ``has-text('Nano Banana 2')`` matches TWO entries.

        AMBIGUOUS is treated as a failure, not a ``.first`` guess: ``.first``
        resolves by DOM order, so an ambiguous selector silently picks whichever
        Flow renders first and changes behaviour with no code change on our side.

        Raises before anything is submitted, so a failure costs nothing.
        """
        option_sels = IMAGE_MODEL_OPTION_SELECTORS.get(model)
        if not option_sels:
            raise UiSelectorDriftError(
                selector_drift_detail(
                    "image_model",
                    f"no picker selector registered for model {model.value!r}.",
                    None,
                )
            )

        trigger = page.locator(IMAGE_MODEL_PICKER_TRIGGER).first
        await trigger.wait_for(state="visible", timeout=4000)
        await trigger.click()
        await page.wait_for_timeout(500)

        offered = await offered_menu_labels(page)
        for sel in option_sels:
            try:
                matches, first_visible = await count_visible(page, sel)
            except Exception as exc:  # noqa: BLE001
                log.debug("ui_automation.image_model_selector_miss", sel=sel, error=str(exc)[:120])
                continue
            if matches == 0:
                continue
            if matches > 1:
                await close_menu(page)
                raise UiSelectorDriftError(
                    selector_drift_detail(
                        "image_model",
                        (
                            f"selector for {model.value!r} is AMBIGUOUS — {matches} entries "
                            f"match {sel!r}. Selecting .first would pick by DOM order and "
                            f"could bill a different model than requested. "
                            f"Flow offered: {offered}."
                        ),
                        None,
                    )
                )
            assert first_visible is not None  # matches > 0 implies one was found
            await first_visible.click()
            await page.wait_for_timeout(500)
            log.info("ui_automation.image_model_selected", model=model.value, via=sel)
            return

        await close_menu(page)
        raise UiSelectorDriftError(
            selector_drift_detail(
                "image_model",
                (
                    f"model {model.value!r} is not selectable — no picker entry matched. "
                    f"Flow offered: {offered}. Refusing to generate on a different model "
                    f"than requested."
                ),
                None,
            )
        )

    @staticmethod
    async def _configure_generation_settings(
        page: Page,
        aspect_cli: str | None,
        count: int | None,
        *,
        model: Model | None = None,
        out_dir: Path | None = None,
        prompt_idx: int | None = None,
    ) -> None:
        """Open the per-generation settings panel and apply model, aspect ratio,
        and count.

        When ``out_dir`` and ``prompt_idx`` are both provided, diagnostic
        screenshots are saved as ``count_before_prompt_{idx}.png`` and
        ``count_after_prompt_{idx}.png`` so future count-drift can be
        diagnosed without re-instrumenting the code.

        Skips gracefully if the panel trigger cannot be found (non-fatal —
        generation will proceed with Flow's current default settings).
        """
        if aspect_cli is None and count is None and model is None:
            # Nothing to apply.
            return

        # Phase 3 — before screenshot (diagnostic, best-effort).
        await UiAutomationTransport._capture_diag_screenshot(
            page,
            out_dir,
            prompt_idx,
            "before",
        )

        if not await UiAutomationTransport._open_gen_settings_panel(page):
            # A requested MODEL cannot be honoured without this panel, and the
            # submit will inherit whatever the project's picker holds — the
            # silent wrong-model path (#586).
            #
            # This deliberately WARNS rather than raises. An earlier version
            # raised here; the existing batch tests showed it turning one
            # transient panel miss on prompt 0 into a whole-batch abort, because
            # the transport cannot distinguish "user passed --model X" from
            # "X is the default" — `request.model` is populated either way, so a
            # raise punishes callers who never asked for a specific model.
            #
            # Detection moved to where it is arm-agnostic and costs nothing:
            # `services.catalog_sync.parse_media_attribution` reads what the
            # SERVER says generated each media. That catches this on the agentic
            # arm too, where this code path never runs at all.
            if model is not None:
                log.warning(
                    "ui_automation.image_model_not_applied",
                    model=model.value,
                    reason="generation-settings panel could not be opened",
                    note="generation will use the project's current selection",
                )
            log.warning("ui_automation.gen_settings_panel_not_found", skipping=True)
            return

        if model is not None:
            await UiAutomationTransport._select_image_model(page, model)

        if aspect_cli:
            await UiAutomationTransport._apply_aspect_ratio(page, aspect_cli)

        if count is not None:
            await UiAutomationTransport._apply_count(page, count, out_dir, prompt_idx)

        # Phase 3 — after screenshot (diagnostic, best-effort).
        await UiAutomationTransport._capture_diag_screenshot(
            page,
            out_dir,
            prompt_idx,
            "after",
        )

        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)

    @staticmethod
    async def _capture_diag_screenshot(
        page: Page,
        out_dir: Path | None,
        prompt_idx: int | None,
        suffix: str,
    ) -> None:
        if out_dir is not None and prompt_idx is not None:
            # The helper creates the directory only when it captures (#1062).
            await _capture_debug_screenshot(
                page,
                out_dir / _DIAGNOSTICS_SUBDIR,
                f"count_{suffix}_prompt_{prompt_idx}.png",
            )

    @staticmethod
    async def _apply_aspect_ratio(page: Page, aspect_cli: str) -> None:
        candidates = _ASPECT_TAB_CANDIDATES.get(aspect_cli, (aspect_cli,))
        clicked = False
        last_err: str | None = None
        for tab_text in candidates:
            # `:text-is(...)` is exact-match — preferred for short labels
            # like "1:1" because `:has-text(...)` substring-matches and
            # would clash with longer tabs that include the label.
            try:
                tab = page.locator(f'[role="tab"]:text-is("{tab_text}")').first
                await tab.wait_for(state="visible", timeout=2_000)
                await tab.click()
                clicked = True
                log.info(
                    "ui_automation.aspect_ratio_set",
                    value=aspect_cli,
                    matched_label=tab_text,
                )
                break
            except Exception as e:
                last_err = str(e)
                continue
        if not clicked:
            log.warning(
                "ui_automation.aspect_ratio_set_failed",
                value=aspect_cli,
                candidates_tried=list(candidates),
                error=last_err,
            )

    @staticmethod
    async def _apply_count(
        page: Page,
        count: int,
        out_dir: Path | None,
        prompt_idx: int | None,
    ) -> None:
        if count not in _SUPPORTED_COUNTS:
            log.warning("ui_automation.unsupported_count", value=count)
        else:
            await UiAutomationTransport._set_count(
                page,
                count,
                out_dir=out_dir,
                prompt_idx=prompt_idx,
            )

    @staticmethod
    async def _set_count(
        page: Page,
        count: int,
        *,
        out_dir: Path | None = None,
        prompt_idx: int | None = None,
    ) -> None:
        """Click the count tab by its DIGIT — locale-invariant, read-back verify with retry.

        Algorithm (#404 rewrite of the #24 positional pick):
        1. Ensure the settings panel is open without toggling it closed
           (stay-mounted batch: panel may already be open from the prior prompt).
        2. Read the currently-displayed count via :data:`_COUNT_TAB_TEXT_RE` —
           immune to Mode/Aspect tabs, matches both label cohorts.
        3. If it already matches ``count``, return early (no click needed).
        4. Click the tab selected by :func:`_count_tab_locator_for` — keyed on
           the digit in the label (``1x``/``x1`` for count=1), NOT position:
           Flow's label rename shrank the old filtered set and shifted every
           positional pick by one (issue #404).
        5. Read back the digit and confirm the change.
        6. Retry up to 3 attempts total; raise :class:`UiSelectorDriftError`
           (exit 23) on non-convergence — a bare ``RuntimeError`` would be
           message-hashed by observability into an opaque ``UnexpectedError``.

        When read-back returns ``None`` (no selected count tab recognised),
        the digit-keyed click is trusted — it is deterministic regardless.

        Four structlog events are emitted for diagnosability:
        - ``ui_automation.count_setter_entered``
        - ``ui_automation.count_click_attempted``
        - ``ui_automation.count_click_result``
        - ``ui_automation.count_setter_completed``
        """
        panel_open = await UiAutomationTransport._is_settings_panel_open(page)
        initial_displayed = await UiAutomationTransport._read_displayed_count(page)

        log.info(
            "ui_automation.count_setter_entered",
            desired_count=count,
            panel_currently_visible=panel_open,
            initial_displayed_count=initial_displayed,
        )

        # Ensure the panel is open — open it only if it's currently closed
        # (avoid toggling a stay-mounted open panel closed).
        if not panel_open:
            opened = await UiAutomationTransport._open_gen_settings_panel(page)
            if not opened:
                log.warning(
                    "ui_automation.count_setter_panel_open_failed",
                    desired_count=count,
                )
                # Non-fatal: completed event records failure.
                log.info(
                    _EVT_COUNT_SETTER_COMPLETED,
                    desired_count=count,
                    final_displayed_count=initial_displayed,
                    success=False,
                    attempts=0,
                )
                return

        _max_attempts = 3
        # Reuse the initial read — avoids a redundant DOM round-trip.
        displayed: int | None = initial_displayed

        for attempt in range(1, _max_attempts + 1):
            # If current display already matches desired, we're done.
            if displayed == count:
                log.info(
                    _EVT_COUNT_SETTER_COMPLETED,
                    desired_count=count,
                    final_displayed_count=displayed,
                    success=True,
                    attempts=attempt - 1,
                )
                return

            panel_visible_before = await UiAutomationTransport._is_settings_panel_open(page)
            clicked = False
            click_error: str | None = None

            # Digit-keyed pick: the tab whose label carries the desired digit
            # ("1x"/"x1" for count=1). Position-independent — survives the
            # label-cohort rename and filtered-set drift (issue #404).
            target_tab = _count_tab_locator_for(page, count).first
            selector_desc = f"count-tab label ~ /^({count}x|x{count})$/"
            log.info(
                "ui_automation.count_click_attempted",
                target=f"count={count}",
                selector=selector_desc,
                panel_visible=panel_visible_before,
                current_displayed_count=displayed,
            )
            try:
                await target_tab.wait_for(state="visible", timeout=3_000)
                await target_tab.click()
                await page.wait_for_timeout(300)
                clicked = True
            except Exception as e:
                click_error = str(e)

            # Read back to verify (digit-extraction, locale-agnostic).
            displayed = await UiAutomationTransport._read_displayed_count(page)
            log.info(
                "ui_automation.count_click_result",
                target=f"count={count}",
                success=clicked,
                effect_observed=displayed == count,
                current_displayed_count_after=displayed,
                error=click_error,
            )

            # Success when the click landed AND read-back digit matches, OR
            # when read-back returned None (no selected count tab recognised —
            # the digit-keyed click targeted the right tab, so trust it).
            if clicked and (displayed is None or displayed == count):
                log.info(
                    _EVT_COUNT_SETTER_COMPLETED,
                    desired_count=count,
                    final_displayed_count=displayed,
                    success=True,
                    attempts=attempt,
                    readback_trusted=(displayed is None),
                )
                return

            # Brief pause before retry to allow React re-render.
            if attempt < _max_attempts:
                await page.wait_for_timeout(500)

        # All attempts exhausted without convergence.
        log.info(
            _EVT_COUNT_SETTER_COMPLETED,
            desired_count=count,
            final_displayed_count=displayed,
            success=False,
            attempts=_max_attempts,
        )
        shot = await _capture_debug_screenshot(page, out_dir, "debug_count_setter_drift.png")
        raise UiSelectorDriftError(
            selector_drift_detail(
                "count_tab",
                f"count setter failed to converge: desired={count}, "
                f"displayed={displayed} after {_max_attempts} attempts. "
                f"Flow's count control may have changed again "
                f"(labels are matched as {count}x/x{count}).",
                shot,
            )
        )
