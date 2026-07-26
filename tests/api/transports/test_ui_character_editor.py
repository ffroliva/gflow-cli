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

from gflow_cli.api.character import CharacterImageRequest
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
        t._select_character_model = _rec("select_model")  # type: ignore[attr-defined]
        t._send_prompt = _rec("send_prompt")  # type: ignore[attr-defined]
        t._submit_body_prompt = _rec("submit_body_prompt")  # type: ignore[attr-defined]
        t._click_character_slot_add = _rec(  # type: ignore[attr-defined]
            "slot_add", return_value=True
        )
        t._count_character_prompt_boxes = _rec("count_boxes", return_value=1)  # type: ignore[attr-defined]

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
        """Face slot (index 0): slot-add interaction must NOT fire and the
        face path uses _send_prompt (raw prompt), NOT the body template path."""
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
        assert "count_boxes" not in order, (
            f"the body-box mount gate must not run for the face slot; order={order}"
        )
        assert "send_prompt" in order, f"face slot must use _send_prompt; order={order}"
        assert "submit_body_prompt" not in order, (
            f"face slot must NOT use the body template path; order={order}"
        )
        # The raw face prompt is sent verbatim (no template substitution).
        t._send_prompt.assert_awaited_once()  # type: ignore[attr-defined]
        assert t._send_prompt.call_args.args[1] == "portrait of a heroine"  # type: ignore[attr-defined]
        assert len(images) >= 1
        assert len(workflows) >= 1

    @pytest.mark.asyncio
    async def test_slot_1_fires_slot_add_before_body_prompt(self) -> None:
        """Body slot (index 1): slot-add must fire before the body template
        submit, and the body path uses _submit_body_prompt (NOT _send_prompt)."""
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
        assert "submit_body_prompt" in order, (
            f"body slot must use _submit_body_prompt; order={order}"
        )
        assert "send_prompt" not in order, (
            f"body slot must NOT use the generic _send_prompt (it would clear "
            f"Flow's pre-filled triptych template); order={order}"
        )
        slot_idx = order.index("slot_add")
        body_idx = order.index("submit_body_prompt")
        assert slot_idx < body_idx, f"slot_add must precede body submit; order={order}"
        assert "count_boxes" in order and order.index("count_boxes") < slot_idx, (
            f"the prompt-box count snapshot (the body-box mount gate's baseline) "
            f"must be taken BEFORE slot-add; order={order}"
        )
        body_kwargs = t._submit_body_prompt.call_args.kwargs  # type: ignore[attr-defined]
        assert body_kwargs.get("boxes_before") == 1, (
            f"the pre-slot-add box count must reach _submit_body_prompt as "
            f"boxes_before; got kwargs={body_kwargs}"
        )
        assert body_kwargs.get("shared_body_box") is True
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


# ---------------------------------------------------------------------------
# Regression: real CharacterImageRequest field access (issue caught by live e2e)
# ---------------------------------------------------------------------------


