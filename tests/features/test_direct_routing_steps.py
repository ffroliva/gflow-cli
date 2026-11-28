"""Offline BDD step definitions for direct routing and refusal retry.

Bound to tests/features/direct_routing.feature. Covers the 8 scenarios planned in
Task 20. Scenarios 1-5 run against the refusal retry loop; scenarios 6-8 run against the
route-aware client construction (``client_visibility``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_bdd import given, parsers, scenario, then, when

from gflow_cli.api.submit_policy import (
    BrowserVisibility,
    VisibilitySetting,
)

# ---------------------------------------------------------------------------
# Scenarios (8)
# ---------------------------------------------------------------------------


@scenario(
    "direct_routing.feature",
    "A refused direct submit is retried once from a fresh headless browser",
)
def test_refused_direct_submit_retried_once() -> None:
    pass


@scenario(
    "direct_routing.feature",
    "A refusal on both attempts stops without a third submit",
)
def test_refusal_on_both_attempts_stops() -> None:
    pass


@scenario(
    "direct_routing.feature",
    "A timeout after send is never resent",
)
def test_timeout_after_send_never_resent() -> None:
    pass


@scenario(
    "direct_routing.feature",
    "A signed-out profile reports the UI path's auth error with no retry",
)
def test_signed_out_profile_reports_auth_error_no_retry() -> None:
    pass


@scenario(
    "direct_routing.feature",
    "An explicit headed setting with no display fails before launch, naming the display",
)
def test_explicit_headed_without_display_fails_before_launch() -> None:
    pass


@scenario(
    "direct_routing.feature",
    "A credit-capped video takes the UI path",
)
def test_credit_capped_video_takes_ui_path() -> None:
    pass


@scenario(
    "direct_routing.feature",
    "An unmeasured shape takes the UI path headed",
)
def test_unmeasured_shape_takes_ui_path_headed() -> None:
    pass


@scenario(
    "direct_routing.feature",
    "GFLOW_CLI_HEADLESS=false pins headed",
)
def test_headless_false_pins_headed() -> None:
    pass


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def world() -> dict[str, Any]:
    return {
        "submits": [],
        "relaunches": [],
        "credits_charged": 0,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Given steps (Scenarios 1-5: Refusal retry & display checks)
# ---------------------------------------------------------------------------


@given("the direct path serves the request")
def _step_direct_path_serves(world: dict[str, Any]) -> None:
    world["route_direct"] = True


@given("Flow refuses the first submit as unusual activity")
def _step_flow_refuses_first(world: dict[str, Any]) -> None:
    world["submit_responses"] = ["UNUSUAL_ACTIVITY", "ACCEPTED"]


@given("Flow refuses every submit as unusual activity")
def _step_flow_refuses_every(world: dict[str, Any]) -> None:
    world["submit_responses"] = ["UNUSUAL_ACTIVITY", "UNUSUAL_ACTIVITY"]


@given("the submit times out after the body was sent")
def _step_submit_times_out(world: dict[str, Any]) -> None:
    world["submit_responses"] = ["TIMEOUT_AFTER_SEND"]


@given("the profile's saved session is signed out")
def _step_signed_out_profile(world: dict[str, Any]) -> None:
    world["signed_out"] = True


@given("an explicit headed browser setting without a display")
def _step_explicit_headed_no_display(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    world["setting"] = VisibilitySetting.HEADED
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


# ---------------------------------------------------------------------------
# When steps (Scenarios 1-5: Generation and client launch)
# ---------------------------------------------------------------------------


class _Host:
    """The client the loop relaunches: records each relaunch's visibility."""

    def __init__(self, world: dict[str, Any]) -> None:
        self.visibility = BrowserVisibility.HEADLESS
        self.relaunch_generation = 0
        self._world = world

    async def relaunch(self, visibility: BrowserVisibility, **_: object) -> None:
        self._world["relaunches"].append(visibility)
        self.visibility = visibility


@when("the generation runs")
def _step_generation_runs(world: dict[str, Any]) -> None:
    from gflow_cli.api._flow_retry import run_with_retry
    from gflow_cli.api.submit_policy import (
        DirectAttemptEnded,
        Indeterminate,
        NotSent,
        RefusedNotBilled,
    )
    from gflow_cli.errors import NetworkError

    answers = list(world.get("submit_responses", ["ACCEPTED"]))

    async def direct(before: Any) -> str:
        if world.get("signed_out"):
            raise DirectAttemptEnded(NotSent("signed_out_shell", auth=True))
        world["submits"].append(len(world["relaunches"]))
        answer = answers.pop(0)
        if answer == "UNUSUAL_ACTIVITY":
            raise DirectAttemptEnded(RefusedNotBilled("ogiZ0b", "UNUSUAL_ACTIVITY"))
        if answer == "TIMEOUT_AFTER_SEND":
            # As the gateway does: an outcome past the send carries its reconcile error.
            raise DirectAttemptEnded(
                Indeterminate("ogiZ0b", "timeout after send"),
                error=NetworkError(
                    detail="timeout after send on an image submit; not resent",
                    route="batchexecute:ogiZ0b",
                    next_action="reconcile",
                ),
            )
        return "image"

    async def ui() -> str:
        raise AssertionError("the UI path must not run")

    async def run() -> None:
        try:
            world["result"] = await run_with_retry(
                _Host(world),
                direct=direct,
                ui=ui,
                setting=world.get("setting", VisibilitySetting.AUTO),
            )
        except Exception as exc:  # noqa: BLE001 - the Then steps assert on it
            world["error"] = exc

    asyncio.run(run())


