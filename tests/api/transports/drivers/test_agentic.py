"""Unit tests for AgenticFlowUiDriver (Task 3).

Covers: settings → prompt composition; Slate keyboard send (not fill);
await_images dedup (9 nodes → 3 UUIDs); URL construction; content-policy
block; flag-only present → NOT a block; timeout with partial;
video methods raise FlowAgentUiError.

All tests use a mock Page — no live browser required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.image import AgentInstruction, Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports.drivers import agentic as agentic_mod
from gflow_cli.api.transports.drivers.agentic import (
    _MEDIA_REDIRECT_BASE,
    AgenticFlowUiDriver,
    _extract_uuids,
)
from gflow_cli.errors import (
    ContentPolicyError,
    FlowAgentUiError,
    MediaAttributionError,
    TransportTimeoutError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_A = "aaaaaaaa-0000-0000-0000-000000000001"
UUID_B = "bbbbbbbb-0000-0000-0000-000000000002"
UUID_C = "cccccccc-0000-0000-0000-000000000003"

# Nine src strings: 3 distinct UUIDs × 3 node variants each (full-res, thumbnail, preview).
_NINE_SRCS = [
    f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}",
    f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL",
    f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}&mediaUrlType=OTHER",
    f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_B}",
    f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_B}&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL",
    f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_B}&mediaUrlType=OTHER",
    f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_C}",
    f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_C}&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL",
    f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_C}&mediaUrlType=OTHER",
]


def _make_image_request(
    *,
    count: int = 1,
    aspect: Aspect = Aspect.PORTRAIT,
    model: Model = Model.NARWHAL,
) -> GenerateImageRequest:
    return GenerateImageRequest(prompt="a cat", count=count, aspect=aspect, model=model)


def _mock_page_no_settings_panel() -> MagicMock:
    """Page where the Agent settings panel's tune button is absent (count 0) —
    exercises the graceful-skip path so configure_image_settings tests don't
    need to model the full settings-panel click sequence."""
    page = MagicMock()
    locator_mock = MagicMock()
    locator_mock.first = locator_mock
    locator_mock.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=locator_mock)
    return page


def _fake_page_no_policy(
    *,
    initial_srcs: list[str],
    new_srcs: list[str],
    second_baseline_srcs: list[str] | None = None,
) -> MagicMock:
    """Page that returns ``initial_srcs`` on the first TWO ``img`` scrapes
    (the baseline-settle pair — see ``await_images`` docstring) and
    ``new_srcs`` on every scrape after.

    ``second_baseline_srcs`` overrides just the second baseline call, for
    tests exercising the baseline-union behaviour (a UUID that only renders
    on the second baseline pass must still count as baseline, not "new").
    Defaults to ``initial_srcs`` so existing single-baseline callers are
    unaffected by the two-pass settle.

    Branches on the selector: ``img`` calls drive the scrape; the policy
    alert/dialog region scan returns no regions (no content-policy signal).
    """
    img_calls = 0
    second_baseline = initial_srcs if second_baseline_srcs is None else second_baseline_srcs

    async def _eval_on_selector_all(selector: str, expr: str) -> list[str]:
        nonlocal img_calls
        if selector == "img":
            img_calls += 1
            if img_calls == 1:
                return initial_srcs
            if img_calls == 2:
                return second_baseline
            return new_srcs
        return []  # policy region scan: no alert/dialog regions present

    page = MagicMock()
    page.eval_on_selector_all = _eval_on_selector_all

    count_mock = AsyncMock(return_value=0)
    locator_mock = MagicMock()
    locator_mock.count = count_mock
    page.locator = MagicMock(return_value=locator_mock)

    return page


# ---------------------------------------------------------------------------
# _extract_uuids
# ---------------------------------------------------------------------------


def test_extract_uuids_deduplicates() -> None:
    """Nine src strings with 3 distinct UUIDs → exactly 3 UUIDs returned."""
    result = _extract_uuids(_NINE_SRCS)
    assert result == {UUID_A, UUID_B, UUID_C}


def test_extract_uuids_empty() -> None:
    assert _extract_uuids([]) == set()


def test_extract_uuids_ignores_non_media_urls() -> None:
    assert _extract_uuids(["https://example.com/image.png"]) == set()


# ---------------------------------------------------------------------------
# Prompt directive composition
# ---------------------------------------------------------------------------


def test_compose_directive_with_aspect() -> None:
    directive = AgenticFlowUiDriver._compose_directive(4, "16:9", "a red apple")
    assert directive == "Make me 4 pictures of a red apple in a 16:9 aspect ratio."


def test_compose_directive_no_aspect() -> None:
    directive = AgenticFlowUiDriver._compose_directive(1, None, "a red apple")
    assert directive == "Make me a picture of a red apple."


def test_compose_directive_is_conversational_not_imperative() -> None:
    """Regression guard for the instructions spike: the directive must NOT use
    the imperative ``Generate N images:`` form, which the agent passes to the
    image tool verbatim and thereby bypasses the project-brief instruction
    cards. It must read as a natural request so the agent's reasoning step folds
    enabled cards into the tool prompt."""
    directive = AgenticFlowUiDriver._compose_directive(2, "16:9", "a cat")
    assert "Generate" not in directive
    assert not directive.startswith("Generate")
    assert directive.lower().startswith("make me")


def test_compose_directive_plural_singular() -> None:
    assert "a picture of" in AgenticFlowUiDriver._compose_directive(1, None, "x")
    assert "2 pictures of" in AgenticFlowUiDriver._compose_directive(2, None, "x")


# ---------------------------------------------------------------------------
# configure_image_settings → stores values for send_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configure_image_settings_stores_count_and_aspect() -> None:
    driver = AgenticFlowUiDriver()
    page = _mock_page_no_settings_panel()
    req = _make_image_request(count=4, aspect=Aspect.LANDSCAPE)
    await driver.configure_image_settings(page, req)
    assert driver._pending_count == 4  # noqa: SLF001
    assert driver._pending_aspect == "16:9"  # noqa: SLF001


@pytest.mark.asyncio
async def test_configure_image_settings_portrait_aspect() -> None:
    driver = AgenticFlowUiDriver()
    req = _make_image_request(count=1, aspect=Aspect.PORTRAIT)
    await driver.configure_image_settings(_mock_page_no_settings_panel(), req)
    assert driver._pending_aspect == "9:16"  # noqa: SLF001


@pytest.mark.asyncio
async def test_configure_image_settings_square_aspect() -> None:
    driver = AgenticFlowUiDriver()
    req = _make_image_request(count=2, aspect=Aspect.SQUARE)
    await driver.configure_image_settings(_mock_page_no_settings_panel(), req)
    assert driver._pending_aspect == "1:1"  # noqa: SLF001


# ---------------------------------------------------------------------------
# _enforce_image_count_via_settings_panel — issue #313 fallback (reworked)
# ---------------------------------------------------------------------------


def _mock_settings_panel_page(
    *,
    panel_already_open: bool = False,
    tune_button_present: bool = True,
    tab_count: int = 8,
    target_initially_selected: bool = False,
    converges_on_click: bool = True,
    save_button_found: bool = True,
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Build a page mock for the reworked enforcement method.

    Returns (page, tune_btn, target_tab, back_btn) so tests can assert on
    click() call counts for specific elements. Patches
    ``UiAutomationTransport._is_settings_panel_open`` and
    ``_count_tabs_locator`` via ``unittest.mock.patch`` in each test (they
    are imported inside the method under test via a late import, so the
    patch target is the real module, not the local re-export).
    """
    tune_btn = MagicMock()
    tune_btn.count = AsyncMock(return_value=1 if tune_button_present else 0)
    tune_btn.click = AsyncMock()

    selected_state = {"value": target_initially_selected}

    async def _get_attribute(name: str) -> str | None:
        if name != "aria-selected":
            return None
        return "true" if selected_state["value"] else "false"

    async def _click_target(**_kwargs: object) -> None:
        if converges_on_click:
            selected_state["value"] = True

    target_tab = MagicMock()
    target_tab.get_attribute = AsyncMock(side_effect=_get_attribute)
    target_tab.click = AsyncMock(side_effect=_click_target)

    tabs = MagicMock()
    tabs.count = AsyncMock(return_value=tab_count)
    tabs.nth = MagicMock(return_value=target_tab)

    back_btn = MagicMock()
    back_btn.count = AsyncMock(return_value=1)
    back_btn.click = AsyncMock()

    save_btn = MagicMock()
    save_btn.click = AsyncMock()

    def _locator(selector: str) -> MagicMock:
        result = MagicMock()
        if "arrow_back" in selector:
            result.first = back_btn
        elif "data-gflow-save-target" in selector:
            result.first = save_btn
        elif "tune" in selector:
            result.first = tune_btn
        else:
            result.first = MagicMock()
        return result

    page = MagicMock()
    page.locator = MagicMock(side_effect=_locator)
    page.evaluate = AsyncMock(return_value=save_button_found)
    page.wait_for_timeout = AsyncMock()  # method awaits it; MagicMock isn't awaitable

    return page, tune_btn, target_tab, back_btn, tabs


