"""Unit tests for ``UiAutomationTransport._switch_to_image_mode``.

Mirror of the video-side ``_switch_to_video_mode`` tests. The image
transport must select Image mode explicitly when entering the editor;
otherwise an account whose last-used mode was Video silently routes
``image t2i`` / ``image batch`` prompts to the video endpoint (no
``batchGenerateImages`` response observed; 3-minute listener timeout).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.transports import ui_automation as mod
from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import (
    MODE_SWITCH_TRIGGER_SELECTORS,
)
from gflow_cli.errors import UiSelectorDriftError


def _cascade_page(visible: set[str]) -> MagicMock:
    """A fake page whose ``locator(sel)`` is 'visible' only for ``sel in visible``."""
    page = MagicMock()

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        loc.first = loc
        if sel in visible:
            loc.wait_for = AsyncMock()
        else:
            loc.wait_for = AsyncMock(side_effect=Exception("not visible"))
        loc.click = AsyncMock()
        # No cohort markers by default → _detect_non_classic_cohort returns None,
        # so a trigger miss routes to UiSelectorDriftError (genuine drift), not
        # FlowAgentUiError. Cohort tests live in test_agentic_cohort_detection.py.
        loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = MagicMock(side_effect=_locator)
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    return page


class TestSwitchToImageMode:
    @pytest.mark.asyncio
    async def test_opens_dropdown_then_clicks_image_tab(self) -> None:
        trigger = MODE_SWITCH_TRIGGER_SELECTORS[0]
        image_tab = mod.IMAGE_TAB_IN_MENU_SELECTORS[0]
        page = _cascade_page({trigger, image_tab})
        await UiAutomationTransport._switch_to_image_mode(page, out_dir=None)
        assert page.locator.call_count >= 2

    @pytest.mark.asyncio
    async def test_raises_when_trigger_missing(self) -> None:
        page = _cascade_page(set())
        with pytest.raises(UiSelectorDriftError, match="mode_switch_trigger"):
            await UiAutomationTransport._switch_to_image_mode(page, out_dir=None)

    @pytest.mark.asyncio
    async def test_raises_when_image_tab_missing(self) -> None:
        page = _cascade_page({MODE_SWITCH_TRIGGER_SELECTORS[0]})
        with pytest.raises(UiSelectorDriftError, match="Image tab"):
            await UiAutomationTransport._switch_to_image_mode(page, out_dir=None)

    @pytest.mark.asyncio
    async def test_trigger_miss_detail_carries_diagnostics_path(self, tmp_path: Path) -> None:
        # On a genuine drift (no cohort marker), the drift error now points at the
        # richer debug-engine diagnostics artifact (ligature inventory + full-page
        # screenshot) instead of a bare viewport shot — the payload #183 reporters need.
        page = _cascade_page(set())
        page.evaluate = AsyncMock(return_value={"ligatures": [], "cropPresent": False})
        page.screenshot = AsyncMock()
        with pytest.raises(UiSelectorDriftError, match="diag_mode_switch_miss"):
            await UiAutomationTransport._switch_to_image_mode(page, out_dir=tmp_path)
        page.screenshot.assert_awaited()

    @pytest.mark.asyncio
    async def test_trigger_miss_detail_omits_screenshot_clause_without_out_dir(self) -> None:
        # No out_dir → no capture; the detail must drop the clause entirely
        # rather than render a literal "Screenshot: None".
        page = _cascade_page(set())
        with pytest.raises(UiSelectorDriftError) as excinfo:
            await UiAutomationTransport._switch_to_image_mode(page, out_dir=None)
        assert "Screenshot" not in str(excinfo.value)
        assert "None" not in str(excinfo.value)


class TestModeSwitchCallSite:
    """Regression guard: assert ``switch_to_image_mode`` is actually
    invoked by ``generate_images`` and ``generate_images_batch`` before
    any prompt is submitted. This is the assertion that would have caught
    the 2026-05-23 mode-confusion bug (image prompts silently routed to
    the video endpoint) had it existed earlier.

    With the Strategy pattern, the transport creates a ``ClassicFlowUiDriver``
    and delegates to it — the recorder patches ``ClassicFlowUiDriver`` methods
    at the class level so the delegation path is still observable.
    """

    @staticmethod
    def _build_transport_with_recorder() -> tuple[UiAutomationTransport, list[str]]:
        """Wire up a UiAutomationTransport instance with every step we care
        about replaced by an AsyncMock that appends its name to a shared
        call-order list. Returns ``(transport, call_order)``."""
        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        page = MagicMock()
        # Stable Flow project URL so _extract_project_id returns a real string
        # after _enter_editor — both the single t2i and batch paths read it.
        page.url = (
            "https://labs.google/fx/tools/flow/project/11111111-2222-3333-4444-555555555555?hl=en"
        )
        t._page = page  # type: ignore[attr-defined]
        order: list[str] = []

        def _recorder(name: str, return_value: object = None) -> AsyncMock:
            m = AsyncMock(return_value=return_value)
            m.side_effect = lambda *_a, **_kw: order.append(name) or return_value
            return m

        t._enter_editor = _recorder("enter_editor")  # type: ignore[attr-defined]
        t._dismiss_blocking_overlays = _recorder("dismiss_overlays")  # type: ignore[attr-defined]

        # _attach_batch_response_listener is sync; return a (list, detach) pair.
        def _attach(*_a: object, **_kw: object) -> tuple[list[object], object]:
            order.append("attach_listener")
            return ([], lambda: order.append("detach_listener"))

        t._attach_batch_response_listener = MagicMock(side_effect=_attach)  # type: ignore[attr-defined]
        t._send_prompt = _recorder("send_prompt")  # type: ignore[attr-defined]

        # _await_captured raises a sentinel so the test stops cleanly after
        # _send_prompt was reached — we do not want to mock the full
        # parse + download chain (that is exercised by other tests).
        class _StopHereError(RuntimeError):
            pass

        def _await(*_a: object, **_kw: object) -> None:
            order.append("await_captured")
            raise _StopHereError("stop after send_prompt — call-order recorded")

        t._await_captured = AsyncMock(side_effect=_await)  # type: ignore[attr-defined]
        t._stop_sentinel = _StopHereError  # type: ignore[attr-defined]
        return t, order

    @pytest.mark.asyncio
    async def test_generate_images_switches_to_image_mode_before_send_prompt(self) -> None:
        from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

        t, order = self._build_transport_with_recorder()
        req = GenerateImageRequest(
            prompt="a calm forest at dawn", model=Model.NARWHAL, aspect=Aspect.PORTRAIT
        )

        # Patch the ClassicFlowUiDriver methods so the delegation path is
        # observable — the transport instantiates the driver per generation,
        # so class-level patching intercepts all driver calls.
        def _switch(*_a: object, **_kw: object) -> None:
            order.append("switch_to_image_mode")

        def _configure(*_a: object, **_kw: object) -> None:
            order.append("configure")

        def _send(*_a: object, **_kw: object) -> None:
            order.append("send_prompt")

        switch_mock = AsyncMock(side_effect=_switch)
        configure_mock = AsyncMock(side_effect=_configure)
        send_mock = AsyncMock(side_effect=_send)
        with (
            patch.object(ClassicFlowUiDriver, "switch_to_image_mode", new=switch_mock),
            patch.object(ClassicFlowUiDriver, "configure_image_settings", new=configure_mock),
            patch.object(ClassicFlowUiDriver, "send_prompt", new=send_mock),
            pytest.raises(t._stop_sentinel),  # type: ignore[attr-defined]
        ):
            await t.generate_images(project_id=None, request=req)

        assert "switch_to_image_mode" in order, f"switch_to_image_mode never called; order={order}"
        switch_idx = order.index("switch_to_image_mode")
        send_idx = order.index("send_prompt")
        assert switch_idx < send_idx, (
            f"switch_to_image_mode must precede send_prompt; order={order}"
        )
        dismiss_idx = order.index("dismiss_overlays")
        assert dismiss_idx < switch_idx, (
            f"switch_to_image_mode must follow dismiss_overlays; order={order}"
        )

    @pytest.mark.asyncio
    async def test_generate_images_batch_switches_to_image_mode_once_before_any_prompt(
        self,
    ) -> None:
        from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
        from gflow_cli.errors import BatchPartialError

        t, order = self._build_transport_with_recorder()
        prompts: list[GenerateImageRequest] = [
            GenerateImageRequest(prompt=f"p{i}", model=Model.NARWHAL, aspect=Aspect.PORTRAIT)
            for i in range(3)
        ]

        def _switch(*_a: object, **_kw: object) -> None:
            order.append("switch_to_image_mode")

        def _configure(*_a: object, **_kw: object) -> None:
            order.append("configure")

        def _send(*_a: object, **_kw: object) -> None:
            order.append("send_prompt")

        switch_mock = AsyncMock(side_effect=_switch)
        configure_mock = AsyncMock(side_effect=_configure)
        send_mock = AsyncMock(side_effect=_send)
        # The recorder makes _await_captured raise — under continue_on_error=False
        # the transport wraps it in BatchPartialError. That is the expected
        # synthetic-test outcome; we only care that the mode switch ran exactly
        # once and before the first send_prompt.
        with (
            patch.object(ClassicFlowUiDriver, "switch_to_image_mode", new=switch_mock),
            patch.object(ClassicFlowUiDriver, "configure_image_settings", new=configure_mock),
            patch.object(ClassicFlowUiDriver, "send_prompt", new=send_mock),
            pytest.raises(BatchPartialError),
        ):
            await t.generate_images_batch(
                prompts=prompts, jitter_range=(0.0, 0.0), continue_on_error=False
            )

        switch_count = order.count("switch_to_image_mode")
        assert switch_count == 1, (
            f"switch_to_image_mode must be called exactly once per batch; "
            f"got {switch_count}; order={order}"
        )
        switch_idx = order.index("switch_to_image_mode")
        first_send_idx = order.index("send_prompt")
        assert switch_idx < first_send_idx, (
            f"switch_to_image_mode must precede the first send_prompt; order={order}"
        )
