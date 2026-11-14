"""Settings pane interaction, models, aspects, and durations.

A part of `MigratedComposer`, moved byte-identical from
`migrated_composer.py` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.transports._mc_common import (
    ASPECT_LIGATURE,
    FRAMES_LIGATURE,
    IMAGE_ASPECT_LIGATURE,
    IMAGE_MODEL_MENU_MATCHERS,
    INGREDIENTS_LIGATURE,
    MENU_ITEM,
    OVERLAY,
    PANE_CLOSE_ESCAPES,
    R2V_DURATION_S,
    RADIO,
    RADIOGROUP,
    READY_ANCHOR,
    VIDEO_MODEL_MENU_LABELS,
    VIDEO_MODEL_MENU_MATCHERS,
    VISIBLE_OVERLAY,
    _duration,
    _exact,
    _ligature,
    _select_resolution,
    _visible_model_picker,
)
from gflow_cli.api.transports._mc_editor_gate import EditorGateMixin
from gflow_cli.api.video import I2V_DEFAULT_MODEL, Mode, VideoModel
from gflow_cli.errors import ConfigurationError, UiSelectorDriftError

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest

log = structlog.get_logger(__name__)


class SettingsPaneMixin(EditorGateMixin):
    """Settings pane interaction, models, aspects, and durations."""

    # --- settings ---------------------------------------------------------------

    async def apply_video_settings(self, page: Page, request: GenerateVideoRequest) -> None:
        """Mode, model, aspect, duration, count — through the radios, with read-back.

        Model goes first: like on labs.google the duration row is model-state.
        """
        pane = await self._open_pane(page)
        try:
            await self._select(page, pane, axis="mode", lig="videocam")
            if request.mode is Mode.I2V:
                # Frames renders the Start/End chips the attach stage binds to. Flow
                # remembers the last submode per account, so it is set, not assumed.
                await self._select(page, pane, axis="submode", lig=FRAMES_LIGATURE)
            if request.mode is Mode.R2V or request.reference_entities:
                # Ingredients is where references live, and the app derives the r2v model
                # key from this plus the picker choice — the same run sends
                # veo_3_1_r2v_lite_low_priority here and veo_3_1_lite_low_priority under
                # Frames — so nothing maps that by hand.
                #
                # A CHARACTER reference needs it too, whatever mode the caller asked for
                # (#723). Flow treats an attached character as reference-to-video: the
                # 2026-09-07 payload capture, taken in Ingredients, submitted
                # `abra_r2v_8s`. A t2v request that attached a character chip while the
                # composer sat under Frames clicked submit and got no reply at all —
                # measured, not inferred. So the submode follows the REFERENCE, not the
                # mode name.
                await self._select(page, pane, axis="submode", lig=INGREDIENTS_LIGATURE)
            model = request.model
            if model is None and request.mode is Mode.I2V:
                # #125: a queued MCP payload carries model=None, and without this bind
                # the editor submits on whatever tier it last remembered — possibly a
                # 100-credit one for a run the caller expected to cost 10.
                model = I2V_DEFAULT_MODEL
                log.info("migrated.i2v_model_defaulted", model=model.value, issue_ref="#125")
            if model is not None:
                await self._select_model(page, pane, model)
            await self._select(page, pane, axis="aspect", lig=ASPECT_LIGATURE[request.aspect])
            if request.duration is not None:
                if (
                    request.mode is Mode.R2V
                    and model not in (VideoModel.OMNI_FLASH, VideoModel.OMNI_1_2_FLASH)
                    and request.duration != R2V_DURATION_S
                ):
                    raise ConfigurationError(
                        detail=(
                            "the SDK restricts Veo/unspecified-model "
                            "reference-to-video to "
                            f"{R2V_DURATION_S}s; at {request.duration}s it does not refuse but "
                            "silently drops the references and submits a text-to-video run "
                            "with their file names typed into the prompt — a full-price clip "
                            "with none of the images on it"
                        ),
                        remediation_hint=(
                            f"Drop --duration (r2v pins {R2V_DURATION_S}s on its own), pass "
                            f"--duration {R2V_DURATION_S}, or run this length on labs with "
                            "GFLOW_CLI_FLOW_HOST=labs.google."
                        ),
                    )
                if model is VideoModel.OMNI_1_2_FLASH:
                    await self._select_omni12_duration(page, pane, request.duration)
                else:
                    await self._select(page, pane, axis="duration", text=f"{request.duration}s")
            elif request.mode is Mode.R2V:
                if model is VideoModel.OMNI_1_2_FLASH:
                    await self._select_omni12_duration(page, pane, R2V_DURATION_S)
                else:
                    await self._pin_r2v_duration(page, pane)
            if request.resolution is not None:
                await _select_resolution(page, pane, request.resolution)
            await self._select(page, pane, axis="count", text=f"x{request.count}")
            log.info(
                "migrated.settings_applied",
                aspect=request.aspect.value,
                duration=request.duration,
                resolution=request.resolution,
                count=request.count,
                # The EFFECTIVE model — `request.model` is None on an i2v run that
                # took the #125 default, and logging that read as "no model bound".
                model=model.value if model else None,
            )
        except BaseException:
            # A close failure must never replace the error that is already travelling.
            # `_close_pane` in a bare `finally` did exactly that: a `--model` Flow does
            # not offer raises ConfigurationError (exit 11) naming the offered models,
            # and a stuck pane then overwrote it with UiSelectorDriftError (exit 23),
            # losing the list the user needed. On this path the pane is best-effort.
            await self._close_pane(page, strict=False)
            raise
        await self._close_pane(page, strict=True)

    async def apply_image_settings(self, page: Page, request: GenerateImageRequest) -> None:
        """Bind Image mode, model, aspect and count with read-back before submission."""
        pane = await self._open_pane(page)
        try:
            await self._select(page, pane, axis="mode", lig="image")
            await self._select_image_model(page, pane, request.model)
            await self._select(
                page,
                pane,
                axis="aspect",
                lig=IMAGE_ASPECT_LIGATURE[request.aspect],
            )
            await self._select(page, pane, axis="count", text=f"x{request.count}")
            log.info(
                "migrated.image_settings_applied",
                model=request.model.value,
                aspect=request.aspect.value,
                count=request.count,
            )
        except BaseException:
            await self._close_pane(page, strict=False)
            raise
        await self._close_pane(page, strict=True)

    async def _open_pane(self, page: Page) -> Any:

        await self._dismiss_cookie_bar(page)
        # And again here, not only in `ensure_editor` (#859). That probe runs one frame
        # after `domcontentloaded`, before Angular has rendered anything, so the promo
        # modal it is looking for does not exist yet and `is_visible()` returns False --
        # which is why a run blocked by one logs NEITHER `migrated.dialog_dismissed` nor
        # `migrated.dialog_not_dismissed`. By the time the first real click is made the
        # dialog IS up, and its CDK backdrop is what the post-mortem named.
        await self._dismiss_dialog(page)
        trigger = page.locator(READY_ANCHOR).first
        try:
            # Visibility, not `count()`. Agent mode leaves the trigger in the DOM under a
            # bare `hidden` (#749), and the mode can flip between `ensure_editor` and
            # here — a count-guard then walks into a click that expires as a bare
            # Playwright TimeoutError, with no exit code and no mention of the mode.
            await trigger.wait_for(state="visible", timeout=5000)
        except Exception as e:
            why = (
                " — the account is in Flow's agent mode, which hides it; turn the Agent "
                "chip off in a browser and re-run"
                if await self._agent_chip_pressed(page)
                else ""
            )
            detail = await self._ui_failure_detail(
                page,
                phase="wait_settings_trigger",
                selector=READY_ANCHOR,
                detail=(
                    f"migrated host: the settings trigger ({READY_ANCHOR}) is not visible"
                    f"{why} (host=migrated)"
                ),
                error=e,
            )
            raise UiSelectorDriftError(detail=detail) from e
        # The other half of #752 finding #7: the guard above became a visibility wait,
        # this click stayed bare, and the comment above describes what it went on doing.
        await self._click(page, trigger, named=READY_ANCHOR, timeout=5000)
        # THE overlay that holds the option groups — not `.last`: once the model
        # menu (a second overlay) has opened and closed, a detached menu pane can
        # still be the last one in the DOM, and every axis after `--model` then
        # reads "0 option groups" (measured 2026-09-05, $0 run).
        pane = page.locator(OVERLAY).filter(has=page.locator(RADIOGROUP)).last
        try:
            await pane.locator(RADIOGROUP).first.wait_for(state="visible", timeout=8000)
        except Exception as e:
            detail = await self._ui_failure_detail(
                page,
                phase="discover_settings_pane",
                selector=f"{OVERLAY} -> {RADIOGROUP}",
                detail=(
                    "migrated host: the settings pane opened but rendered no option "
                    "groups ([role='radiogroup']) (host=migrated)"
                ),
                error=e,
            )
            raise UiSelectorDriftError(detail=detail) from e
        return pane

    def _blocking_overlays(self, page: Page) -> Any:
        """Visible overlays that are ours to dismiss — the settings pane and the model
        menu, identified by what they contain.

        Scoped rather than every ``.cdk-overlay-pane``: those classes are generic CDK
        and Flow mounts snackbars, tooltips and dialogs in the same container, none of
        which cover the composer or answer to Escape. An unrelated toast visible at the
        wrong moment would otherwise burn both escapes and abort a run that was fine.
        """
        return page.locator(VISIBLE_OVERLAY).filter(has=page.locator(f"{RADIOGROUP}, {MENU_ITEM}"))

    async def _close_pane(self, page: Page, *, strict: bool = True) -> None:
        """Dismiss every visible settings/menu overlay, and verify that none is left.

        ``strict=False`` downgrades a stuck pane to a warning. It is passed on the path
        where an exception is already in flight, so a close failure cannot mask the
        error the caller actually needs — see :meth:`apply_video_settings`.

        One Escape is not enough after ``--model``. Selecting from the menu leaves
        Angular with **two** stacked overlays — the settings pane and the menu opened
        over it — and each Escape dismisses exactly one, so a single press closed only
        the menu and left the settings pane covering the composer. ``send_prompt``'s
        click then failed Playwright's actionability check and surfaced ~5 s later as a
        bare ``TimeoutError`` naming ``[contenteditable='true']``, with nothing pointing
        at the pane. Field-reported as "switch the model and the run dies; re-run with
        the same model and it works" — a re-run binds the model at the button read-back,
        never opens the menu, and so never stacks the second overlay.

        Measured 2026-09-05 at $0 (spike
        ``2026-09-05-migrated-model-menu-lower-priority.md``): after a switch the
        composer's bounding box is **identical** for 12 s — it was never unstable — while
        ``.cdk-overlay-pane:visible`` stays at 1; one more Escape takes it to 0 and the
        prompt types.

        The old read-back *did* notice — ``migrated.pane_still_open`` fires on the
        pre-fix source, confirmed by replaying it against the live host. It was a
        warning: the run continued, the composer click timed out 5 s later, and the
        error it raised named ``[contenteditable='true']`` with no reference to the
        warning that had predicted it. The observation was there and only the
        consequence was missing, so it is now the failure itself.

        A pane that will not close is raised here, pre-submit and at $0, rather than left
        for the composer click to report as an unattributable timeout.
        """
        for _ in range(PANE_CLOSE_ESCAPES):
            # Re-queried every pass rather than held: the count has to be re-read after
            # each Escape, and a locator built once is only re-evaluated because
            # Playwright's are lazy — not a property worth depending on here.
            if not await self._blocking_overlays(page).count():
                return
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
        remaining = await self._blocking_overlays(page).count()
        if not remaining:
            return
        log.warning("migrated.pane_still_open", visible_overlays=remaining, strict=strict)
        if not strict:
            return
        detail = await self._ui_failure_detail(
            page,
            phase="close_settings_pane",
            selector=VISIBLE_OVERLAY,
            detail=(
                f"migrated host: {remaining} overlay(s) still visible after "
                f"{PANE_CLOSE_ESCAPES} Escape presses — the settings pane would cover the "
                f"composer and the prompt could not be typed (host=migrated)"
            ),
            error=f"{remaining} visible overlay(s)",
        )
        raise UiSelectorDriftError(detail=detail)

    async def _select_omni12_duration(self, page: Page, pane: Any, seconds: int) -> None:
        """Measured Omni 1.2 slider: index 0..12 maps to 3..15 seconds.

        Free accounts expose 0..7 (3..10 seconds). Never clamp a subscriber-only
        duration or reinterpret a changed slider: validate the structure and read
        both its index and displayed seconds back before leaving settings.
        """
        component = pane.locator("flow-duration-slider")
        slider = component.locator('input[type="range"]')
        try:
            await page.locator(MENU_ITEM).first.wait_for(state="hidden", timeout=4000)
            await slider.first.wait_for(state="visible", timeout=4000)
        except Exception as exc:
            raise UiSelectorDriftError(
                detail="Omni 1.2 duration slider missing (model menu open or slider unmounted)"
            ) from exc
        if await slider.count() != 1:
            raise UiSelectorDriftError(detail="Omni 1.2 duration slider ambiguous")
        minimum = await slider.get_attribute("min")
        maximum = await slider.get_attribute("max")
        step = await slider.get_attribute("step")
        if minimum != "0" or maximum not in ("7", "12") or step != "1":
            raise UiSelectorDriftError(detail="Omni 1.2 duration slider range changed")
        index = seconds - 3
        if not 0 <= index <= int(maximum) or not await slider.is_enabled():
            raise ConfigurationError(
                detail=f"Omni 1.2 duration {seconds}s is not offered by this account",
                remediation_hint="Choose an offered duration; 11–15s requires a Google AI plan.",
            )
        await slider.press("Home")
        for _ in range(index):
            await slider.press("ArrowRight")
        await asyncio.sleep(0.2)
        label = await component.locator(".duration-slider-value").inner_text()
        actual_index = await slider.input_value()
        if actual_index != str(index) or not _duration(f"{seconds}s").search(label):
            raise UiSelectorDriftError(
                detail=(
                    f"Omni 1.2 duration slider did not bind {seconds}s: "
                    f"index={actual_index}, label={label!r}"
                )
            )

    async def _pin_r2v_duration(self, page: Page, pane: Any) -> None:
        """Bind the base duration for a references run, when this pane offers durations.

        The editor REMEMBERS the last duration, so an r2v run that passes none inherits
        whatever the previous run left behind — and below :data:`R2V_DURATION_S` the app
        degrades an ingredients run to text-to-video instead of refusing it. That is the
        same shape as #125 one axis over: "no opinion" is not what the caller gets, the
        editor's memory is.

        Best-effort by construction. A cohort that renders no duration row already submits
        the base tier, so a missing row is the correct state, not an error — which is why
        this cannot go through :meth:`_select`, whose duration branch raises exit 11.
        """
        wanted = f"{R2V_DURATION_S}s"
        if not await pane.locator(RADIO).filter(has_text=_duration(wanted)).count():
            log.info("migrated.r2v_duration_row_absent", wanted=wanted)
            return
        await self._select(page, pane, axis="duration", text=wanted)
        log.info("migrated.r2v_duration_pinned", seconds=R2V_DURATION_S, issue_ref="#639")

    async def _select(
        self,
        page: Page,
        pane: Any,
        *,
        axis: str,
        lig: str | None = None,
        text: str | None = None,
    ) -> None:
        """Click one radio and read ``aria-checked`` back; re-query once on a stale node."""
        radios = pane.locator(RADIO)
        wanted = text if text is not None else str(lig)
        matches = (
            radios.filter(has=_ligature(page, lig))
            if lig
            else radios.filter(has_text=_duration(wanted) if axis == "duration" else _exact(wanted))
        )
        target = matches.first
        if not await target.count():
            groups = await pane.locator(RADIOGROUP).count()
            # Only the duration row is a per-account/model capability (#650) — a missing
            # mode/aspect/count radio, or an empty pane, is the DOM having changed.
            if axis != "duration" or groups == 0:
                raise UiSelectorDriftError(
                    detail=(
                        f"migrated host: no '{axis}' radio offering {wanted!r} in the settings "
                        f"pane ({groups} option groups rendered) (host=migrated)"
                    ),
                )
            raise ConfigurationError(
                detail=(
                    f"the migrated Flow host renders no duration control offering {wanted!r} "
                    f"for this account and model ({groups} option groups shown)"
                ),
                remediation_hint=(
                    "Drop --duration to accept Flow's default length, or pick a model whose "
                    "settings pane shows a duration row (on the maintainer cohort only "
                    "Omni 1.1 Flash does)."
                ),
            )
        if await target.get_attribute("aria-checked") == "true":
            return
        await target.click(timeout=4000)
        await asyncio.sleep(0.2)
        if await matches.first.get_attribute("aria-checked") == "true":
            return
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: the '{axis}' radio {wanted!r} did not become aria-checked "
                f"after the click (host=migrated)"
            ),
        )

    async def _select_model(self, page: Page, pane: Any, model: VideoModel) -> None:
        matcher = VIDEO_MODEL_MENU_MATCHERS.get(model)
        if matcher is None:
            raise ConfigurationError(
                detail=(
                    f"gflow has no flow.google.com selector for model '{model.value}' — "
                    f"this is a gap in gflow's table, not a reading of Flow's menu. "
                    f"Selectors exist for: {', '.join(VIDEO_MODEL_MENU_LABELS.values())}"
                ),
                remediation_hint="Pass --model with one of the offered names, or omit it.",
            )
        button = await _visible_model_picker(page, pane, what="model picker")
        current = (await button.text_content() or "").strip()
        if matcher.matches(current):
            # Logged, because otherwise this path is invisible: a run that short-circuits
            # here emits no model event at all, and a field timeline cannot tell "bound
            # the tier you asked for" from "never touched the picker". The 2026-09-05
            # live run needed a separate $0 probe to answer exactly that.
            log.info("migrated.model_already_selected", model=current, requested=model.value)
            return
        await button.click(timeout=4000)
        items = page.locator(MENU_ITEM)
        try:
            await items.first.wait_for(state="visible", timeout=5000)
        except Exception as e:
            raise UiSelectorDriftError(
                detail="migrated host: model menu ([role='menuitem']) did not open (host=migrated)",
            ) from e
        # Matched in Python rather than through a `has_text` filter: the menu is read
        # back for the refusal diagnostic anyway, an *exclusion* is not expressible as
        # `has_text`, and more than one hit has to REFUSE instead of resolving `.first`
        # (#539 — the labs A/B that proved a `.first` on an ambiguous selector picks a
        # tier the user never asked for, and Flow bills for it).
        offered = [t.strip() for t in await items.all_text_contents()]
        hits = [i for i, text in enumerate(offered) if matcher.matches(text)]
        if not hits:
            await page.keyboard.press("Escape")
            raise ConfigurationError(
                detail=(
                    f"model '{model.value}' is not offered on this account's migrated Flow "
                    f"host; offered: {', '.join(offered)}"
                ),
                remediation_hint="Pass --model with one of the offered names, or omit it.",
            )
        if len(hits) > 1:
            await page.keyboard.press("Escape")
            raise ConfigurationError(
                detail=(
                    f"model '{model.value}' matched {len(hits)} entries in the migrated model "
                    f"menu ({', '.join(offered[i] for i in hits)}) — refusing rather than "
                    f"guessing which tier Flow would bill"
                ),
                remediation_hint=(
                    "Pass a --model that names one entry, or omit it to accept Flow's default."
                ),
            )
        await items.nth(hits[0]).click(timeout=4000)
        log.info("migrated.model_selected", model=offered[hits[0]], requested=model.value)

    async def _select_image_model(self, page: Page, pane: Any, model: ImageModel) -> None:
        matcher = IMAGE_MODEL_MENU_MATCHERS.get(model)
        if matcher is None:
            raise ConfigurationError(
                detail=(
                    f"gflow has no flow.google.com selector for image model "
                    f"'{model.value}' — a gap in gflow's table, not a reading of Flow's menu"
                ),
                remediation_hint=(
                    "Use nano2 or nano-pro. GFLOW_CLI_FLOW_HOST=labs.google only "
                    "helps if Flow still serves you labs."
                ),
            )
        button = await _visible_model_picker(page, pane, what="image model picker")
        current = (await button.text_content() or "").strip()
        if matcher.matches(current):
            log.info("migrated.image_model_already_selected", model=current, requested=model.value)
            return
        await button.click(timeout=4000)
        items = page.locator(MENU_ITEM)
        await items.first.wait_for(state="visible", timeout=5000)
        offered = [text.strip() for text in await items.all_text_contents()]
        hits = [index for index, text in enumerate(offered) if matcher.matches(text)]
        if len(hits) != 1:
            await page.keyboard.press("Escape")
            raise ConfigurationError(
                detail=(
                    f"image model '{model.value}' matched {len(hits)} entries on the migrated "
                    f"host; offered: {', '.join(offered)}"
                ),
                remediation_hint="Choose one offered image model or omit --model.",
            )
        await items.nth(hits[0]).click(timeout=4000)
        log.info("migrated.image_model_selected", model=offered[hits[0]], requested=model.value)