@pytest.mark.asyncio
async def test_enforce_count_opens_panel_clicks_and_saves() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(
        panel_already_open=False,
        target_initially_selected=False,
    )
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 3)  # noqa: SLF001
    tune_btn.click.assert_awaited_once()
    target_tab.click.assert_awaited_once()
    page.evaluate.assert_awaited_once()
    back_btn.click.assert_not_awaited()  # Save auto-closes; no separate back-arrow click needed


@pytest.mark.asyncio
async def test_enforce_count_skips_click_and_save_when_already_correct() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(
        target_initially_selected=True,
    )
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 1)  # noqa: SLF001
    target_tab.click.assert_not_awaited()
    page.evaluate.assert_not_awaited()  # no Save needed
    back_btn.click.assert_awaited_once()  # but the panel we opened must still be closed


@pytest.mark.asyncio
async def test_enforce_count_does_not_reopen_already_open_panel() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(
        target_initially_selected=True,
    )
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 1)  # noqa: SLF001
    tune_btn.click.assert_not_awaited()  # already open — must not toggle it closed


@pytest.mark.asyncio
async def test_enforce_count_closes_panel_on_target_not_found() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(tab_count=2)
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 4)  # noqa: SLF001
    back_btn.click.assert_awaited_once()  # panel opened, target missing — must still close


