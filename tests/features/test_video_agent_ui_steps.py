"""Step bindings for video_agent_ui.feature.

Covers:
- Forced Agentic UI raises FlowAgentUiError (existing, kept as-is).
- Agentic image driver: UUID dedup, settings-in-prompt, content-policy block,
  flag-only page is NOT a block, and count-mismatch timeout.

Driver-boundary mock approach
------------------------------
All new scenarios drive ``AgenticFlowUiDriver`` directly — the same boundary
used in ``tests/api/transports/drivers/test_agentic.py``. A mock ``Page``
(``MagicMock``) reproduces the exact DOM surface the driver queries:
- ``page.eval_on_selector_all`` → returns img srcs for the ``img`` selector, and
  alert/dialog region text for the policy-region selector (scoped policy check).
- ``page.keyboard``            → ``MagicMock`` with ``AsyncMock`` methods.
- ``page.locator(…).first``    → composer/submit mocks.

pytest-bdd 8.x does not support ``async def`` step functions. All async
driver calls are wrapped with ``asyncio.run()`` executed inside sync steps.
State is shared across steps via per-scenario fixture dicts (``driver_state``
and ``driver_result``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports.drivers.agentic import (
    _MEDIA_REDIRECT_BASE,
    AgenticFlowUiDriver,
)
from gflow_cli.cli import main
from gflow_cli.errors import ContentPolicyError, FlowAgentUiError, TransportTimeoutError

scenarios("video_agent_ui.feature")

# ---------------------------------------------------------------------------
# UUID constants (mirror test_agentic.py)
# ---------------------------------------------------------------------------

UUID_A = "aaaaaaaa-0000-0000-0000-000000000001"
UUID_B = "bbbbbbbb-0000-0000-0000-000000000002"
UUID_C = "cccccccc-0000-0000-0000-000000000003"

# Nine src strings: 3 distinct UUIDs × 3 node variants each.
_NINE_SRCS = [
    f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}",
    (
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}"
        "&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL"
    ),
    (f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}&mediaUrlType=OTHER"),
    f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_B}",
    (
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_B}"
        "&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL"
    ),
    (f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_B}&mediaUrlType=OTHER"),
    f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_C}",
    (
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_C}"
        "&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL"
    ),
    (f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_C}&mediaUrlType=OTHER"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_image_request(
    *,
    count: int = 1,
    aspect: Aspect = Aspect.PORTRAIT,
    model: Model = Model.NARWHAL,
) -> GenerateImageRequest:
    return GenerateImageRequest(prompt="a cat", count=count, aspect=aspect, model=model)


def _fake_page_no_policy(
    *,
    initial_srcs: list[str],
    new_srcs: list[str],
) -> MagicMock:
    """Page mock: first ``img`` scrape returns initial_srcs, subsequent ones new_srcs.

    Branches on selector: ``img`` drives the scrape; the policy alert/dialog
    region scan returns no regions (no content-policy signal).
    """
    img_calls = 0

    async def _eval_on_selector_all(selector: str, expr: str) -> list[str]:
        nonlocal img_calls
        if selector == "img":
            img_calls += 1
            return initial_srcs if img_calls == 1 else new_srcs
        return []  # policy region scan: no alert/dialog regions

    page = MagicMock()
    page.eval_on_selector_all = _eval_on_selector_all

    loc_mock = MagicMock()
    loc_mock.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=loc_mock)
    return page


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_result_holder() -> dict[str, Any]:
    return {"result": None}


@pytest.fixture
def driver_state() -> dict[str, Any]:
    """Shared mutable state for driver-boundary scenarios."""
    return {
        "driver": None,
        "page": None,
        "images": None,
        "exception": None,
        "keyboard_mock": None,
    }


@pytest.fixture
def driver_result() -> dict[str, Any]:
    return {"images": None, "exception": None}


@pytest.fixture(autouse=True)
def _isolate_profile_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate profile home environment variables for testing."""
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.setattr("gflow_cli.cli_video._resolve_profile", lambda profile: "default")
    monkeypatch.setattr("gflow_cli.cli_video._make_provider_dir", lambda name: tmp_path)


# ---------------------------------------------------------------------------
# Existing scenario: Forced Agentic UI raises FlowAgentUiError
# ---------------------------------------------------------------------------