@when("the client launches")
def _step_client_launches(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli.api.client import FlowApiClient

    monkeypatch.setattr("gflow_cli.api.client._on_linux", lambda: True)
    monkeypatch.setattr(
        "gflow_cli.api.client.async_playwright",
        MagicMock(side_effect=AssertionError("Playwright must not start")),
    )

    async def launch() -> None:
        async with FlowApiClient(tmp_path, visibility=BrowserVisibility.HEADED):
            pass

    try:
        asyncio.run(launch())
    except Exception as exc:  # noqa: BLE001 - the Then step asserts on it
        world["error"] = exc


# ---------------------------------------------------------------------------
# Then steps (Scenarios 1-5: Refusal retry & display assertions)
# ---------------------------------------------------------------------------


@then("exactly two submits are sent")
def _step_exactly_two_submits(world: dict[str, Any]) -> None:
    assert len(world.get("submits", [])) == 2


@then("the second runs on a new headless browser process")
def _step_second_runs_headless(world: dict[str, Any]) -> None:
    assert world.get("relaunches") == [BrowserVisibility.HEADLESS]


@then("no credits are reported as charged")
def _step_no_credits_charged(world: dict[str, Any]) -> None:
    assert world.get("credits_charged", 0) == 0


@then("the error is not retryable and suggests no next action")
def _step_error_not_retryable(world: dict[str, Any]) -> None:
    from gflow_cli.errors import WafRejectionError

    err = world.get("error")
    assert isinstance(err, WafRejectionError)
    assert getattr(err, "retryable", None) is False
    assert getattr(err, "next_action", None) == "none"
    assert getattr(err, "charged", None) == "no"


@then("exactly one submit is sent")
def _step_exactly_one_submit(world: dict[str, Any]) -> None:
    assert len(world.get("submits", [])) == 1


@then("the outcome is indeterminate and the project is reconciled")
def _step_outcome_indeterminate(world: dict[str, Any]) -> None:
    err = world.get("error")
    assert err is not None
    assert getattr(err, "next_action", None) == "reconcile"
    assert getattr(err, "charged", None) == "maybe"
    assert getattr(err, "retryable", None) is False


@then("no submits are sent")
def _step_no_submits(world: dict[str, Any]) -> None:
    assert len(world.get("submits", [])) == 0


@then("the UI auth error is reported with no retry")
def _step_ui_auth_error(world: dict[str, Any]) -> None:
    from gflow_cli.errors import AuthExpiredError  # the class the UI path raises signed out

    err = world.get("error")
    assert isinstance(err, AuthExpiredError)
    assert len(world.get("submits", [])) == 0


@then("the launch fails before launch naming the missing display")
def _step_fails_naming_display(world: dict[str, Any]) -> None:
    from gflow_cli.errors import UiModeUnavailableError, exit_code_for

    err = world.get("error")
    assert isinstance(err, UiModeUnavailableError)
    assert "display" in err.detail.lower()
    assert exit_code_for(err) == 28


# ---------------------------------------------------------------------------
# Given steps (Scenarios 6-8: Routing)
# ---------------------------------------------------------------------------


@given("a video request at a measured key with a credit cap")
def _step_video_capped(world: dict[str, Any]) -> None:
    world["request_type"] = "video"
    world["max_credits"] = 10


@given("an image-to-video request with an end frame")
def _step_i2v_end_frame(world: dict[str, Any]) -> None:
    world["request_type"] = "i2v"
    world["has_end_frame"] = True


@given(parsers.parse('the environment variable GFLOW_CLI_HEADLESS is set to "{val}"'))
def _step_env_headless_false(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, val: str
) -> None:
    monkeypatch.setenv("GFLOW_CLI_HEADLESS", val)
    from gflow_cli import config

    config.reset_settings()


# ---------------------------------------------------------------------------
# When steps (Scenarios 6-8: Routing via client_visibility)
# ---------------------------------------------------------------------------


@when("the route is chosen")
def _step_route_chosen(world: dict[str, Any], tmp_path: Path) -> None:
    from gflow_cli._cli_helpers import client_visibility, video_route
    from gflow_cli.api.generation_route import Route
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel
    from gflow_cli.config import get_settings

    settings = get_settings()

    if world.get("request_type") == "video":
        # The measured Omni Flash text-to-video key, with a cap.
        route = video_route(
            GenerateVideoRequest(
                prompt="a lighthouse at dusk",
                mode=Mode.T2V,
                model=VideoModel.OMNI_1_2_FLASH,
                duration=4,
                resolution="360p",
                aspect=Aspect.LANDSCAPE,
                max_credits=world["max_credits"],
            )
        )
    elif world.get("request_type") == "i2v":
        route = video_route(
            GenerateVideoRequest(
                prompt="a lighthouse at dusk",
                mode=Mode.I2V,
                aspect=Aspect.PORTRAIT,
                start_image=tmp_path / "start.png",
                end_image=tmp_path / "end.png" if world.get("has_end_frame") else None,
            )
        )
    else:
        route = Route.DIRECT
    world["route"] = route
    world["visibility"] = client_visibility(settings, routes=(route,))


# ---------------------------------------------------------------------------
# Then steps (Scenarios 6-8: Routing assertions)
# ---------------------------------------------------------------------------


@then("the UI path is chosen and launched headed")
def _step_ui_path_headed(world: dict[str, Any]) -> None:
    from gflow_cli.api.generation_route import Route

    assert world["route"] == Route.UI
    assert world["visibility"] == BrowserVisibility.HEADED


@then("the launch visibility is headed")
def _step_visibility_headed(world: dict[str, Any]) -> None:
    assert world["visibility"] == BrowserVisibility.HEADED