@pytest.mark.asyncio
async def test_enforce_count_closes_panel_when_click_never_converges() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(
        converges_on_click=False,
    )
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 2)  # noqa: SLF001
    assert target_tab.click.await_count == 3  # noqa: PLR2004  # exhausted all 3 attempts
    back_btn.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_count_closes_panel_when_save_not_found() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(
        save_button_found=False,
    )
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 2)  # noqa: SLF001
    target_tab.click.assert_awaited_once()
    back_btn.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_enforce_count_skips_gracefully_when_tune_button_absent() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page(
        tune_button_present=False,
    )
    with patch(
        "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
        new=AsyncMock(return_value=False),
    ):
        await driver._enforce_image_count_via_settings_panel(page, 1)  # noqa: SLF001
    # No exception — graceful skip before the panel was ever touched.
    back_btn.click.assert_not_awaited()


@pytest.mark.asyncio
async def test_enforce_count_swallows_exceptions_and_still_closes_panel() -> None:
    driver = AgenticFlowUiDriver()
    page, tune_btn, target_tab, back_btn, tabs = _mock_settings_panel_page()
    tabs.count = AsyncMock(side_effect=RuntimeError("boom"))
    with (
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport._is_settings_panel_open",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "gflow_cli.api.transports.ui_automation._count_tabs_locator",
            return_value=tabs,
        ),
    ):
        # Must not raise.
        await driver._enforce_image_count_via_settings_panel(page, 1)  # noqa: SLF001
    back_btn.click.assert_awaited_once()  # even the exception path closes the panel


@pytest.mark.asyncio
async def test_close_agent_settings_panel_is_noop_when_already_closed() -> None:
    page = MagicMock()
    absent = MagicMock()
    absent.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=absent)
    await AgenticFlowUiDriver._close_agent_settings_panel(page)  # noqa: SLF001
    # No exception, no click attempted on a nonexistent element.


@pytest.mark.asyncio
async def test_close_agent_settings_panel_swallows_click_errors() -> None:
    page = MagicMock()
    back_btn = MagicMock()
    back_btn.count = AsyncMock(return_value=1)
    back_btn.click = AsyncMock(side_effect=RuntimeError("boom"))
    result = MagicMock()
    result.first = back_btn
    page.locator = MagicMock(return_value=result)
    # Must not raise.
    await AgenticFlowUiDriver._close_agent_settings_panel(page)  # noqa: SLF001


# ---------------------------------------------------------------------------
# switch_to_image_mode — no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_to_image_mode_is_noop() -> None:
    driver = AgenticFlowUiDriver()
    # Should not raise, return None.
    result = await driver.switch_to_image_mode(MagicMock())
    assert result is None


