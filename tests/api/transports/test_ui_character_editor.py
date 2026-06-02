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


# ---------------------------------------------------------------------------
# Regression: real CharacterImageRequest field access (issue caught by live e2e)
# ---------------------------------------------------------------------------


class TestCharacterImageRequestFieldContract:
    """Lock the wire between ``CharacterImageRequest`` and the settings panel.

    The live crash was ``'CharacterImageRequest' object has no attribute
    'count'``: ``_generate_character_images_locked`` was written against
    ``GenerateImageRequest`` (``.count``; enum ``.aspect``/``.model``) but the
    real caller (``client.generate_character_images``) passes a
    ``CharacterImageRequest`` (no ``.count``; CLI-*string* ``.aspect``/
    ``.model``).  All 942 prior tests missed it because they (a) passed a
    ``GenerateImageRequest`` and (b) mocked ``_configure_generation_settings``
    away — so the real ``request.count`` / ``request.aspect`` access never ran.

    These tests build a REAL ``CharacterImageRequest`` and DO NOT mock
    ``_configure_generation_settings`` away — they replace it with a spy that
    records the exact arguments the locked method computes, forcing the real
    attribute access (``request.aspect``, ``request.model``) and the real
    ``Model.from_cli`` conversion to execute.
    """

    @staticmethod
    def _build_transport_with_config_spy(
        *,
        fixture: dict[str, Any],
        project_id: str = "proj-1",
    ) -> tuple[UiAutomationTransport, AsyncMock, list[Any]]:
        """Wire a transport where ``_configure_generation_settings`` is a SPY.

        Returns ``(transport, config_spy, await_count_calls)`` where
        ``config_spy`` records every ``(args, kwargs)`` it was called with and
        ``await_count_calls`` collects the ``expected_count`` passed to
        ``_await_captured`` (the second real consumer of the per-slot count).
        Only the Playwright/browser-touching steps are stubbed; the panel
        config "skips gracefully" because the spy never touches a browser.
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

        # The SPY: real signature is _configure_generation_settings(page,
        # aspect_cli, count, *, model=..., ...).  We record everything and
        # return None — no browser, "skips gracefully".
        config_spy = AsyncMock(return_value=None)
        t._configure_generation_settings = config_spy  # type: ignore[attr-defined]

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

        return t, config_spy, await_count_calls

    @pytest.mark.asyncio
    async def test_does_not_raise_attribute_error_and_translates_fields(self) -> None:
        """The live crash repro: a REAL CharacterImageRequest must drive the
        locked method without AttributeError, and the settings panel must
        receive the CLI aspect string, count=1, and a Model enum (not a raw
        CharacterImageRequest string attribute)."""
        fixture = _load_fixture()
        t, config_spy, await_count_calls = self._build_transport_with_config_spy(fixture=fixture)

        # REAL DTO — CLI-string aspect/model, NO .count attribute at all.
        req = CharacterImageRequest(prompt="x", aspect="9:16", model="narwhal")
        assert not hasattr(req, "count"), "guard: CharacterImageRequest must NOT have .count"

        # MUST NOT raise AttributeError('CharacterImageRequest' ... 'count').
        images, workflows = await t.generate_character_images(
            project_id="proj-1",
            entity_id="e1",
            request=req,
            image_reference_index=0,
            locale="pt",
        )

        # _configure_generation_settings(page, aspect_cli, count, *, model=...)
        config_spy.assert_called_once()
        call = config_spy.call_args
        # positional: (page, aspect_cli, count)
        aspect_cli = call.args[1]
        count = call.args[2]
        model = call.kwargs["model"]

        assert aspect_cli == "9:16", f"CLI aspect string must pass through; got {aspect_cli!r}"
        assert count == 1, f"character gen is exactly one image per slot; got {count!r}"
        assert model is Model.NARWHAL, (
            f"model must be the Model enum from Model.from_cli('narwhal'); "
            f"got {model!r} (a raw string means the old GenerateImageRequest path)"
        )
        assert not isinstance(model, str) or isinstance(model, Model), (
            "model must NOT be the raw CharacterImageRequest.model string"
        )

        # _await_captured must also receive the per-slot count of 1.
        assert await_count_calls == [1], (
            f"expected_count passed to _await_captured must be 1; got {await_count_calls!r}"
        )
        assert len(images) >= 1
        assert len(workflows) >= 1

    @pytest.mark.asyncio
    async def test_unknown_model_alias_falls_back_to_none(self) -> None:
        """An unknown --model alias must NOT crash; model selection is skipped
        (model=None → Flow default), honoring best-effort settings."""
        fixture = _load_fixture()
        t, config_spy, _ = self._build_transport_with_config_spy(fixture=fixture)

        req = CharacterImageRequest(prompt="x", aspect="9:16", model="totally-unknown-model")
        # MUST NOT raise ValueError from Model.from_cli.
        await t.generate_character_images(
            project_id="proj-1",
            entity_id="e1",
            request=req,
            image_reference_index=0,
            locale="pt",
        )
        assert config_spy.call_args.kwargs["model"] is None, (
            "unknown alias must fall back to model=None (Flow default), not crash"
        )


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
        other.wait_for = AsyncMock()
        other.click = AsyncMock()
        return other

    page.locator = MagicMock(side_effect=_locator)
    return page


class TestClickCharacterSlotAddSelector:
    """Proves the icon-only ``[role=button]`` is chosen, not ``.nth(1)``."""

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
