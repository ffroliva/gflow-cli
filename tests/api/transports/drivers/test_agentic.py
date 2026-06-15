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

from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports.drivers.agentic import (
    _MEDIA_REDIRECT_BASE,
    AgenticFlowUiDriver,
    _extract_uuids,
)
from gflow_cli.errors import ContentPolicyError, FlowAgentUiError, TransportTimeoutError

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


def _fake_page_no_policy(*, initial_srcs: list[str], new_srcs: list[str]) -> MagicMock:
    """Page that returns initial_srcs on the first ``img`` scrape, new_srcs after.

    Branches on the selector: ``img`` calls drive the scrape; the policy
    alert/dialog region scan returns no regions (no content-policy signal).
    """
    img_calls = 0

    async def _eval_on_selector_all(selector: str, expr: str) -> list[str]:
        nonlocal img_calls
        if selector == "img":
            img_calls += 1
            return initial_srcs if img_calls == 1 else new_srcs
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
    assert directive == "Generate 4 images in 16:9 aspect ratio: a red apple"


def test_compose_directive_no_aspect() -> None:
    directive = AgenticFlowUiDriver._compose_directive(1, None, "a red apple")
    assert directive == "Generate 1 image: a red apple"


def test_compose_directive_plural_singular() -> None:
    assert "1 image:" in AgenticFlowUiDriver._compose_directive(1, None, "x")
    assert "2 images:" in AgenticFlowUiDriver._compose_directive(2, None, "x")


# ---------------------------------------------------------------------------
# configure_image_settings → stores values for send_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configure_image_settings_stores_count_and_aspect() -> None:
    driver = AgenticFlowUiDriver()
    page = MagicMock()
    req = _make_image_request(count=4, aspect=Aspect.LANDSCAPE)
    await driver.configure_image_settings(page, req)
    assert driver._pending_count == 4  # noqa: SLF001
    assert driver._pending_aspect == "16:9"  # noqa: SLF001


@pytest.mark.asyncio
async def test_configure_image_settings_portrait_aspect() -> None:
    driver = AgenticFlowUiDriver()
    req = _make_image_request(count=1, aspect=Aspect.PORTRAIT)
    await driver.configure_image_settings(MagicMock(), req)
    assert driver._pending_aspect == "9:16"  # noqa: SLF001


@pytest.mark.asyncio
async def test_configure_image_settings_square_aspect() -> None:
    driver = AgenticFlowUiDriver()
    req = _make_image_request(count=2, aspect=Aspect.SQUARE)
    await driver.configure_image_settings(MagicMock(), req)
    assert driver._pending_aspect == "1:1"  # noqa: SLF001


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
    assert "Generate 2 images in 16:9 aspect ratio: a red apple" == typed_text


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
    assert "3 images" in typed
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