# ---------------------------------------------------------------------------
# send_prompt — uses keyboard.insert_text, not fill()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_prompt_uses_keyboard_not_fill() -> None:
    """Slate ignores fill() — send_prompt must use keyboard.insert_text."""
    driver = AgenticFlowUiDriver()
    driver._pending_count = 2  # noqa: SLF001
    driver._pending_aspect = "16:9"  # noqa: SLF001

    page = MagicMock()
    keyboard = MagicMock()
    keyboard.press = AsyncMock()
    keyboard.insert_text = AsyncMock()
    page.keyboard = keyboard

    composer_loc = MagicMock()
    composer_loc.wait_for = AsyncMock()
    composer_loc.click = AsyncMock()

    # The submit button is found on first selector.
    submit_btn = MagicMock()
    submit_btn.count = AsyncMock(return_value=1)
    submit_btn.click = AsyncMock()

    def _locator(sel: str) -> MagicMock:
        if "textbox" in sel or "slate" in sel:
            return MagicMock(first=composer_loc)
        loc = MagicMock()
        loc.first = submit_btn
        return loc

    page.locator = MagicMock(side_effect=_locator)

    await driver.send_prompt(page, "a red apple")

    # fill() must NOT have been called.
    assert not hasattr(page, "fill") or not page.fill.called
    # insert_text must have been called with the directive.
    keyboard.insert_text.assert_awaited_once()
    call_args = keyboard.insert_text.call_args
    typed_text: str = call_args[0][0]
    assert "Make me 2 pictures of a red apple in a 16:9 aspect ratio." == typed_text


@pytest.mark.asyncio
async def test_send_prompt_includes_count_and_aspect_in_directive() -> None:
    """The composed directive contains both count and aspect directives."""
    driver = AgenticFlowUiDriver()
    driver._pending_count = 3  # noqa: SLF001
    driver._pending_aspect = "1:1"  # noqa: SLF001

    page = MagicMock()
    keyboard = MagicMock()
    keyboard.press = AsyncMock()
    keyboard.insert_text = AsyncMock()
    page.keyboard = keyboard

    composer_loc = MagicMock()
    composer_loc.wait_for = AsyncMock()
    composer_loc.click = AsyncMock()

    submit_btn = MagicMock()
    submit_btn.count = AsyncMock(return_value=1)
    submit_btn.click = AsyncMock()

    def _locator(sel: str) -> MagicMock:
        if "textbox" in sel or "slate" in sel:
            return MagicMock(first=composer_loc)
        loc = MagicMock()
        loc.first = submit_btn
        return loc

    page.locator = MagicMock(side_effect=_locator)

    await driver.send_prompt(page, "a mountain")

    typed: str = keyboard.insert_text.call_args[0][0]
    assert "3 pictures" in typed
    assert "1:1" in typed
    assert "a mountain" in typed


# ---------------------------------------------------------------------------
# await_images — dedup, URL construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_await_images_deduplicates_nine_nodes_to_three() -> None:
    """9 img nodes (3 UUIDs × 3 variants) → 3 GeneratedImage objects."""
    driver = AgenticFlowUiDriver()
    page = _fake_page_no_policy(initial_srcs=[], new_srcs=_NINE_SRCS)

    images = await driver.await_images(page, expected_count=3)

    assert len(images) == 3
    returned_uuids = {img.media_name for img in images}
    assert returned_uuids == {UUID_A, UUID_B, UUID_C}


@pytest.mark.asyncio
async def test_await_images_url_has_no_thumbnail_param() -> None:
    """Full-res URL omits the &mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL param."""
    driver = AgenticFlowUiDriver()
    page = _fake_page_no_policy(initial_srcs=[], new_srcs=_NINE_SRCS)

    images = await driver.await_images(page, expected_count=3)

    for img in images:
        assert "THUMBNAIL" not in img.fife_url
        assert f"name={img.media_name}" in img.fife_url
        assert img.fife_url.startswith("https://labs.google/")


@pytest.mark.asyncio
async def test_await_images_url_construction() -> None:
    """Each returned image has the correct tRPC redirect URL."""
    driver = AgenticFlowUiDriver()
    srcs = [
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}",
    ]
    page = _fake_page_no_policy(initial_srcs=[], new_srcs=srcs)

    images = await driver.await_images(page, expected_count=1)

    assert len(images) == 1
    assert images[0].fife_url == _MEDIA_REDIRECT_BASE.format(uuid=UUID_A)


@pytest.mark.asyncio
async def test_await_images_only_new_uuids_counted() -> None:
    """UUIDs already present in the baseline are NOT counted as new."""
    driver = AgenticFlowUiDriver()
    # UUID_A is already in the page before generation.
    initial = [
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}",
    ]
    # After generation: UUID_A + UUID_B + UUID_C appear.
    after = initial + [
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_B}",
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_C}",
    ]
    page = _fake_page_no_policy(initial_srcs=initial, new_srcs=after)

    images = await driver.await_images(page, expected_count=2)

    returned_uuids = {img.media_name for img in images}
    assert UUID_A not in returned_uuids, "baseline UUID must not appear as new"
    assert {UUID_B, UUID_C} == returned_uuids