class TestCharacterImageRequestFieldContract:
    """Lock the wire between ``CharacterImageRequest`` and model selection.

    Characters have NO aspect-ratio control and NO per-generation settings
    panel — only the editor's own model picker.  ``_generate_character_images_locked``
    must drive a REAL ``CharacterImageRequest`` (no ``.count``, no ``.aspect``)
    without AttributeError, calling ``_select_character_model`` with the request's
    model alias and ``_await_captured`` with the per-slot ``expected_count`` of 1.

    These tests build a REAL ``CharacterImageRequest`` and replace
    ``_select_character_model`` with a spy, forcing the real attribute access to
    execute (a stray ``request.aspect`` / ``request.count`` would raise).
    """

    @staticmethod
    def _build_transport_with_model_spy(
        *,
        fixture: dict[str, Any],
        project_id: str = "proj-1",
    ) -> tuple[UiAutomationTransport, AsyncMock, list[Any]]:
        """Wire a transport where ``_select_character_model`` is a SPY.

        Returns ``(transport, model_spy, await_count_calls)`` where ``model_spy``
        records every ``(args, kwargs)`` it was called with and
        ``await_count_calls`` collects the ``expected_count`` passed to
        ``_await_captured``.  Only Playwright/browser-touching steps are stubbed.
        """
        t = UiAutomationTransport()
        t._setup_done = True  # type: ignore[attr-defined]
        page = MagicMock()
        page.url = f"https://labs.google/fx/pt/tools/flow/project/{project_id}/character/e1"
        t._page = page  # type: ignore[attr-defined]

        t._enter_character_editor = AsyncMock()  # type: ignore[attr-defined]
        t._dismiss_blocking_overlays = AsyncMock(return_value=False)  # type: ignore[attr-defined]
        t._send_prompt = AsyncMock()  # type: ignore[attr-defined]
        t._click_character_slot_add = AsyncMock()  # type: ignore[attr-defined]

        # The SPY: real signature is _select_character_model(page, model_alias,
        # out_dir).  Record everything and return None — no browser touched.
        model_spy = AsyncMock(return_value=None)
        t._select_character_model = model_spy  # type: ignore[attr-defined]

        captured_response = _make_captured_response(fixture, project_id=project_id)
        await_count_calls: list[Any] = []

        async def _await_captured(
            _captured: list[Any],
            _timeout: float = 180.0,
            *,
            expected_count: Any = None,
            **_kw: Any,
        ) -> list[dict[str, Any]]:
            await_count_calls.append(expected_count)
            return [captured_response]

        t._await_captured = AsyncMock(side_effect=_await_captured)  # type: ignore[attr-defined]

        def _attach(
            _page: Any, *, project_id: str | None = None
        ) -> tuple[list[dict[str, Any]], Any]:
            return [], lambda: None

        t._attach_batch_response_listener = MagicMock(side_effect=_attach)  # type: ignore[attr-defined]

        return t, model_spy, await_count_calls

    @pytest.mark.asyncio
    async def test_drives_model_picker_without_attribute_error(self) -> None:
        """A REAL CharacterImageRequest (no .count, no .aspect) must drive the
        locked method without AttributeError; _select_character_model receives
        the request's model alias and _await_captured the per-slot count 1."""
        fixture = _load_fixture()
        t, model_spy, await_count_calls = self._build_transport_with_model_spy(fixture=fixture)

        # REAL DTO — CLI alias model, NO .count and NO .aspect attributes.
        req = CharacterImageRequest(prompt="x", model="nanopro")
        assert not hasattr(req, "count"), "guard: CharacterImageRequest must NOT have .count"
        assert not hasattr(req, "aspect"), "guard: CharacterImageRequest must NOT have .aspect"

        images, workflows = await t.generate_character_images(
            project_id="proj-1",
            entity_id="e1",
            request=req,
            image_reference_index=0,
            locale="pt",
        )

        # _select_character_model(page, model_alias, out_dir)
        model_spy.assert_called_once()
        call = model_spy.call_args
        model_alias = call.args[1]
        assert model_alias == "nanopro", (
            f"the request's model alias must reach _select_character_model; got {model_alias!r}"
        )

        # _await_captured must receive the per-slot count of 1 (hardcoded).
        assert await_count_calls == [1], (
            f"expected_count passed to _await_captured must be 1; got {await_count_calls!r}"
        )
        assert len(images) >= 1
        assert len(workflows) >= 1


# ---------------------------------------------------------------------------
# Tests: _select_character_model — editor model picker (best-effort, non-fatal)
# ---------------------------------------------------------------------------


class _FakeMenuOption:
    """A clickable model-menu option."""

    def __init__(self, *, visible: bool = True) -> None:
        self.clicked = False
        self.first = self
        self.click = AsyncMock(side_effect=self._record)
        if visible:
            self.wait_for = AsyncMock()
        else:
            self.wait_for = AsyncMock(side_effect=Exception("not visible"))

    async def _record(self, *_a: Any, **_kw: Any) -> None:
        self.clicked = True


