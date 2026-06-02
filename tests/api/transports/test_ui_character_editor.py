"""Unit tests for UiAutomationTransport character-editor navigation and
passive-capture entry (Phase 2 Task 4).

All tests use a faked Playwright ``page`` — no real browser is launched.
The fake-page pattern mirrors ``test_ui_automation_image_mode.py`` and
``test_ui_automation.py``: a ``MagicMock`` with ``AsyncMock`` helpers that
record calls, raise on demand, or stay silent.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports.ui_automation import UiAutomationTransport

# ---------------------------------------------------------------------------
# Helpers / shared fakes
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "character_gen_response.json"


def _load_fixture() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _make_captured_response(
    fixture: dict[str, Any],
    *,
    project_id: str = "580a6bbf-d433-4153-80b9-1842b5a560ea",
    status: int = 200,
) -> dict[str, Any]:
    """Wrap the fixture body as a captured response dict (the shape _await_captured returns)."""
    return {
        "status": status,
        "url": (
            f"https://aisandbox-pa.googleapis.com/v1/projects/{project_id}"
            "/flowMedia:batchGenerateImages"
        ),
        "body": fixture,
    }


def _make_page(
    *,
    visible_selectors: set[str] | None = None,
    url: str = "https://labs.google/fx/pt/tools/flow/project/p1/character/e1",
) -> MagicMock:
    """Build a minimal fake Playwright page.

    ``visible_selectors``: selectors for which ``wait_for(state='visible')``
    succeeds.  All others raise ``Exception('not visible')``.
    """
    vis = visible_selectors or set()
    page = MagicMock()
    page.url = url
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.keyboard.insert_text = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"")

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        loc.first = loc
        if sel in vis:
            loc.wait_for = AsyncMock()
            loc.is_visible = AsyncMock(return_value=True)
        else:
            loc.wait_for = AsyncMock(side_effect=Exception("not visible"))
            loc.is_visible = AsyncMock(return_value=False)
        loc.click = AsyncMock()
        loc.count = AsyncMock(return_value=0)
        loc.text_content = AsyncMock(return_value="")

        def _nth(_n: int) -> MagicMock:
            inner = MagicMock()
            inner.wait_for = AsyncMock(side_effect=Exception("not visible"))
            inner.click = AsyncMock()
            return inner

        loc.nth = MagicMock(side_effect=_nth)
        return loc

    page.locator = MagicMock(side_effect=_locator)
    page.on = MagicMock()
    page.remove_listener = MagicMock()
    return page


def _make_transport(*, page: MagicMock | None = None) -> UiAutomationTransport:
    """Return a transport instance with setup already marked done."""
    t = UiAutomationTransport()
    t._setup_done = True  # type: ignore[attr-defined]
    if page is not None:
        t._page = page  # type: ignore[attr-defined]
    return t


# ---------------------------------------------------------------------------
# Tests: _workflows_from_responses
# ---------------------------------------------------------------------------


class TestWorkflowsFromResponses:
    def test_extracts_workflow_with_parent_entity_id(self) -> None:
        fixture = _load_fixture()
        response = _make_captured_response(fixture)
        result = UiAutomationTransport._workflows_from_responses([response])
        assert len(result) == 1
        wf = result[0]
        assert wf["name"] == "fed25ab9-30c3-4819-b2d2-a0c6a0e13241"
        assert wf["parentEntityId"] == "d73ef41a-5fa0-4cef-af3f-ee9f8b20390f"
        assert wf["metadata"]["primaryMediaId"] == "542e49ba-183b-4db9-812f-73c841c10673"
        assert wf["projectId"] == "580a6bbf-d433-4153-80b9-1842b5a560ea"

    def test_skips_non_200_responses(self) -> None:
        fixture = _load_fixture()
        response = _make_captured_response(fixture, status=500)
        result = UiAutomationTransport._workflows_from_responses([response])
        assert result == []

    def test_empty_workflows_key(self) -> None:
        response: dict[str, Any] = {"status": 200, "url": "http://x", "body": {"workflows": []}}
        assert UiAutomationTransport._workflows_from_responses([response]) == []

    def test_missing_workflows_key(self) -> None:
        response: dict[str, Any] = {"status": 200, "url": "http://x", "body": {"media": []}}
        assert UiAutomationTransport._workflows_from_responses([response]) == []

    def test_multiple_responses_accumulated(self) -> None:
        fixture = _load_fixture()
        r1 = _make_captured_response(fixture)
        r2 = _make_captured_response(fixture)
        result = UiAutomationTransport._workflows_from_responses([r1, r2])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Tests: _enter_character_editor
# ---------------------------------------------------------------------------


class TestEnterCharacterEditor:
    @pytest.mark.asyncio
    async def test_goto_uses_character_editor_url(self) -> None:
        """page.goto must be called with the correct /fx/ character editor URL."""
        ready_sel = UiAutomationTransport._CHARACTER_EDITOR_READY_SELECTOR
        page = _make_page(visible_selectors={ready_sel})
        t = _make_transport(page=page)

        # Stub out overlay dismiss (it looks for iframes)
        t._dismiss_blocking_overlays = AsyncMock(return_value=False)  # type: ignore[attr-defined]

        await t._enter_character_editor(page, project_id="p1", entity_id="e1", locale="pt")

        # Assert goto was called and the URL contains /fx/
        page.goto.assert_called_once()
        url_arg: str = page.goto.call_args[0][0]
        assert "/fx/" in url_arg
        assert "/project/p1/character/e1" in url_arg

    @pytest.mark.asyncio
    async def test_raises_when_editor_not_ready(self) -> None:
        """RuntimeError when the prompt textbox never becomes visible."""
        page = _make_page(visible_selectors=set())  # nothing visible
        t = _make_transport(page=page)
        t._dismiss_blocking_overlays = AsyncMock(return_value=False)  # type: ignore[attr-defined]

        with pytest.raises(RuntimeError, match="Character editor not ready"):
            await t._enter_character_editor(page, project_id="p", entity_id="e", locale="en")

    @pytest.mark.asyncio
    async def test_does_not_call_enter_editor(self) -> None:
        """_enter_character_editor must NOT invoke _enter_editor (no new-project creation)."""
        ready_sel = UiAutomationTransport._CHARACTER_EDITOR_READY_SELECTOR
        page = _make_page(visible_selectors={ready_sel})
        t = _make_transport(page=page)
        t._dismiss_blocking_overlays = AsyncMock(return_value=False)  # type: ignore[attr-defined]
        t._enter_editor = AsyncMock()  # type: ignore[attr-defined]

        await t._enter_character_editor(page, project_id="p", entity_id="e", locale="en")

        t._enter_editor.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: generate_character_images — slot 0 (face) and slot 1 (body)
# ---------------------------------------------------------------------------


class TestGenerateCharacterImages:
    """Tests use recorder-style mocking (same pattern as TestModeSwitchCallSite)."""

    @staticmethod
    def _build_transport_with_recorder(
        *,
        fixture: dict[str, Any],
        project_id: str = "proj-1",
    ) -> tuple[UiAutomationTransport, list[str]]:
        """Wire a transport with all interactive steps replaced by recorders.

        The ``_await_captured`` mock returns a list containing one captured
        response built from the fixture, allowing ``_images_from_responses``
        and ``_workflows_from_responses`` to do real work.
        """
        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        page = MagicMock()
        page.url = f"https://labs.google/fx/pt/tools/flow/project/{project_id}/character/e1"
        t._page = page  # type: ignore[attr-defined]

        order: list[str] = []

        def _rec(name: str, return_value: object = None) -> AsyncMock:
            m = AsyncMock(return_value=return_value)
            m.side_effect = lambda *_a, **_kw: order.append(name) or return_value
            return m

        t._enter_character_editor = _rec("enter_character_editor")  # type: ignore[attr-defined]
        t._dismiss_blocking_overlays = _rec("dismiss_overlays")  # type: ignore[attr-defined]
        t._configure_generation_settings = _rec("configure")  # type: ignore[attr-defined]
        t._send_prompt = _rec("send_prompt")  # type: ignore[attr-defined]
        t._click_character_slot_add = _rec("slot_add")  # type: ignore[attr-defined]

        # _attach_batch_response_listener is sync — seed it with the fixture response.
        captured_store: list[dict[str, Any]] = []

        def _attach(
            _page: Any, *, project_id: str | None = None
        ) -> tuple[list[dict[str, Any]], Any]:
            order.append("attach_listener")
            return captured_store, lambda: order.append("detach_listener")

        t._attach_batch_response_listener = MagicMock(side_effect=_attach)  # type: ignore[attr-defined]

        # _await_captured: seed the store then return it
        captured_response = _make_captured_response(fixture, project_id=project_id)
        # Add ts so post-submit-time filter passes
        captured_response_with_ts = {**captured_response, "ts": time.monotonic() + 1000}

        async def _await_captured(
            _captured: list[Any],
            _timeout: float = 180.0,
            **_kw: Any,
        ) -> list[dict[str, Any]]:
            order.append("await_captured")
            # Populate the store so workflows can be read
            captured_store.append(captured_response_with_ts)
            # Return the plain response (no ts key) as _await_captured normally would
            return [captured_response]

        t._await_captured = AsyncMock(side_effect=_await_captured)  # type: ignore[attr-defined]

        return t, order

    @pytest.mark.asyncio
    async def test_slot_0_does_not_click_slot_add(self) -> None:
        """Face slot (index 0): slot-add interaction must NOT fire."""
        fixture = _load_fixture()
        t, order = self._build_transport_with_recorder(fixture=fixture)
        req = GenerateImageRequest(
            prompt="portrait of a heroine", model=Model.NARWHAL, aspect=Aspect.LANDSCAPE
        )
        images, workflows = await t.generate_character_images(
            project_id="proj-1",
            entity_id="e1",
            request=req,
            image_reference_index=0,
            locale="pt",
        )

        assert "slot_add" not in order, f"slot_add must not fire for index 0; order={order}"
        assert len(images) >= 1
        assert len(workflows) >= 1

    @pytest.mark.asyncio
    async def test_slot_1_fires_slot_add_before_send_prompt(self) -> None:
        """Body slot (index 1): slot-add must fire before send_prompt."""
        fixture = _load_fixture()
        t, order = self._build_transport_with_recorder(fixture=fixture)
        req = GenerateImageRequest(
            prompt="warrior outfit", model=Model.NARWHAL, aspect=Aspect.PORTRAIT
        )
        images, workflows = await t.generate_character_images(
            project_id="proj-1",
            entity_id="e1",
            request=req,
            image_reference_index=1,
            locale="pt",
        )

        assert "slot_add" in order, f"slot_add must fire for index 1; order={order}"
        slot_idx = order.index("slot_add")
        send_idx = order.index("send_prompt")
        assert slot_idx < send_idx, f"slot_add must precede send_prompt; order={order}"
        assert len(images) >= 1

    @pytest.mark.asyncio
    async def test_enter_editor_not_called(self) -> None:
        """_enter_editor (new-project path) must never be called."""
        fixture = _load_fixture()
        t, order = self._build_transport_with_recorder(fixture=fixture)
        req = GenerateImageRequest(prompt="test", model=Model.NARWHAL, aspect=Aspect.LANDSCAPE)
        await t.generate_character_images(
            project_id="proj-1",
            entity_id="e1",
            request=req,
            image_reference_index=0,
            locale="en",
        )
        assert "enter_editor" not in order, (
            f"_enter_editor (new-project path) must not be called; order={order}"
        )

    @pytest.mark.asyncio
    async def test_returns_workflow_parent_entity_id(self) -> None:
        """Returned workflows carry parentEntityId from the fixture."""
        fixture = _load_fixture()
        t, _ = self._build_transport_with_recorder(
            fixture=fixture,
            project_id="580a6bbf-d433-4153-80b9-1842b5a560ea",
        )
        req = GenerateImageRequest(
            prompt="character portrait", model=Model.NARWHAL, aspect=Aspect.LANDSCAPE
        )
        _images, workflows = await t.generate_character_images(
            project_id="580a6bbf-d433-4153-80b9-1842b5a560ea",
            entity_id="d73ef41a-5fa0-4cef-af3f-ee9f8b20390f",
            request=req,
            image_reference_index=0,
            locale="en",
        )
        assert len(workflows) == 1
        assert workflows[0]["parentEntityId"] == "d73ef41a-5fa0-4cef-af3f-ee9f8b20390f"

    @pytest.mark.asyncio
    async def test_listener_filtered_on_project_id(self) -> None:
        """_attach_batch_response_listener must be called with project_id."""
        fixture = _load_fixture()
        t, _ = self._build_transport_with_recorder(fixture=fixture, project_id="myproject")
        req = GenerateImageRequest(prompt="test", model=Model.NARWHAL, aspect=Aspect.LANDSCAPE)
        await t.generate_character_images(
            project_id="myproject",
            entity_id="e1",
            request=req,
            image_reference_index=0,
            locale="en",
        )
        t._attach_batch_response_listener.assert_called_once()
        call_kwargs = t._attach_batch_response_listener.call_args[1]
        assert call_kwargs.get("project_id") == "myproject"

    @pytest.mark.asyncio
    async def test_raises_when_setup_not_called(self) -> None:
        """generate_character_images raises RuntimeError if setup() not called."""
        t = UiAutomationTransport()  # _setup_done = False
        req = GenerateImageRequest(prompt="x", model=Model.NARWHAL, aspect=Aspect.LANDSCAPE)
        with pytest.raises(RuntimeError, match="setup.*must be called"):
            await t.generate_character_images(
                project_id="p",
                entity_id="e",
                request=req,
                image_reference_index=0,
                locale="en",
            )