# ---------------------------------------------------------------------------
# await_images — baseline settle (two-pass union) & ambiguity fail-fast (#281)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_await_images_exactly_expected_passes_through() -> None:
    """Exactly ``expected_count`` new UUIDs -> a clean success, no error."""
    driver = AgenticFlowUiDriver()
    srcs = [
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}",
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_B}",
    ]
    page = _fake_page_no_policy(initial_srcs=[], new_srcs=srcs)

    images = await driver.await_images(page, expected_count=2)

    assert {img.media_name for img in images} == {UUID_A, UUID_B}


@pytest.mark.asyncio
async def test_await_images_more_than_expected_raises_media_attribution_error() -> None:
    """MORE new UUIDs than expected -> MediaAttributionError, NEVER a truncated success.

    Regression guard for issue #281: the agentic driver must not silently pick
    an arbitrary subset of an unordered UUID set and report it as success.
    """
    driver = AgenticFlowUiDriver()
    page = _fake_page_no_policy(initial_srcs=[], new_srcs=_NINE_SRCS)  # 3 distinct UUIDs

    with pytest.raises(MediaAttributionError) as exc_info:
        await driver.await_images(page, expected_count=2)

    detail = str(exc_info.value)
    # Must name the candidate UUIDs and the expected count -- never truncate/slice.
    assert UUID_A in detail
    assert UUID_B in detail
    assert UUID_C in detail
    assert "2" in detail


def test_build_generated_images_requires_exact_count() -> None:
    """_build_generated_images enforces its exact-count invariant defensively.

    It is only reachable with exactly ``expected_count`` UUIDs (await_images
    raises for both too-few and too-many); a mismatch means a caller bug, so
    it must fail loudly instead of slicing an unordered set (#281).
    """
    from gflow_cli.api.transports.drivers.agentic import _build_generated_images

    with pytest.raises(AssertionError, match="invariant"):
        _build_generated_images(
            uuids={UUID_A, UUID_B, UUID_C},
            expected_count=2,
            pending_model="NARWHAL",
            pending_aspect=None,
        )


@pytest.mark.asyncio
async def test_await_images_baseline_union_ignores_lazy_render_in_second_pass() -> None:
    """A UUID present only in the second baseline pass is NOT "new" (#281).

    This is the exact production failure mode: a pre-existing project tile
    lazily renders into the DOM between the first and second baseline scrape.
    The two-pass union must absorb it into the baseline so it is never
    miscounted as freshly generated.
    """
    driver = AgenticFlowUiDriver()
    # UUID_A renders immediately; UUID_B only appears on the second (settled)
    # baseline pass -- both must be excluded from "new". Only UUID_C, which
    # appears after generation, is new.
    initial = [f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}"]
    second_baseline = initial + [
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_B}"
    ]
    after_generation = second_baseline + [
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_C}"
    ]
    page = _fake_page_no_policy(
        initial_srcs=initial,
        new_srcs=after_generation,
        second_baseline_srcs=second_baseline,
    )

    images = await driver.await_images(page, expected_count=1)

    returned_uuids = {img.media_name for img in images}
    assert returned_uuids == {UUID_C}
    assert UUID_B not in returned_uuids, "lazy tile from the second baseline pass must not be 'new'"


# ---------------------------------------------------------------------------
# await_images — content-policy block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_await_images_raises_content_policy_on_explicit_text() -> None:
    """Explicit policy text in an alert/dialog region → ContentPolicyError (not timeout)."""
    driver = AgenticFlowUiDriver()

    async def _eval(selector: str, expr: str) -> list[str]:
        if selector == "img":
            return []  # no generated images
        # Policy alert/dialog region carries an explicit block phrase.
        return ["Sorry, I can't create that due to content policy."]

    page = MagicMock()
    page.eval_on_selector_all = _eval
    page.locator = MagicMock(return_value=MagicMock(count=AsyncMock(return_value=0)))

    with pytest.raises(ContentPolicyError):
        await driver.await_images(page, expected_count=1)


@pytest.mark.asyncio
async def test_await_images_ignores_body_chrome_policy_text() -> None:
    """Static "content policy" chrome OUTSIDE an alert region must NOT block.

    Regression guard for the 2026-06-14 live false positive: a benign prompt
    raised a spurious block because the whole page body was scanned and a footer
    "Content policy" link matched. Detection is now scoped to alert/dialog
    regions, so a clean generation with chrome text still succeeds.
    """
    driver = AgenticFlowUiDriver()
    srcs = [f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}"]
    # Region scan returns [] (no alert) even though body chrome would contain
    # "content policy" — the driver no longer scans the body.
    page = _fake_page_no_policy(initial_srcs=[], new_srcs=srcs)
    images = await driver.await_images(page, expected_count=1)
    assert len(images) == 1