class _FakeTrigger:
    """The arrow_drop_down dropdown trigger."""

    def __init__(self, *, visible: bool = True) -> None:
        self.clicked = False
        self.first = self
        self.click = AsyncMock(side_effect=self._record)
        if visible:
            self.wait_for = AsyncMock()
        else:
            self.wait_for = AsyncMock(side_effect=Exception("not visible"))

    async def _record(self, *_a: Any, **_kw: Any) -> None:
        self.clicked = True


def _make_model_picker_page(
    *,
    trigger_visible: bool = True,
    option_visible: bool = True,
) -> tuple[MagicMock, _FakeTrigger, _FakeMenuOption]:
    """Fake page wiring the model-picker trigger + the Nano Banana Pro option."""
    trigger = _FakeTrigger(visible=trigger_visible)
    option = _FakeMenuOption(visible=option_visible)

    trigger_sels = set(UiAutomationTransport._CHARACTER_MODEL_PICKER_TRIGGER_SELECTORS)

    page = MagicMock()
    page.url = "https://labs.google/fx/pt/tools/flow/project/p1/character/e1"
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"")
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    def _locator(sel: str) -> Any:
        if sel in trigger_sels:
            return trigger
        if "Nano Banana Pro" in sel:
            return option
        loc = MagicMock()
        loc.first = loc
        loc.wait_for = AsyncMock(side_effect=Exception("not visible"))
        loc.click = AsyncMock()
        return loc

    page.locator = MagicMock(side_effect=_locator)
    return page, trigger, option


class TestSelectCharacterModel:
    @pytest.mark.asyncio
    async def test_nano2_default_does_not_click_dropdown(self) -> None:
        """nano2 (the default) must be a no-op — no dropdown trigger click."""
        page, trigger, option = _make_model_picker_page()
        t = _make_transport(page=page)

        await t._select_character_model(page, "nano2", None)  # type: ignore[attr-defined]

        assert not trigger.clicked, "default model must NOT open the dropdown"
        assert not option.clicked

    @pytest.mark.asyncio
    async def test_nanopro_opens_dropdown_and_clicks_option(self) -> None:
        """nanopro must open the dropdown and click the Nano Banana Pro option."""
        page, trigger, option = _make_model_picker_page()
        t = _make_transport(page=page)

        await t._select_character_model(page, "nanopro", None)  # type: ignore[attr-defined]

        assert trigger.clicked, "nanopro must open the model dropdown"
        assert option.clicked, "the Nano Banana Pro option must be clicked"

    @pytest.mark.asyncio
    async def test_picker_not_found_is_non_fatal(self) -> None:
        """If neither the trigger nor the option is found, no exception escapes."""
        page, trigger, option = _make_model_picker_page(trigger_visible=False)
        t = _make_transport(page=page)

        # MUST NOT raise — best-effort model selection.
        await t._select_character_model(page, "nanopro", None)  # type: ignore[attr-defined]

        assert not option.clicked, "no trigger → no option click"

    @pytest.mark.asyncio
    async def test_unknown_alias_is_non_fatal_no_click(self) -> None:
        """An unknown alias must NOT crash and must not touch the dropdown."""
        page, trigger, option = _make_model_picker_page()
        t = _make_transport(page=page)

        await t._select_character_model(page, "totally-unknown", None)  # type: ignore[attr-defined]

        assert not trigger.clicked
        assert not option.clicked


# ---------------------------------------------------------------------------
# Tests: _click_character_slot_add selector logic (live-DOM grounded)
# ---------------------------------------------------------------------------


class _FakeCandidate:
    """One ``add_2``-bearing ``[role=button]`` candidate in the fake editor DOM.

    Records whether it was clicked.  ``inner_text`` returns the candidate's
    accessible text — icon-only candidates reduce to exactly ``"add_2"``; the
    decoy carries a hidden label so its inner_text is longer.
    """

    def __init__(self, *, inner_text: str, visible: bool = True) -> None:
        self._inner_text = inner_text
        self._visible = visible
        self.clicked = False
        self.click = AsyncMock(side_effect=self._record_click)
        if visible:
            self.wait_for = AsyncMock()
        else:
            self.wait_for = AsyncMock(side_effect=Exception("not visible"))

    async def _record_click(self, *_a: Any, **_kw: Any) -> None:
        self.clicked = True

    async def inner_text(self) -> str:
        return self._inner_text