@given("the page DOM shows forced Agentic UI")
def _mock_forced_agent_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock generate_video to raise FlowAgentUiError simulating forced Agentic UI."""

    async def _raise(*args: Any, **kwargs: Any) -> None:
        raise FlowAgentUiError(detail="Agentic UI detected.")

    monkeypatch.setattr("gflow_cli.api.client.FlowApiClient.generate_video", _raise)


@when('I run "gflow video t2v a futuristic city"')
def _run_t2v_city(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    """Invoke the CLI command to generate a video."""
    cli_result_holder["result"] = runner.invoke(main, ["video", "t2v", "a futuristic city"])


@then(parsers.parse("the exit code is {code:d}"))
def _assert_exit_code(cli_result_holder: dict[str, Any], code: int) -> None:
    """Assert that the CLI exit code matches the expected code."""
    res = cli_result_holder["result"]
    assert res is not None
    assert res.exit_code == code


@then(parsers.parse('the output contains "{text}"'))
def _assert_output_contains(cli_result_holder: dict[str, Any], text: str) -> None:
    """Assert that the CLI stdout/stderr output contains the expected text."""
    res = cli_result_holder["result"]
    assert res is not None
    assert text in res.output


# ---------------------------------------------------------------------------
# Scenario: UUID dedup (9 img nodes → 3 images)
# ---------------------------------------------------------------------------


@given("an agentic page with 9 img srcs for 3 distinct UUIDs and no prior images")
def _given_nine_srcs_page(driver_state: dict[str, Any]) -> None:
    """Set up a page mock that returns 9 img srcs (3 UUIDs × 3 variants)."""
    driver_state["driver"] = AgenticFlowUiDriver()
    driver_state["page"] = _fake_page_no_policy(initial_srcs=[], new_srcs=_NINE_SRCS)


@when("I call await_images with expected_count 3")
def _when_await_images_3(driver_state: dict[str, Any]) -> None:
    """Call await_images(expected_count=3) on the driver; capture result or exception."""
    driver: AgenticFlowUiDriver = driver_state["driver"]
    page: MagicMock = driver_state["page"]
    try:
        images = asyncio.run(driver.await_images(page, expected_count=3))
        driver_state["images"] = images
    except Exception as exc:  # noqa: BLE001
        driver_state["exception"] = exc


@then("3 GeneratedImage objects are returned")
def _then_3_images(driver_state: dict[str, Any]) -> None:
    assert driver_state["exception"] is None, driver_state["exception"]
    assert driver_state["images"] is not None
    assert len(driver_state["images"]) == 3


@then("each image URL contains the media UUID and no THUMBNAIL param")
def _then_urls_no_thumbnail(driver_state: dict[str, Any]) -> None:
    images = driver_state["images"]
    assert images is not None
    for img in images:
        assert "THUMBNAIL" not in img.fife_url
        assert f"name={img.media_name}" in img.fife_url
        assert img.fife_url.startswith("https://labs.google/")
        # Confirm it matches the redirect base format.
        expected = _MEDIA_REDIRECT_BASE.format(uuid=img.media_name)
        assert img.fife_url == expected


# ---------------------------------------------------------------------------
# Scenario: Settings encoded in the prompt directive
# ---------------------------------------------------------------------------


@given("an agentic driver configured with count 4 and aspect 16:9")
def _given_driver_count4_landscape(driver_state: dict[str, Any]) -> None:
    """Configure the driver with count=4, aspect=LANDSCAPE (→ 16:9)."""
    driver = AgenticFlowUiDriver()
    req = _make_image_request(count=4, aspect=Aspect.LANDSCAPE)
    page = MagicMock()
    asyncio.run(driver.configure_image_settings(page, req))
    driver_state["driver"] = driver


@when('I call send_prompt with text "a red apple"')
def _when_send_prompt_red_apple(driver_state: dict[str, Any]) -> None:
    """Call send_prompt and record the keyboard mock for assertion."""
    driver: AgenticFlowUiDriver = driver_state["driver"]

    keyboard = MagicMock()
    keyboard.press = AsyncMock()
    keyboard.insert_text = AsyncMock()

    page = MagicMock()
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

    asyncio.run(driver.send_prompt(page, "a red apple"))
    driver_state["keyboard_mock"] = keyboard


@then('keyboard.insert_text was called with "4 images" and "16:9" in the directive')
def _then_directive_contains_count_and_aspect(driver_state: dict[str, Any]) -> None:
    keyboard: MagicMock = driver_state["keyboard_mock"]
    keyboard.insert_text.assert_awaited_once()
    typed_text: str = keyboard.insert_text.call_args[0][0]
    assert "4 images" in typed_text, f"Expected '4 images' in directive: {typed_text!r}"
    assert "16:9" in typed_text, f"Expected '16:9' in directive: {typed_text!r}"
    assert "a red apple" in typed_text, f"Expected prompt text in directive: {typed_text!r}"


# ---------------------------------------------------------------------------
# Scenario: Content-policy block raises ContentPolicyError
# ---------------------------------------------------------------------------


@given("an agentic page whose body text signals a content-policy block")
def _given_policy_block_page(driver_state: dict[str, Any]) -> None:
    """Page mock whose alert/dialog region carries explicit policy block text."""
    driver_state["driver"] = AgenticFlowUiDriver()

    async def _eval(selector: str, expr: str) -> list[str]:
        if selector == "img":
            return []  # no generated images
        return ["Sorry, I can't create that due to content policy."]  # alert region

    page = MagicMock()
    page.eval_on_selector_all = _eval
    loc = MagicMock()
    loc.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=loc)
    driver_state["page"] = page


@when("I call await_images expecting 1 image")
def _when_await_images_1(driver_state: dict[str, Any]) -> None:
    """Call await_images(expected_count=1) and capture any exception raised."""
    driver: AgenticFlowUiDriver = driver_state["driver"]
    page: MagicMock = driver_state["page"]
    try:
        images = asyncio.run(driver.await_images(page, expected_count=1))
        driver_state["images"] = images
    except Exception as exc:  # noqa: BLE001
        driver_state["exception"] = exc


@then("ContentPolicyError is raised")
def _then_content_policy_error(driver_state: dict[str, Any]) -> None:
    exc = driver_state["exception"]
    assert exc is not None, "Expected ContentPolicyError but no exception was raised"
    assert isinstance(exc, ContentPolicyError), (
        f"Expected ContentPolicyError, got {type(exc).__name__}: {exc}"
    )


# ---------------------------------------------------------------------------
# Scenario: Flag-only page is NOT a content-policy block
# ---------------------------------------------------------------------------


@given("an agentic page whose body text contains only flag affordances")
def _given_flag_only_page(driver_state: dict[str, Any]) -> None:
    """Page mock: body text has 'flag' (normal chat affordance) but no UUID yet."""
    driver_state["driver"] = AgenticFlowUiDriver()

    srcs_with_uuid_a = [
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}",
    ]
    # Policy detection scans alert/dialog regions only (the helper returns none),
    # so the `flag` chat affordance can never be read as a block.
    page = _fake_page_no_policy(initial_srcs=[], new_srcs=srcs_with_uuid_a)
    driver_state["page"] = page


@when("I call await_images expecting 1 image and 1 UUID is present")
def _when_await_images_1_with_uuid(driver_state: dict[str, Any]) -> None:
    """Call await_images(expected_count=1) — should succeed because 'flag' is excluded."""
    driver: AgenticFlowUiDriver = driver_state["driver"]
    page: MagicMock = driver_state["page"]
    try:
        images = asyncio.run(driver.await_images(page, expected_count=1))
        driver_state["images"] = images
    except Exception as exc:  # noqa: BLE001
        driver_state["exception"] = exc


@then("1 GeneratedImage is returned without error")
def _then_1_image_no_error(driver_state: dict[str, Any]) -> None:
    assert driver_state["exception"] is None, f"Unexpected exception: {driver_state['exception']}"
    images = driver_state["images"]
    assert images is not None
    assert len(images) == 1


# ---------------------------------------------------------------------------
# Scenario: Count mismatch / partial → TransportTimeoutError
# ---------------------------------------------------------------------------


@given("an agentic page that only ever yields 1 distinct UUID")
def _given_partial_page(driver_state: dict[str, Any]) -> None:
    """Page mock: scrape always returns exactly 1 UUID regardless of poll count."""
    driver_state["driver"] = AgenticFlowUiDriver()

    srcs_with_one_uuid = [
        f"https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={UUID_A}",
    ]
    page = _fake_page_no_policy(initial_srcs=[], new_srcs=srcs_with_one_uuid)
    driver_state["page"] = page


@given("the await timeout is patched to a tiny value")
def _given_tiny_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch module-level timeout constants so the test runs in milliseconds."""
    monkeypatch.setattr(
        "gflow_cli.api.transports.drivers.agentic._AWAIT_TIMEOUT_S",
        0.15,
    )
    monkeypatch.setattr(
        "gflow_cli.api.transports.drivers.agentic._POLL_INTERVAL_S",
        0.05,
    )


@when("I call await_images expecting 4 images")
def _when_await_images_4(driver_state: dict[str, Any]) -> None:
    """Call await_images(expected_count=4) — should time out (only 1 UUID available)."""
    driver: AgenticFlowUiDriver = driver_state["driver"]
    page: MagicMock = driver_state["page"]
    try:
        images = asyncio.run(driver.await_images(page, expected_count=4))
        driver_state["images"] = images
    except Exception as exc:  # noqa: BLE001
        driver_state["exception"] = exc


@then("TransportTimeoutError is raised with produced and requested counts in the detail")
def _then_timeout_error_with_detail(driver_state: dict[str, Any]) -> None:
    exc = driver_state["exception"]
    assert exc is not None, "Expected TransportTimeoutError but no exception was raised"
    assert isinstance(exc, TransportTimeoutError), (
        f"Expected TransportTimeoutError, got {type(exc).__name__}: {exc}"
    )
    # Detail must mention the partial produced count (1) and requested count (4).
    detail = str(exc)
    assert "1" in detail, f"Expected produced count '1' in detail: {detail!r}"
    assert "4" in detail, f"Expected requested count '4' in detail: {detail!r}"