@pytest.mark.asyncio
async def test_await_images_flag_only_is_not_a_policy_block() -> None:
    """The ``flag`` ligature is a normal chat affordance — must NOT trigger ContentPolicyError.

    2026-06-14 live capture: ``flag`` matched 11× on a SUCCESSFUL generation.
    This test pins the exclusion so it can never regress.
    """
    driver = AgenticFlowUiDriver()

    srcs = [
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}",
    ]

    # Policy detection scans alert/dialog regions only (the helper returns none),
    # so the `flag` chat affordance can never be read as a block. Generation
    # succeeds with the scraped UUID_A image.
    page = _fake_page_no_policy(initial_srcs=[], new_srcs=srcs)
    images = await driver.await_images(page, expected_count=1)
    assert len(images) == 1


# ---------------------------------------------------------------------------
# await_images — timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_await_images_timeout_with_partial_raises_typed_error() -> None:
    """Partial completion (1 of 3 expected) → TransportTimeoutError with mismatch detail.

    Uses a longer timeout so the poll loop has time to see UUID_A (partial result)
    before giving up on the remaining 2.
    """
    driver = AgenticFlowUiDriver()

    # Baseline is empty; subsequent polls always see only UUID_A (1 of 3 expected).
    page = _fake_page_no_policy(
        initial_srcs=[],
        new_srcs=[
            f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}",
        ],
    )

    with (
        patch(
            "gflow_cli.api.transports.drivers.agentic._AWAIT_TIMEOUT_S",
            0.6,  # enough time for ~1 poll cycle to find UUID_A, not 3
        ),
        pytest.raises(TransportTimeoutError) as exc_info,
    ):
        await driver.await_images(page, expected_count=3)

    detail = str(exc_info.value)
    # Detail must mention 1 produced and 3 requested.
    assert "1" in detail and "3" in detail


@pytest.mark.asyncio
async def test_await_images_timeout_zero_new_raises_typed_error() -> None:
    """Zero new UUIDs after timeout → TransportTimeoutError."""
    driver = AgenticFlowUiDriver()

    page = MagicMock()
    page.eval_on_selector_all = AsyncMock(return_value=[])
    page.evaluate = AsyncMock(return_value="normal page text")
    count_mock = AsyncMock(return_value=0)
    loc = MagicMock()
    loc.count = count_mock
    page.locator = MagicMock(return_value=loc)

    with (
        patch(
            "gflow_cli.api.transports.drivers.agentic._AWAIT_TIMEOUT_S",
            0.05,
        ),
        pytest.raises(TransportTimeoutError),
    ):
        await driver.await_images(page, expected_count=1)


# ---------------------------------------------------------------------------
# Video methods raise FlowAgentUiError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_to_video_mode_raises_flow_agent_ui_error() -> None:
    driver = AgenticFlowUiDriver()
    with pytest.raises(FlowAgentUiError, match="[Aa]gentic video"):
        await driver.switch_to_video_mode(MagicMock())


@pytest.mark.asyncio
async def test_configure_video_settings_raises_flow_agent_ui_error() -> None:
    driver = AgenticFlowUiDriver()
    with pytest.raises(FlowAgentUiError, match="[Aa]gentic video"):
        await driver.configure_video_settings(MagicMock(), MagicMock())


# ---------------------------------------------------------------------------
# switch_to_image_mode — no-op (explicit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_to_image_mode_does_not_call_fill() -> None:
    """switch_to_image_mode is a pure no-op: it must not invoke any page method."""
    driver = AgenticFlowUiDriver()
    page = MagicMock()
    await driver.switch_to_image_mode(page)
    page.fill.assert_not_called()
    page.click.assert_not_called()


# ---------------------------------------------------------------------------
# Scrape-synthesised fields in GeneratedImage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_await_images_synthesised_fields() -> None:
    """Scrape-synthesised fields have sentinel values documented in the module."""
    driver = AgenticFlowUiDriver()
    driver._pending_model = "NARWHAL"  # noqa: SLF001
    driver._pending_aspect = "16:9"  # noqa: SLF001

    srcs = [
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}",
    ]
    page = _fake_page_no_policy(initial_srcs=[], new_srcs=srcs)

    images = await driver.await_images(page, expected_count=1)
    img = images[0]

    assert img.seed == 0
    assert img.workflow_id == ""
    assert img.model_name_type == "NARWHAL"
    assert img.aspect_ratio == "IMAGE_ASPECT_RATIO_LANDSCAPE"
    assert img.media_generation_id is None
    assert img.dimensions == (0, 0)