class _FakeSlotLocator:
    """Fake locator over the slot-add candidate list.

    Models the live DOM: index 0 is the icon-only slot-add ``[role=button]``
    (inner_text == ``"add_2"``), index 1 is the decoy ``<button>`` whose hidden
    text label lengthens its inner_text.  This is the inverse of the old
    ``.nth(1)`` heuristic — a correct implementation MUST pick index 0 here, so
    the old code (which clicked nth(1)) would fail this test.
    """

    def __init__(self, candidates: list[_FakeCandidate]) -> None:
        self._candidates = candidates
        # ``.first`` is the readiness anchor — wires to candidate 0.
        self.first = candidates[0] if candidates else _FakeCandidate(inner_text="")

    async def count(self) -> int:
        return len(self._candidates)

    def nth(self, n: int) -> _FakeCandidate:
        return self._candidates[n]


def _make_slot_add_page(candidates: list[_FakeCandidate]) -> MagicMock:
    """Fake page whose slot-add selector resolves to ``candidates``.

    Any other locator returns a benign MagicMock so unrelated calls don't crash.
    """
    page = MagicMock()
    page.url = "https://labs.google/fx/pt/tools/flow/project/p1/character/e1"
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"")

    slot_sel = UiAutomationTransport._CHARACTER_SLOT_ADD_SELECTOR
    slot_loc = _FakeSlotLocator(candidates)

    def _locator(sel: str) -> Any:
        if sel == slot_sel:
            return slot_loc
        other = MagicMock()
        other.first = other
        other.wait_for = AsyncMock(side_effect=Exception("not visible"))
        other.click = AsyncMock()
        return other

    page.locator = MagicMock(side_effect=_locator)
    return page


class TestClickCharacterSlotAddSelector:
    """Proves the icon-only ``[role=button]`` is chosen, not ``.nth(1)``."""

    @pytest.mark.asyncio
    async def test_activates_current_body_mode_and_waits_for_face_reference(self) -> None:
        """Live 2026-07-26: Create Body reuses one Slate box and mounts the
        generated face reference; both signals are language-independent icons."""
        body_mode = _FakeCandidate(inner_text="accessibility_new Create Body")
        face_reference = _FakeCandidate(inner_text="cancel")
        legacy = _FakeCandidate(inner_text="add_2")
        page = MagicMock()
        page.url = "https://labs.google/fx/en/tools/flow/project/p1/character/e1"
        page.wait_for_timeout = AsyncMock()
        page.screenshot = AsyncMock(return_value=b"")

        body_loc = _FakeSlotLocator([body_mode])
        reference_loc = _FakeSlotLocator([face_reference])
        legacy_loc = _FakeSlotLocator([legacy])

        def _locator(sel: str) -> Any:
            if sel == UiAutomationTransport._CHARACTER_BODY_MODE_SELECTOR:
                return body_loc
            if sel == UiAutomationTransport._CHARACTER_BODY_REFERENCE_SELECTOR:
                return reference_loc
            if sel == UiAutomationTransport._CHARACTER_SLOT_ADD_SELECTOR:
                return legacy_loc
            raise AssertionError(f"unexpected selector: {sel}")

        page.locator = MagicMock(side_effect=_locator)
        t = _make_transport(page=page)

        shared_body_box = await t._click_character_slot_add(page)  # type: ignore[attr-defined]

        assert shared_body_box is True
        assert body_mode.clicked
        face_reference.wait_for.assert_awaited_once()
        assert not legacy.clicked

    @pytest.mark.asyncio
    async def test_clicks_icon_only_candidate_not_decoy(self) -> None:
        # Live-DOM order: [0] = icon-only slot-add, [1] = labeled decoy button.
        icon_only = _FakeCandidate(inner_text="add_2")
        # Decoy: <i>add_2</i> + hidden <span> label → inner_text has extra text.
        decoy = _FakeCandidate(inner_text="add_2 Adicionar imagem do personagem")
        page = _make_slot_add_page([icon_only, decoy])
        t = _make_transport(page=page)

        await t._click_character_slot_add(page)  # type: ignore[attr-defined]

        assert icon_only.clicked, "the icon-only slot-add [role=button] must be clicked"
        assert not decoy.clicked, (
            "the labeled decoy must NOT be clicked; the old .nth(1) heuristic "
            "would have clicked it (it is at index 1)"
        )

    @pytest.mark.asyncio
    async def test_picks_icon_only_when_decoy_is_first(self) -> None:
        # Robustness: even if the decoy renders FIRST, inner_text filtering wins
        # (pure positional .nth(0) or .nth(1) would both be wrong here).
        decoy = _FakeCandidate(inner_text="add_2 some hidden label")
        icon_only = _FakeCandidate(inner_text="add_2")
        page = _make_slot_add_page([decoy, icon_only])
        t = _make_transport(page=page)

        await t._click_character_slot_add(page)  # type: ignore[attr-defined]

        assert icon_only.clicked, "must select the icon-only candidate by inner_text, not position"
        assert not decoy.clicked, "labeled decoy must never be clicked"

    @pytest.mark.asyncio
    async def test_no_icon_only_candidate_is_non_fatal(self) -> None:
        # Only a labeled decoy present → no icon-only match → log + skip, no raise.
        decoy = _FakeCandidate(inner_text="add_2 label")
        page = _make_slot_add_page([decoy])
        t = _make_transport(page=page)

        # MUST NOT raise — best-effort slot-add.
        await t._click_character_slot_add(page)  # type: ignore[attr-defined]

        assert not decoy.clicked, "no icon-only candidate → nothing clicked"


