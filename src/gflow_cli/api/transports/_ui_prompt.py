"""Image mode switching, prompt formatting and prompt box interactions.

Mixed into `UiAutomationTransport` via `PromptInputMixin` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog

from gflow_cli.api.transports._common import raise_if_known_landing
from gflow_cli.api.transports._ui_common import (
    _BODY_SLOT_MOUNT_POLL_MS,
    _BODY_SLOT_MOUNT_TIMEOUT_S,
    _BODY_TRIPTYCH_PREAMBLE,
    _FORMAT_REWRITE_MIN_DELTA,
    _FORMAT_REWRITE_POLL_MS,
    _FORMAT_REWRITE_TIMEOUT_S,
    IMAGE_TAB_IN_MENU_SELECTORS,
    MODE_SWITCH_TRIGGER_SELECTORS,
    PROMPT_FORMAT_SELECTORS,
    PROMPT_INPUT_SELECTORS,
    SUBMIT_BUTTON_SELECTORS,
    _capture_debug_screenshot,
    _jitter_ms,
    _prompt_hash_stable,
)
from gflow_cli.api.transports._ui_navigation import PageNavigationMixin
from gflow_cli.api.transports.ui_automation_video import (
    VideoGenerationMixin,
    screenshot_clause,
    selector_drift_detail,
)
from gflow_cli.errors import UiSelectorDriftError

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Page


log = structlog.get_logger(__name__)


class PromptInputMixin(PageNavigationMixin):
    """Prompt box targeting, submission and prompt formatting."""

    # Selector for the character editor's Slate prompt textbox — used as the
    # "editor mounted" readiness anchor.  Its first alternative IS
    # PROMPT_INPUT_SELECTORS[0];
    # duplicated here as a named constant so the character-editor path is
    # self-documenting without importing the tuple.
    # Two frontends, one feature. labs.google renders the character editor with
    # React + Slate; flow.google.com renders the SAME editor — same tRPC backend,
    # same aisandbox REST, same entities — with Angular + ProseMirror (recon
    # 2026-09-06, scripts/dev/spike_migrated_character_editor_anchor.py). Asking
    # only for Slate made a present feature look absent for 20 s and then get
    # reported as "labs-only, ever", which was wrong. Both anchors are library
    # build artefacts rather than display strings, so neither translates.
    _CHARACTER_EDITOR_READY_SELECTOR = (
        'div[role="textbox"][data-slate-editor="true"], div.ProseMirror[contenteditable="true"]'
    )

    @staticmethod
    async def _switch_to_image_mode(page: Page, *, out_dir: Path | None = None) -> None:
        """Open the 2-step mode dropdown and switch to Image mode.

        Mirror of :meth:`VideoGenerationMixin._switch_to_video_mode`.  Without
        this, an account whose last-used Flow mode was Video silently routes
        ``image t2i`` / ``image batch`` prompts to the video endpoint — no
        ``batchGenerateImages`` response is observed, and the listener times
        out after 3 minutes (an image typically completes in ~15 s).

        The dropdown is closed afterwards (via :kbd:`Escape`) so the caller's
        :meth:`_configure_generation_settings` can open it fresh.
        """
        # New Flow UI: if the composer is in Agent mode the generation panel is
        # absent — switch back to media mode first so the trigger probe below
        # can find the crop_* dropdown.
        await VideoGenerationMixin._exit_agent_mode(page, out_dir=out_dir)
        trigger = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "mode_switch_trigger",
            MODE_SWITCH_TRIGGER_SELECTORS,
        )
        if trigger is None:
            # Reached on the labs arm when --project was supplied: `_enter_editor` returns
            # early there with no readiness gate, so the guard in its gallery arm never
            # ran. Consulted here, where the caller is already about to raise, and BEFORE
            # `_mode_switch_error` classifies -- that helper would otherwise answer
            # "migrated host" or "selector drift" for an account that simply has no Flow.
            await raise_if_known_landing(
                page, requested="the Flow editor", at="labs.switch_to_image_mode"
            )
            raise await VideoGenerationMixin._mode_switch_error(page, out_dir, media="image")
        await trigger.click()
        await page.wait_for_timeout(_jitter_ms(800))
        image_tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "image_mode_tab",
            IMAGE_TAB_IN_MENU_SELECTORS,
        )
        if image_tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_image_tab.png")
            raise UiSelectorDriftError(
                selector_drift_detail(
                    "image_mode_tab",
                    "Image tab not found in the mode dropdown.",
                    shot,
                )
            )
        await image_tab.click()
        await page.wait_for_timeout(_jitter_ms(1200))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(_jitter_ms(200))
        log.info("ui_automation.image_mode_entered")

    async def _locate_prompt_box(
        self,
        page: Page,
        out_dir: Path | None = None,
    ) -> Any:
        """Locate the visible Slate prompt box via :data:`PROMPT_INPUT_SELECTORS`.

        Returns the first visible locator (``.first`` of the matching
        selector). On no-match, writes a debug screenshot to ``out_dir``
        (if provided) and raises ``RuntimeError``.
        """
        for selector in PROMPT_INPUT_SELECTORS:
            try:
                loc = page.locator(selector).first
                await loc.wait_for(state="visible", timeout=10_000)
                log.info("ui_automation.prompt_input_found", selector=selector)
                return loc
            except Exception:
                continue

        # The third site, and the worst of the three. `_enter_editor(project_id=...)`
        # has no readiness gate of its own — it navigates, settles, checks overlays and
        # returns — so a landing page passes all of it and the FIRST thing to fail is
        # this sweep. It raises a bare RuntimeError, which `observability.py` SHA-256
        # hashes because it is not a GFlowError, so the operator is shown "Unexpected
        # error" with even the URL destroyed. Worse than the drift report #756 is about.
        await raise_if_known_landing(page, requested="the Flow editor", at="labs.locate_prompt_box")

        shot_path = await _capture_debug_screenshot(page, out_dir, "debug_prompt_not_found.png")
        msg = f"Prompt input not found in Flow UI. URL: {page.url}.{screenshot_clause(shot_path)}"
        raise RuntimeError(msg)

    async def _click_submit(self, page: Page) -> None:
        """Submit the active prompt via the ``arrow_forward`` button.

        Tries :data:`SUBMIT_BUTTON_SELECTORS` in priority order; falls back
        to pressing Enter if no submit button is visible.
        """
        for sel in SUBMIT_BUTTON_SELECTORS:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=2_000)
                await btn.click()
                log.info("ui_automation.prompt_submitted", via=sel)
                return
            except Exception:
                continue

        log.info("ui_automation.prompt_submitted", via="enter_key_fallback")
        await page.keyboard.press("Enter")

    async def format_character_prompt(
        self,
        page: Page,
        *,
        prompt_box: Any,
        typed_text: str,
    ) -> bool:
        """Click Flow's Format button and WAIT for the rewrite to actually land.

        Best-effort, like :meth:`_select_character_model`: formatting is a nicety
        on top of a prompt that already submits fine, so every failure logs and
        returns ``False`` rather than failing the generation. Callers submit
        regardless — which is why the return value must not lie.

        **Returning ``True`` means the composer's text changed, not that a button
        was pressed.** Flow rewrites *server-side*: measured 2026-09-07 on the
        migrated host, the click fires a ``batchexecute`` round trip and the
        reshaped text reaches the composer at **~5.4 s**, while this method used to
        wait ``_jitter_ms(500)`` and return. ``_send_prompt`` submits on the very
        next line, so ``--format-prompt`` shipped the prompt the user typed and
        discarded the rewrite — on every run, with ``prompt_formatted`` logged and
        exit 0. The defect was never the duration. It was reporting a success we
        had not observed: the absence of a completion inside a window we chose,
        recorded as a completion. See
        ``docs/superpowers/spikes/2026-09-07-format-click-is-not-a-format.md``.

        The gate is the DOM, not the wire. ``eAenfb``'s response arrives **4.2 s
        before** the text settles, so awaiting it would reproduce the same early
        submit — and an undocumented ``batchexecute`` rpcid is not an anchor this
        project is willing to depend on. Comparing the box against the string we
        inserted is locale-invariant by construction: it never reads a display label.

        A **length delta** rather than ``!=`` because Slate/ProseMirror re-render
        and normalise whitespace; bare inequality would fire on that churn and
        re-report the same false success with better telemetry.

        The enabled check is NOT redundant with the visible check. Flow ships this
        button ``disabled`` while the prompt box is empty (verified 2026-07-27), and
        a disabled button is still *visible* — so visibility alone would hand a
        disabled element to ``click()``, which auto-waits for actionability and
        stalls for the full timeout. A disabled match means THAT anchor resolved to
        the wrong element, so the cascade continues rather than aborting: the old
        ``return False`` here let one stale anchor kill every selector behind it.
        """
        button = await self._locate_format_button(page, prompt_box=prompt_box)
        if button is None:
            log.warning("ui_automation.format_button_not_found", selectors=PROMPT_FORMAT_SELECTORS)
            return False

        locator, selector = button
        try:
            # Explicit short timeout: never inherit Playwright's 30s default on
            # a best-effort nicety sitting in front of the submit.
            await locator.click(timeout=5000)
        except Exception as e:
            log.warning("ui_automation.format_click_failed", selector=selector, error=str(e))
            return False
        log.info("ui_automation.format_button_clicked", selector=selector)

        typed = typed_text.strip()
        deadline = time.monotonic() + _FORMAT_REWRITE_TIMEOUT_S
        started = time.monotonic()
        while time.monotonic() < deadline:
            await page.wait_for_timeout(_jitter_ms(_FORMAT_REWRITE_POLL_MS))
            try:
                current = (await prompt_box.inner_text()).strip()
            except Exception as e:
                log.debug("ui_automation.format_readback_failed", error=str(e))
                continue
            # GROWTH, not absolute delta. `abs()` accepted change in either direction, and
            # Flow CLEARS the box before repopulating it — so a poll landing in that window
            # read "" and `abs(0 - 19) >= 16` passed. `prompt_formatted` then logged
            # `prompt_len_after=0` and `_click_submit` ran on the next line, submitting an
            # EMPTY prompt on a path that spends image quota (#745). A success signal that
            # fires on the ABSENCE of the thing it measures is the defect #727 was about,
            # rebuilt inside its own fix.
            if len(current) < len(typed) + _FORMAT_REWRITE_MIN_DELTA or current == typed:
                continue
            # And confirm it settled. One read can land mid-write; the rewrite observed live
            # was a single discrete swap, but that was sampled at 250ms and a partial write
            # between samples was never ruled out. Two agreeing reads cost one interval.
            await page.wait_for_timeout(_jitter_ms(_FORMAT_REWRITE_POLL_MS))
            try:
                settled = (await prompt_box.inner_text()).strip()
            except Exception as e:
                log.debug("ui_automation.format_readback_failed", error=str(e))
                continue
            if settled != current:
                log.debug("ui_automation.format_still_settling", chars=len(settled))
                continue
            # Lengths and a stable hash only. Flow ELABORATES a terse description into a
            # detailed physical one, so the rewrite is more PII-dense than the input, and
            # structlog is not governed by GFLOW_CLI_HISTORY_PROMPTS — no operator control
            # would apply to it.
            log.info(
                "ui_automation.prompt_formatted",
                selector=selector,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                prompt_len_before=len(typed),
                prompt_len_after=len(current),
                prompt_hash=_prompt_hash_stable(current),
            )
            return True

        # Say what was observed, never what Flow "failed" to do: an unchanged box
        # also covers Flow declining, erroring, or judging the prompt already
        # formatted. None of those were provoked (2026-09-07), so none are claimed.
        log.warning(
            "ui_automation.format_not_observed",
            selector=selector,
            waited_s=_FORMAT_REWRITE_TIMEOUT_S,
            prompt_len=len(typed),
        )
        return False

    async def _locate_format_button(self, page: Page, *, prompt_box: Any) -> Any:
        """Resolve the Format button belonging to the ACTIVE composer.

        Returns ``(locator, selector)`` or ``None``.

        Box identity matters here. :meth:`_locate_body_prompt_box` documents that
        on a two-box cohort "the LAST mounted box is the target" — the body
        composer, never the portrait's. A page-global ``.first`` on the format
        button therefore resolves to the PORTRAIT composer's button while the body
        prompt is the one that was just typed into, reshaping the wrong prompt (or
        finding a disabled button and giving up). This mirrors that existing
        convention rather than inventing a second one: when more than one prompt
        box is mounted, take the last matching button.
        """
        try:
            boxes = await page.locator(self._CHARACTER_EDITOR_READY_SELECTOR).count()
        except Exception:
            boxes = 1
        for selector in PROMPT_FORMAT_SELECTORS:
            try:
                matches = page.locator(selector)
                locator = matches.last if boxes > 1 else matches.first
                # No timeout argument: Playwright documents it as ignored here —
                # `is_visible()` never waits, it answers from the current DOM, so a
                # non-matching entry costs one round trip rather than a second.
                if not await locator.is_visible():
                    continue
                if not await locator.is_enabled():
                    log.debug("ui_automation.format_button_disabled", selector=selector)
                    continue
                return (locator, selector)
            except Exception as e:
                log.debug("ui_automation.format_selector_failed", selector=selector, error=str(e))
        return None

    async def _send_prompt(
        self,
        page: Page,
        prompt_text: str,
        out_dir: Path | None = None,
        format_prompt: bool = False,
    ) -> None:
        """Type ``prompt_text`` into Flow's editor and submit.

        Selectors are tried in priority order; the first visible match
        wins. The text input is cleared first (Slate.js requires real
        keyboard events — ``.fill()`` bypasses onChange handlers).

        A staged R2V character entity is NOT affected by the clear: 'Incluir no
        comando' stages it in a separate references drawer, not as a chip inside
        this prompt box (verified 2026-06-06), so the entity still rides the
        submit.

        Submission is preferred via the Create button; if no submit
        button is visible, Enter is pressed as a fallback.

        On input-not-found, a debug screenshot is written to ``out_dir``
        (if provided) and ``RuntimeError`` is raised.
        """
        # Dismiss any overlays that might have appeared since entering the editor.
        await self._dismiss_blocking_overlays(page, out_dir)

        input_box = await self._locate_prompt_box(page, out_dir)

        await input_box.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        # insert_text fires a single beforeinput event that Slate.js handles
        # natively — near-instant vs keyboard.type() which is ~1.5s/char.
        await page.keyboard.insert_text(prompt_text)
        await page.wait_for_timeout(_jitter_ms(500))

        if format_prompt:
            # The bound box and the exact string we inserted — the rewrite is only
            # detectable against a baseline we know landed, and holding that baseline
            # on `self` would couple it across generations on a reused page.
            await self.format_character_prompt(page, prompt_box=input_box, typed_text=prompt_text)

        await self._click_submit(page)

    async def _submit_body_prompt(
        self,
        page: Page,
        body_description: str,
        *,
        boxes_before: int,
        shared_body_box: bool = False,
        out_dir: Path | None = None,
        format_prompt: bool = False,
    ) -> None:
        """Submit a self-contained triptych body prompt into the body slot's OWN box.

        Activating Create Body auto-attaches the generated face as a reference.
        Current Flow reuses the existing Slate box; legacy cohorts mount a new
        one. Rather than parsing either cohort's localized placeholder/template,
        this helper replaces the settled body composer with gflow's own
        locale-independent triptych instruction plus the body description.

        :meth:`_locate_body_prompt_box` binds the shared box only after the face
        reference mounts, or the legacy last box only after its count rises.
        Observed live 2026-07-25 (0.43.0, incident 8ecd11cc): typing before the
        mode settled replaced the stored portrait prompt, which Flow autosaved.

        Before any destructive keyboard input, the helper verifies that the
        selected body box retained focus. After typing,
        :meth:`_verify_body_prompt_isolation` verifies the target and, when the
        legacy portrait box is separately mounted, reads that box back too.

        ONE generation then yields all three angles (front/side/back) as a
        single triptych image, seeded by the auto-attached face reference.

        Logs ``ui_automation.body_prompt_templated`` with ``template`` and the
        submitted length (NO body text).
        """
        input_box = await self._locate_body_prompt_box(
            page,
            boxes_before=boxes_before,
            shared_body_box=shared_body_box,
            out_dir=out_dir,
        )

        full_prompt = _BODY_TRIPTYCH_PREAMBLE + body_description

        # Clear Flow's pre-filled (localized) template and replace it wholesale.
        # Slate.js needs real keyboard events, but every event must stay bound to
        # this locator: page-global keyboard calls can follow a late focus bounce
        # into the autosaved portrait composer.
        await input_box.click()
        await self._verify_body_prompt_focus(page, input_box, out_dir)
        await input_box.press("Control+A")
        await input_box.press("Delete")
        await input_box.press_sequentially(full_prompt)
        await page.wait_for_timeout(_jitter_ms(500))

        # Readback guard — abort BEFORE submit if the typing landed in the
        # portrait box (mode switch not settled → the 2026-07-25 corruption).
        await self._verify_body_prompt_isolation(page, input_box, out_dir)

        log.info(
            "ui_automation.body_prompt_templated",
            template="self_contained",
            prompt_len=len(full_prompt),
        )
        if format_prompt:
            # `input_box` is the BODY composer, bound by _locate_body_prompt_box —
            # passing it is what keeps the format click off the portrait's box on a
            # two-box cohort.
            await self.format_character_prompt(page, prompt_box=input_box, typed_text=full_prompt)

        await self._click_submit(page)

    async def _count_character_prompt_boxes(self, page: Page) -> int:
        """Count the editor's prompt boxes (legacy body-mode baseline).

        Counts through the SAME anchor the readiness gate accepts, so both
        frontends are seen: labs mounts Slate, flow.google.com mounts
        ProseMirror. Counting only Slate reported 0 boxes on the migrated host
        and aborted the body step with "Body prompt box did not mount" — on an
        editor whose box was mounted and visible the whole time (2026-09-06).
        """
        return int(await page.locator(self._CHARACTER_EDITOR_READY_SELECTOR).count())

    async def _locate_body_prompt_box(
        self,
        page: Page,
        *,
        boxes_before: int,
        shared_body_box: bool = False,
        out_dir: Path | None = None,
    ) -> Any:
        """Locate the body composer's OWN Slate prompt box — never the portrait's.

        The character editor is a two-mode surface (Portrait / Create Body).
        Typing before the switch settles can overwrite the stored portrait
        prompt (observed live 2026-07-25, incident 8ecd11cc), so localized mode
        labels are never treated as proof of the active composer.

        The current editor (live 2026-07-26) reuses one Slate box and signals a
        settled body mode by mounting the generated-face reference; callers
        pass ``shared_body_box=True`` only after observing that signal. Older
        cohorts mount a second box, so their count must exceed
        ``boxes_before``. In either case the LAST mounted box is the target.
        """
        # Both frontends' anchors — see _count_character_prompt_boxes.
        loc = page.locator(self._CHARACTER_EDITOR_READY_SELECTOR)
        deadline = time.monotonic() + _BODY_SLOT_MOUNT_TIMEOUT_S
        count = int(await loc.count())
        required_count = 0 if shared_body_box else boxes_before
        while count <= required_count and time.monotonic() < deadline:
            await page.wait_for_timeout(_BODY_SLOT_MOUNT_POLL_MS)
            count = int(await loc.count())
        if count <= required_count:
            shot = await _capture_debug_screenshot(
                page, out_dir, "debug_body_prompt_box_not_mounted.png"
            )
            settle_signal = (
                "the shared body-mode box disappeared"
                if shared_body_box
                else "no additional body box mounted"
            )
            msg = (
                f"Body prompt box did not mount within "
                f"{_BODY_SLOT_MOUNT_TIMEOUT_S:.0f}s after body-mode activation "
                f"(prompt boxes: {count}, before slot-add: {boxes_before}). "
                f"The body-mode settle check reported that {settle_signal}. "
                "Typing now would land in the portrait prompt box and "
                "overwrite the stored portrait prompt — aborting the body "
                f"step. URL: {page.url}.{screenshot_clause(shot)}"
            )
            raise RuntimeError(msg)
        box = loc.nth(count - 1)
        await box.wait_for(state="visible", timeout=10_000)
        log.info(
            "ui_automation.body_prompt_box_bound",
            boxes_before=boxes_before,
            boxes_now=count,
        )
        return box

    async def _verify_body_prompt_focus(
        self,
        page: Page,
        input_box: Any,
        out_dir: Path | None = None,
    ) -> None:
        """Fail closed unless the body box owns focus before destructive input."""
        try:
            focused = bool(
                await input_box.evaluate(
                    "element => element === document.activeElement "
                    "|| element.contains(document.activeElement)"
                )
            )
        except Exception as e:
            shot = await _capture_debug_screenshot(
                page, out_dir, "debug_body_prompt_focus_unverified.png"
            )
            msg = (
                "Could not verify body prompt focus before editing; aborting to preserve "
                "the stored portrait prompt. "
                f"URL: {page.url}.{screenshot_clause(shot)}"
            )
            raise RuntimeError(msg) from e
        if not focused:
            shot = await _capture_debug_screenshot(
                page, out_dir, "debug_body_prompt_wrong_focus.png"
            )
            msg = (
                "Body composer focus moved to the wrong prompt box before editing. "
                "Aborting before destructive input so the stored portrait prompt is "
                f"not overwritten. URL: {page.url}.{screenshot_clause(shot)}"
            )
            raise RuntimeError(msg)

    async def _verify_body_prompt_isolation(
        self,
        page: Page,
        input_box: Any,
        out_dir: Path | None = None,
    ) -> None:
        """Post-type readback: the triptych text is in the body box ONLY.

        A wrong-box landing (the portrait composer captured the typing while
        the Create-Body mode switch was still settling) corrupts the entity's
        stored portrait prompt — Flow autosaves the box via
        PATCH /v1/flowWorkflows/{id} (observed live 2026-07-25, 0.43.0).
        Abort BEFORE submit in that case. Readback I/O errors also fail closed:
        unstable prompt state is not safe to submit on an autosaved surface.
        """
        sentinel = _BODY_TRIPTYCH_PREAMBLE[:24]
        try:
            target_text = str(await input_box.inner_text() or "")
            # Readiness anchor, not the Slate-only selector: on the migrated
            # editor the latter counts 0 boxes, so the portrait-overwrite guard
            # below silently compared against an empty string.
            boxes = page.locator(self._CHARACTER_EDITOR_READY_SELECTOR)
            box_count = int(await boxes.count())
            portrait_text = str(await boxes.first.inner_text() or "") if box_count > 1 else ""
        except Exception as e:
            log.warning(
                "ui_automation.body_prompt_readback_failed",
                error=str(e)[:120],
            )
            shot = await _capture_debug_screenshot(
                page, out_dir, "debug_body_prompt_readback_failed.png"
            )
            msg = (
                "Could not verify body prompt isolation after editing. "
                "Aborting before submit because the prompt boxes became unstable. "
                f"URL: {page.url}.{screenshot_clause(shot)}"
            )
            raise RuntimeError(msg) from e
        if sentinel in portrait_text or sentinel not in target_text:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_body_prompt_wrong_box.png")
            msg = (
                "Body triptych prompt landed in the wrong prompt box "
                f"(portrait polluted: {sentinel in portrait_text}, "
                f"body box has prompt: {sentinel in target_text}). "
                "Aborting before submit so a corrupted portrait prompt is "
                "never generated. Re-check the entity's portrait prompt "
                f"in the Flow UI. URL: {page.url}.{screenshot_clause(shot)}"
            )
            raise RuntimeError(msg)