# ---------------------------------------------------------------------------
# Instructions Reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_instructions_no_op_when_none() -> None:
    driver = AgenticFlowUiDriver()
    page = _mock_page_no_settings_panel()
    req = GenerateImageRequest(prompt="a cat", instructions=None)
    await driver.configure_image_settings(page, req)
    # instructions=None means _reconcile_instructions's REST PATCH path must
    # not run. The settings-panel count-enforcement step (unrelated) DOES
    # call page.locator now, so the original "locator never called"
    # assertion no longer holds — assert the instructions-specific behavior
    # instead.
    assert not page.request.patch.called


@pytest.mark.asyncio
async def test_reconcile_instructions_patches_rest_api() -> None:
    """_reconcile_instructions must call page.request.patch with the full
    card set via the REST agentInfo endpoint — no DOM loop involved."""
    driver = AgenticFlowUiDriver()

    session_response = MagicMock()
    session_response.json = AsyncMock(return_value={"access_token": "tok-abc"})
    mock_get = AsyncMock(return_value=session_response)

    page = MagicMock()
    page.url = "https://labs.google/fx/tools/flow/project/aaaa0000-0000-0000-0000-000000000001"
    page.request = MagicMock()
    page.request.get = mock_get
    page.request.patch = AsyncMock(return_value=MagicMock(status=200))
    mock_patch = page.request.patch

    instructions = (
        AgentInstruction("guideline A", enabled=True),
        AgentInstruction("guideline B", enabled=False),
    )
    await driver._reconcile_instructions(page, instructions)

    mock_patch.assert_called_once()
    call_args, call_kwargs = mock_patch.call_args
    url = call_args[0]
    assert "aaaa0000-0000-0000-0000-000000000001" in url
    assert "project_brief.cards" in url
    # The brief-level MASTER switch must be turned on (updateMask + body), else
    # a fresh project's brief stays off and every card is ignored.
    assert "project_brief.enabled" in url

    import json

    body = json.loads(call_kwargs["data"])
    assert body["projectBrief"]["enabled"] is True
    cards = body["projectBrief"]["cards"]
    assert len(cards) == 2  # noqa: PLR2004
    texts = {c["description"] for c in cards}
    assert "guideline A" in texts
    assert "guideline B" in texts
    disabled = [c for c in cards if c["description"] == "guideline B"]
    assert disabled and disabled[0]["enabled"] is False
    assert call_kwargs["headers"]["authorization"] == "Bearer tok-abc"
    # Content-type MUST be text/plain — application/json+protobuf is rejected 400
    # by Flow (instructions spike). Regression guard for the silent-sync bug.
    assert "protobuf" not in call_kwargs["headers"]["content-type"]
    assert call_kwargs["headers"]["content-type"].startswith("text/plain")
    # Each card carries its OWN title (derived from text), never a shared
    # constant — Task 7 matches cards by title.
    titles = {c["title"] for c in cards}
    assert titles == {"guideline A", "guideline B"}


@pytest.mark.asyncio
async def test_reconcile_instructions_warns_on_patch_failure() -> None:
    """A non-2xx PATCH must be logged, not swallowed silently (spike bug #1)."""
    driver = AgenticFlowUiDriver()

    mock_patch = AsyncMock(return_value=MagicMock(status=400))
    session_response = MagicMock()
    session_response.json = AsyncMock(return_value={"access_token": "tok"})

    page = MagicMock()
    page.url = "https://labs.google/fx/tools/flow/project/bbbb1111-0000-0000-0000-000000000002"
    page.request = MagicMock()
    page.request.get = AsyncMock(return_value=session_response)
    page.request.patch = mock_patch

    with patch.object(agentic_mod.log, "warning") as mock_warning:
        await driver._reconcile_instructions(page, (AgentInstruction("x", enabled=True),))

    mock_patch.assert_called_once()
    assert any(
        call.args and call.args[0] == "agentic_driver.reconcile_instructions.patch_failed"
        for call in mock_warning.call_args_list
    )


def test_agent_instruction_serialization_with_references() -> None:
    inst = AgentInstruction(
        text="A portrait of the hero",
        enabled=True,
        image_media_ids=("media-uuid-1",),
        character_ids=("char-uuid-2",),
    )
    assert inst.image_media_ids == ("media-uuid-1",)
    assert inst.character_ids == ("char-uuid-2",)