# ---------------------------------------------------------------------------
# Tests: _submit_body_prompt — self-contained triptych prompt (locale-safe)
# ---------------------------------------------------------------------------


class _FakeSlateBox:
    """One Slate prompt box: focusable via click, holding mutable text.

    ``click`` records focus on the page; the page-level keyboard fakes route
    Ctrl+A/Delete (clear) and ``insert_text`` (type) to the FOCUSED box —
    Playwright's real semantics, so a wrong-box click corrupts the wrong box
    exactly like the live editor.  ``inner_text`` reads are recorded in
    ``page.events`` so tests can assert the pre-fill is never read before the
    replacement.
    """

    def __init__(self, page: MagicMock, *, text: str, name: str) -> None:
        self._page = page
        self.text = text
        self.name = name
        self.wait_for = AsyncMock()
        self.click = AsyncMock(side_effect=lambda: setattr(page, "focused_box", self))

    async def inner_text(self) -> str:
        self._page.events.append(("read", self.name))
        return self.text

    async def evaluate(self, _expression: str) -> bool:
        self._page.events.append(("focus-check", self.name))
        return self._page.focused_box is self


class _FakeSlateBoxList:
    """The locator for ``PROMPT_INPUT_SELECTORS[0]``: N mounted Slate boxes."""

    def __init__(self, boxes: list[_FakeSlateBox]) -> None:
        self.boxes = boxes

    async def count(self) -> int:
        return len(self.boxes)

    def nth(self, index: int) -> _FakeSlateBox:
        return self.boxes[index]

    @property
    def first(self) -> _FakeSlateBox:
        return self.boxes[0]


_PORTRAIT_PREFILL = "portrait of a heroine"


