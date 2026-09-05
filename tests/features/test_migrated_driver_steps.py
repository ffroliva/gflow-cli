"""Steps for migrated_driver.feature — the flow.google.com composer.

Uses the same fake page as tests/api/transports/test_migrated_composer.py
(imported, not copied) so the BDD layer exercises the real MigratedComposer
against the measured DOM shape, and the transport dispatch against a stub.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode
from gflow_cli.config import reset_settings
from gflow_cli.errors import EXIT_CODE_MAP, ConfigurationError, FlowHostMigratedError
from tests.api.transports.test_migrated_composer import (
    MEDIA,
    VIDEO_URL,
    FakePage,
    _batch_url,
    _frame,
    _record,
)

scenarios("migrated_driver.feature")

_LABS = "https://labs.google/fx/en/tools/flow/project/p1"
_MIGRATED = "https://flow.google.com/project/p1"


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A transport whose project entry is stubbed; the composer runs for real
    against the fake page, which scripts the three batchexecute replies."""
    page = FakePage(url=_LABS)
    page.scripted_responses = [
        (_batch_url("YhhmEf"), _frame("YhhmEf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("jwpduf"), _frame("jwpduf", [None, 881, [[_record(2)]]])),
        (_batch_url("as29s"), _frame("as29s", _record(3, VIDEO_URL))),
    ]
    transport = UiAutomationTransport()
    transport._page = page  # type: ignore[assignment]  # noqa: SLF001
    transport._setup_done = True  # noqa: SLF001
    w: dict[str, Any] = {"page": page, "transport": transport, "hop": False}

    async def _enter(_p: Any, _o: Any, *, project_id: str | None = None, **_: Any) -> None:
        if w["hop"]:
            page.url = _MIGRATED

    monkeypatch.setattr(transport, "_enter_editor", _enter)
    monkeypatch.setattr(VideoGenerationMixin, "_wait_video_editor_ready", AsyncMock())
    monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())

    def _set_flow_host(value: str) -> None:
        monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", value)
        reset_settings()

    w["set_flow_host"] = _set_flow_host
    _set_flow_host("auto")
    # No download in BDD: the transport's redirect path needs a real request layer.
    return w


@given("the editor hands the session to flow.google.com after entering the project")
def _hop(world: dict[str, Any]) -> None:
    world["hop"] = True


@given("the account has not been moved and a project is given")
def _unmoved(world: dict[str, Any]) -> None:
    world["hop"] = False  # the labs bootstrap URL stays; the composer navigates itself


@given("GFLOW_CLI_FLOW_HOST is labs.google")
def _kill_switch(world: dict[str, Any]) -> None:
    world["set_flow_host"]("labs.google")


@given("the settings pane renders no duration radiogroup")
def _no_duration(world: dict[str, Any]) -> None:
    del world["page"].dom.groups["duration"]
    world["hop"] = True


def _run(world: dict[str, Any], request: GenerateVideoRequest) -> None:
    async def go() -> Any:
        return await world["transport"].generate_video(
            request=request, project_id="p1", download=False, poll_timeout_s=5.0
        )

    try:
        world["result"] = asyncio.run(go())
    except Exception as exc:  # noqa: BLE001 - the Then steps classify it
        world["error"] = exc


@when("gflow video t2v runs with an 8 s request")
def _t2v_8s(world: dict[str, Any]) -> None:
    _run(
        world,
        GenerateVideoRequest(prompt="a crane", mode=Mode.T2V, aspect=Aspect.LANDSCAPE, duration=8),
    )


@when(parsers.parse("a {seconds:d} s duration is requested"))
def _t2v_duration(world: dict[str, Any], seconds: int) -> None:
    _run(
        world,
        GenerateVideoRequest(
            prompt="a crane", mode=Mode.T2V, aspect=Aspect.LANDSCAPE, duration=seconds
        ),
    )


@then("the migrated composer applies the settings and submits")
def _submitted(world: dict[str, Any]) -> None:
    assert "error" not in world, world.get("error")
    dom = world["page"].dom
    assert dom.groups["duration"][2].checked  # 8s
    assert dom.submit_clicked == 1
    assert dom.prompt == "a crane"


@then("the YhhmEf reply yields a workflow id and a media id")
def _submit_reply(world: dict[str, Any]) -> None:
    assert world["result"].status.media_id == MEDIA
    assert world["result"].flow_operation_id


@then("a reply with status 3 yields a flow-content.google URL")
def _result_reply(world: dict[str, Any]) -> None:
    text = world["page"].scripted_responses[-1][1]
    assert json.dumps(VIDEO_URL)[1:-1] in text
    assert "flow-content.google" in VIDEO_URL


@then("the result reports success with that workflow id")
def _success(world: dict[str, Any]) -> None:
    result = world["result"]
    assert result.status.succeeded
    assert result.project_id == "p1"
    assert result.flow_operation_id == "11111111-1111-4111-8111-111111111111"


@then("the run aborts pre-submit with exit 11 and names the missing axis")
def _exit_11(world: dict[str, Any]) -> None:
    exc = world["error"]
    assert isinstance(exc, ConfigurationError)
    assert EXIT_CODE_MAP[ConfigurationError] == 11
    assert "duration" in str(exc)
    assert world["page"].dom.submit_clicked == 0


@then("the run fails with exit 36 and the remediation names the switch")
def _exit_36(world: dict[str, Any]) -> None:
    exc = world["error"]
    assert isinstance(exc, FlowHostMigratedError)
    assert EXIT_CODE_MAP[FlowHostMigratedError] == 36
    assert "GFLOW_CLI_FLOW_HOST" in exc.remediation_hint
    assert world["page"].dom.submit_clicked == 0