@pytest.mark.asyncio
async def test_driver_reconcile_dispatches_patch_payload() -> None:
    driver = AgenticFlowUiDriver()

    mock_patch = AsyncMock(return_value=MagicMock(status=200))
    session_response = MagicMock()
    session_response.json = AsyncMock(return_value={"access_token": "mock-token"})
    mock_get = AsyncMock(return_value=session_response)

    page = MagicMock()
    page.url = "https://labs.google/fx/tools/flow/project/71aa2873-9e6b-4ed1-9bdb-629ed0490b41"
    page.request = MagicMock()
    page.request.get = mock_get
    page.request.patch = mock_patch

    req = GenerateImageRequest(
        prompt="a cat",
        instructions=(
            AgentInstruction(
                text="Daytime Cinematic",
                enabled=True,
                image_media_ids=("media-uuid-1",),
            ),
        ),
    )

    await driver.configure_image_settings(page, req)

    mock_patch.assert_called_once()
    args, kwargs = mock_patch.call_args
    assert "71aa2873-9e6b-4ed1-9bdb-629ed0490b41" in args[0]
    assert "project_brief.cards" in args[0]

    import json

    body = json.loads(kwargs.get("data") or "")
    card = body["projectBrief"]["cards"][0]
    assert card["description"] == "Daytime Cinematic"
    assert card["enabled"] is True
    assert card["imageReferenceMediaIds"] == ["media-uuid-1"]


# ---------------------------------------------------------------------------
# await_images — stable-break condition (#283 hardening of the #281 race)
# ---------------------------------------------------------------------------


def _fake_page_poll_sequence(poll_srcs: list[list[str]]) -> MagicMock:
    """Page whose img scrapes return: [] for both baseline passes, then each
    entry of `poll_srcs` in order (last entry repeats). Policy scan: clean."""
    img_calls = 0

    async def _eval_on_selector_all(selector: str, expr: str) -> list[str]:
        nonlocal img_calls
        if selector == "img":
            img_calls += 1
            if img_calls <= 2:
                return []
            idx = min(img_calls - 3, len(poll_srcs) - 1)
            return poll_srcs[idx]
        return []

    page = MagicMock()
    page.eval_on_selector_all = _eval_on_selector_all
    count_mock = AsyncMock(return_value=0)
    locator_mock = MagicMock()
    locator_mock.count = count_mock
    page.locator = MagicMock(return_value=locator_mock)
    return page


_SRC_A = "https://lh3.googleusercontent.com/x?name=aaaaaaaa-1111-4111-8111-111111111111"
_SRC_B = "https://lh3.googleusercontent.com/x?name=bbbbbbbb-2222-4222-8222-222222222222"


@pytest.mark.asyncio
async def test_await_images_does_not_break_on_first_unstable_sighting() -> None:
    """#283 stable-break: reaching expected_count on ONE scrape is not enough —
    a lazily-rendered pre-existing tile can transiently hit the exact count.
    Here the set grows past expected on the next scrape, which must surface as
    the #281 ambiguity error, NOT be returned as 'the' generated image."""
    driver = AgenticFlowUiDriver()
    page = _fake_page_poll_sequence([[_SRC_A], [_SRC_A, _SRC_B]])
    with pytest.raises(MediaAttributionError):
        await driver.await_images(page, expected_count=1)


@pytest.mark.asyncio
async def test_await_images_breaks_after_two_stable_scrapes() -> None:
    driver = AgenticFlowUiDriver()
    page = _fake_page_poll_sequence([[_SRC_A], [_SRC_A]])
    images = await driver.await_images(page, expected_count=1)
    assert [img.media_name for img in images] == ["aaaaaaaa-1111-4111-8111-111111111111"]


@pytest.mark.asyncio
async def test_await_images_oscillation_needs_two_consecutive_identical_scrapes() -> None:
    """{x} -> {} -> {x} -> {x}: the shrink resets stability; only the final
    consecutive pair may break the loop."""
    driver = AgenticFlowUiDriver()
    page = _fake_page_poll_sequence([[_SRC_A], [], [_SRC_A], [_SRC_A]])
    images = await driver.await_images(page, expected_count=1)
    assert [img.media_name for img in images] == ["aaaaaaaa-1111-4111-8111-111111111111"]


@pytest.mark.asyncio
async def test_await_images_deadline_exit_returns_last_exact_count_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Documented fall-through: exact count first reached on the final poll
    before the deadline is returned WITHOUT the two-scrape confirmation —
    better a last-second success than a spurious timeout."""
    from gflow_cli.api.transports.drivers import agentic as mod

    monkeypatch.setattr(mod, "_AWAIT_TIMEOUT_S", mod._POLL_INTERVAL_S * 3.5)
    # never stable: each poll alternates, deadline lands while set == {A}
    page = _fake_page_poll_sequence([[], [_SRC_A]])
    images = await driver_await(page)
    assert [img.media_name for img in images] == ["aaaaaaaa-1111-4111-8111-111111111111"]


async def driver_await(page: MagicMock) -> list:
    driver = AgenticFlowUiDriver()
    return await driver.await_images(page, expected_count=1)