def _make_body_prompt_page(
    *,
    body_prefill: str = "localized Flow triptych template",
    box_count: int = 2,
) -> tuple[MagicMock, list[_FakeSlateBox], list[str], list[str]]:
    """Fake two-composer character-editor page (Portrait / Create Body).

    Returns ``(page, boxes, inserted, submit_clicks)``: ``boxes[0]`` is the
    PORTRAIT prompt box (pre-filled with the portrait prompt), ``boxes[1]``
    (when ``box_count >= 2``) the body composer's own box pre-filled with
    Flow's localized template.  ``inserted`` collects every
    ``keyboard.insert_text`` argument; typing/clearing are routed to the
    currently-FOCUSED box; ``submit_clicks`` records submit-button clicks.
    """
    from gflow_cli.api.transports.ui_automation import (
        PROMPT_INPUT_SELECTORS,
        SUBMIT_BUTTON_SELECTORS,
    )

    page = MagicMock()
    page.url = "https://labs.google/fx/pt/tools/flow/project/p1/character/e1"
    page.events = []
    page.focused_box = None
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"")
    page.keyboard = MagicMock()

    inserted: list[str] = []

    def _press(key: str) -> None:
        # Ctrl+A selects all inside the focused Slate box; Delete clears it.
        if key == "Delete" and page.focused_box is not None:
            page.focused_box.text = ""

    def _insert(txt: str) -> None:
        inserted.append(txt)
        page.events.append(("insert", txt))
        if page.focused_box is not None:
            page.focused_box.text += txt

    page.keyboard.press = AsyncMock(side_effect=_press)
    page.keyboard.insert_text = AsyncMock(side_effect=_insert)

    portrait = _FakeSlateBox(page, text=_PORTRAIT_PREFILL, name="portrait")
    body = _FakeSlateBox(page, text=body_prefill, name="body")
    boxes = [portrait, body][:box_count]
    box_list = _FakeSlateBoxList(boxes)

    prompt_sel = PROMPT_INPUT_SELECTORS[0]
    submit_sels = set(SUBMIT_BUTTON_SELECTORS)
    submit_clicks: list[str] = []

    def _locator(sel: str) -> Any:
        if sel == prompt_sel:
            return box_list
        loc = MagicMock()
        loc.first = loc
        if sel in submit_sels:
            loc.wait_for = AsyncMock()
            loc.click = AsyncMock(side_effect=lambda: submit_clicks.append(sel))
        else:
            loc.wait_for = AsyncMock(side_effect=Exception("not visible"))
            loc.click = AsyncMock()
        return loc

    page.locator = MagicMock(side_effect=_locator)
    return page, boxes, inserted, submit_clicks


class TestSubmitBodyPrompt:
    """Self-contained triptych behavior — typed into the body slot's OWN box.

    The body slot must submit ``_BODY_TRIPTYCH_PREAMBLE + body_description``
    regardless of whatever Flow pre-filled the box with — it must NOT depend on
    reading/parsing the pre-filled text (locale-safe, timing-safe) — and it must
    NEVER touch the PORTRAIT prompt box (live corruption 2026-07-25, 0.43.0:
    the triptych prompt replaced the portrait prompt in the two-button
    Portrait / Create Body editor).
    """

    @pytest.mark.asyncio
    async def test_submits_self_contained_prompt_replacing_prefill(self) -> None:
        """The submitted text equals PREAMBLE + description, REPLACING whatever
        Flow pre-filled the body box with (here a localized template string)."""
        from gflow_cli.api.transports.ui_automation import _BODY_TRIPTYCH_PREAMBLE

        # The body box is pre-filled with a (Portuguese) Flow template carrying
        # a bracketed placeholder — none of which must leak into the submission.
        flow_prefill = (
            "Tríptico de corpo inteiro em três ângulos diferentes: de frente, "
            "visualização lateral (3/4) e de costas. fundo branco sólido. "
            "[DESCREVA O CORPO E A ROUPA]"
        )
        page, _boxes, inserted, _clicks = _make_body_prompt_page(body_prefill=flow_prefill)
        t = _make_transport(page=page)

        await t._submit_body_prompt(  # type: ignore[attr-defined]
            page, "red raincoat, rubber boots", boxes_before=1
        )

        expected = _BODY_TRIPTYCH_PREAMBLE + "red raincoat, rubber boots"
        assert inserted == [expected], (
            f"submitted text must be the self-contained triptych prompt + "
            f"description, independent of the pre-fill; got {inserted}"
        )
        # None of Flow's pre-filled template leaked through.
        assert "Tríptico" not in inserted[0]
        assert "[DESCREVA O CORPO E A ROUPA]" not in inserted[0]
        assert "red raincoat, rubber boots" in inserted[0]

    @pytest.mark.asyncio
    async def test_triptych_instruction_always_present(self) -> None:
        """The front/side/back triptych instruction is guaranteed in the
        submitted prompt regardless of what the box was pre-filled with."""
        # Pre-fill the box with an empty string AND a totally different language
        # template across two runs — the triptych instruction must appear both
        # times because it comes from gflow, not from the box.
        for prefill in ("", "Some unrelated localized template text."):
            page, _boxes, inserted, _clicks = _make_body_prompt_page(body_prefill=prefill)
            t = _make_transport(page=page)

            await t._submit_body_prompt(  # type: ignore[attr-defined]
                page, "warrior outfit", boxes_before=1
            )

            submitted = inserted[0]
            assert "front, side (3/4), and back" in submitted, (
                f"front/side/back triptych instruction must always be present; got {submitted!r}"
            )
            assert submitted.endswith("warrior outfit")

    @pytest.mark.asyncio
    async def test_types_into_body_box_not_portrait(self) -> None:
        """Regression (live 2026-07-25, 0.43.0): the triptych prompt must land
        in the body slot's OWN box; the portrait prompt box stays untouched."""
        from gflow_cli.api.transports.ui_automation import _BODY_TRIPTYCH_PREAMBLE

        page, boxes, _inserted, submit_clicks = _make_body_prompt_page()
        portrait, body = boxes
        t = _make_transport(page=page)

        await t._submit_body_prompt(page, "red raincoat", boxes_before=1)  # type: ignore[attr-defined]

        assert portrait.text == _PORTRAIT_PREFILL, (
            f"portrait prompt box must NOT be overwritten; got {portrait.text!r}"
        )
        assert body.text == _BODY_TRIPTYCH_PREAMBLE + "red raincoat"
        assert len(submit_clicks) == 1, "the body prompt must be submitted exactly once"

    @pytest.mark.asyncio
    async def test_types_into_shared_box_after_body_mode_settles(self) -> None:
        """Live 2026-07-26: Create Body reuses the one mounted Slate editor;
        the attached-face settle signal makes that shared box safe to use."""
        from gflow_cli.api.transports.ui_automation import _BODY_TRIPTYCH_PREAMBLE

        page, boxes, inserted, submit_clicks = _make_body_prompt_page(box_count=1)
        shared_box = boxes[0]
        shared_box.text = "Describe body and outfit...."
        shared_box.name = "shared-body"
        t = _make_transport(page=page)

        await t._submit_body_prompt(  # type: ignore[attr-defined]
            page,
            "red raincoat",
            boxes_before=1,
            shared_body_box=True,
        )

        assert inserted == [_BODY_TRIPTYCH_PREAMBLE + "red raincoat"]
        assert shared_box.text == _BODY_TRIPTYCH_PREAMBLE + "red raincoat"
        assert len(submit_clicks) == 1

    @pytest.mark.asyncio
    async def test_raises_when_body_box_never_mounts(self, monkeypatch: Any) -> None:
        """If no NEW box mounts after slot-add, the body step must ABORT before
        typing — typing would land in the portrait box (the live corruption)."""
        from gflow_cli.api.transports import ui_automation as _uia

        monkeypatch.setattr(_uia, "_BODY_SLOT_MOUNT_TIMEOUT_S", 0.05)
        page, _boxes, inserted, submit_clicks = _make_body_prompt_page(box_count=1)
        t = _make_transport(page=page)

        with pytest.raises(RuntimeError, match="portrait"):
            await t._submit_body_prompt(  # type: ignore[attr-defined]
                page, "warrior outfit", boxes_before=1
            )

        assert inserted == [], "nothing may be typed when the body box is missing"
        assert submit_clicks == [], "nothing may be submitted when the body box is missing"

    @pytest.mark.asyncio
    async def test_waits_for_body_box_to_mount(self) -> None:
        """The mount gate POLLS: a body box appearing a beat after slot-add
        (mode switch settling) is bound once mounted — no fixed-sleep race."""
        page, boxes, _inserted, submit_clicks = _make_body_prompt_page(box_count=1)
        body = _FakeSlateBox(page, text="localized template", name="body")

        ticks: list[int] = []

        def _tick(_ms: int) -> None:
            ticks.append(_ms)
            if len(ticks) == 2 and len(boxes) == 1:
                boxes.append(body)  # the body composer mounts on the 2nd poll

        page.wait_for_timeout = AsyncMock(side_effect=_tick)
        t = _make_transport(page=page)

        await t._submit_body_prompt(page, "warrior outfit", boxes_before=1)  # type: ignore[attr-defined]

        assert "warrior outfit" in body.text
        assert len(submit_clicks) == 1

    @pytest.mark.asyncio
    async def test_aborts_before_edit_when_body_box_does_not_retain_focus(self) -> None:
        """If Flow bounces focus back to the portrait composer, abort before
        any destructive input because Flow autosaves prompt edits via PATCH."""
        page, boxes, inserted, submit_clicks = _make_body_prompt_page()
        portrait, body = boxes
        # Simulate the live race: clicking the body box leaves focus on PORTRAIT.
        body.click = AsyncMock(side_effect=lambda: setattr(page, "focused_box", portrait))
        t = _make_transport(page=page)

        with pytest.raises(RuntimeError, match="wrong prompt box"):
            await t._submit_body_prompt(  # type: ignore[attr-defined]
                page, "red raincoat", boxes_before=1
            )

        assert portrait.text == _PORTRAIT_PREFILL, "wrong focus must not mutate the portrait"
        assert body.text == "localized Flow triptych template"
        assert inserted == [], "must abort before typing into any prompt box"
        assert submit_clicks == [], "must abort BEFORE submit on a wrong-box landing"

    @pytest.mark.asyncio
    async def test_aborts_before_edit_when_focus_cannot_be_verified(self) -> None:
        """A detached/unstable body box must fail closed before autosaved edits."""
        page, boxes, inserted, submit_clicks = _make_body_prompt_page()
        portrait, body = boxes
        body.evaluate = AsyncMock(side_effect=RuntimeError("detached during focus check"))
        t = _make_transport(page=page)

        with pytest.raises(RuntimeError, match="Could not verify body prompt focus"):
            await t._submit_body_prompt(  # type: ignore[attr-defined]
                page, "red raincoat", boxes_before=1
            )

        assert portrait.text == _PORTRAIT_PREFILL
        assert inserted == []
        assert submit_clicks == []

    @pytest.mark.asyncio
    async def test_prefill_read_only_after_replacement(self) -> None:
        """The body path must not DEPEND on the pre-filled template: no box is
        read before the replacement text is inserted (the post-type readback
        guard is the only reader)."""
        page, _boxes, _inserted, _clicks = _make_body_prompt_page()
        t = _make_transport(page=page)

        await t._submit_body_prompt(page, "body desc", boxes_before=1)  # type: ignore[attr-defined]

        events = page.events
        insert_at = next(i for i, ev in enumerate(events) if ev[0] == "insert")
        early_reads = [ev for ev in events[:insert_at] if ev[0] == "read"]
        assert early_reads == [], (
            f"pre-fill must never be read before the replacement; events={events}"
        )

    @pytest.mark.asyncio
    async def test_logs_self_contained_template(self, monkeypatch: Any) -> None:
        """``ui_automation.body_prompt_templated`` is logged with
        ``template='self_contained'`` and a length — NO body text."""
        from gflow_cli.api.transports import ui_automation as _uia

        events: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            _uia.log,
            "info",
            lambda event, **kw: events.append((event, kw)),
        )

        page, _boxes, _inserted, _clicks = _make_body_prompt_page(body_prefill="template")
        t = _make_transport(page=page)

        await t._submit_body_prompt(page, "red raincoat", boxes_before=1)  # type: ignore[attr-defined]

        templated = [kw for ev, kw in events if ev == "ui_automation.body_prompt_templated"]
        assert len(templated) == 1, f"templated event must be logged once; got {events}"
        kw = templated[0]
        assert kw.get("template") == "self_contained"
        assert isinstance(kw.get("prompt_len"), int) and kw["prompt_len"] > 0
        # No raw body text in the log payload.
        assert "red raincoat" not in str(kw)
