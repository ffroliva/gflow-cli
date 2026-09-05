"""Steps for migrated_i2v.feature — a local start frame on flow.google.com.

Same fake page as tests/api/transports/test_migrated_composer.py (imported, not
copied): the real MigratedComposer drives the measured DOM — toolbar upload,
maseQ reply, Frames picker, eb1hJf submit — and the transport dispatch runs
against a stubbed project entry.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode
from gflow_cli.config import reset_settings
from gflow_cli.errors import (
    EXIT_CODE_MAP,
    FlowHostMigratedError,
    MediaUploadRejectedError,
    ReferenceNotFoundError,
    WireFormatError,
)
from tests.api.transports.test_migrated_composer import (
    MEDIA,
    MEDIA_UP,
    PROJ,
    VIDEO_URL,
    WF,
    FakePage,
    _batch_url,
    _frame,
    _record,
)

scenarios("migrated_i2v.feature")

_LABS = "https://labs.google/fx/en/tools/flow/project/p1"
_MIGRATED = "https://flow.google.com/project/p1"


class _LabsDriverTouchedError(Exception):
    """Sentinel: the labs driver bind was reached."""


def _submit_body(media_id: str, key: str) -> str:
    return f'f.req=[[["x","[\\"{key}\\",\\"{media_id}\\",\\"{PROJ}\\"]",null,"generic"]]]'


def _png(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    return path


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    from gflow_cli.api.transports import migrated_composer

    page = FakePage(url=_LABS)
    page.dom.picker_options = ["hero.png", "Blue sphere on table"]
    page.scripted_request = ("eb1hJf", _submit_body(MEDIA_UP, "veo_3_1_i2v_lite"))
    page.scripted_responses = [
        (_batch_url("eb1hJf"), _frame("eb1hJf", [None, 881, [[MEDIA]], [[_record(6)]]])),
        (_batch_url("jwpduf"), _frame("jwpduf", [None, 881, [[_record(2)]]])),
        (_batch_url("as29s"), _frame("as29s", _record(3, VIDEO_URL))),
    ]
    transport = UiAutomationTransport()
    transport._page = page  # type: ignore[assignment]  # noqa: SLF001
    transport._setup_done = True  # noqa: SLF001
    w: dict[str, Any] = {
        "page": page,
        "transport": transport,
        "hop": False,
        "frame": _png(tmp_path, "hero.png"),
        "end_frame": _png(tmp_path, "last.png"),
    }

    async def _enter(_p: Any, _o: Any, *, project_id: str | None = None, **_: Any) -> None:
        if w["hop"]:
            page.url = _MIGRATED

    async def _labs_bind(*_: Any, **__: Any) -> Any:
        raise _LabsDriverTouchedError

    monkeypatch.setattr(transport, "_enter_editor", _enter)
    monkeypatch.setattr(VideoGenerationMixin, "_wait_video_editor_ready", AsyncMock())
    monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())
    monkeypatch.setattr("gflow_cli.api.transports.drivers.factory.get_ui_driver", _labs_bind)
    # Wait budgets that the fake page can never satisfy are shortened, not skipped.
    # `raising` stays on: a renamed constant must fail loudly here rather than leave
    # the real 8 s/60 s budgets in place and surface as a slow test.
    monkeypatch.setattr(migrated_composer, "FRAME_PICKER_OPEN_S", 0.1)
    monkeypatch.setattr(migrated_composer, "FRAME_UPLOAD_S", 0.2)
    monkeypatch.setattr(migrated_composer, "FRAME_SEARCH_RETRY_PAUSE_S", 0.01)
    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "auto")
    reset_settings()
    return w


# --- Given --------------------------------------------------------------------


@given("the editor hands the session to flow.google.com after entering the project")
def _hop(world: dict[str, Any]) -> None:
    world["hop"] = True


@given("the account has not been moved and a project is given")
def _unmoved(world: dict[str, Any]) -> None:
    world["hop"] = False


@given(parsers.parse('a local start frame "{name}"'))
def _frame_named(world: dict[str, Any], name: str) -> None:
    world["frame"] = _png(world["frame"].parent, name)


@given(parsers.parse('the picker lists no asset named "{name}"'))
def _picker_misses(world: dict[str, Any], name: str) -> None:
    world["hop"] = True
    world["page"].dom.picker_options = [
        o for o in world["page"].dom.picker_options if name.casefold() not in o.casefold()
    ]


@given("the Start chip is bound")
def _chip_binds(world: dict[str, Any]) -> None:
    world["hop"] = True
    world["page"].dom.chip_binds = True


@given("the submit reply arrives on YhhmEf with a t2v model key")
def _t2v_submit(world: dict[str, Any]) -> None:
    page = world["page"]
    page.scripted_request = ("YhhmEf", _submit_body(MEDIA_UP, "veo_3_1_t2v_lite"))
    page.scripted_responses[0] = (
        _batch_url("YhhmEf"),
        _frame("YhhmEf", [None, 881, [[MEDIA]], [[_record(6)]]]),
    )


@given("maseQ answers 400")
def _upload_400(world: dict[str, Any]) -> None:
    world["hop"] = True
    world["page"].dom.maseq_reply = 400


# --- When ---------------------------------------------------------------------


def _run(world: dict[str, Any], request: GenerateVideoRequest) -> None:
    async def go() -> Any:
        return await world["transport"].generate_video(
            request=request, project_id="p1", download=False, poll_timeout_s=5.0
        )

    try:
        world["result"] = asyncio.run(go())
    except Exception as exc:  # noqa: BLE001 - the Then steps classify it
        world["error"] = exc


@when("gflow video i2v runs with an 8 s request")
def _i2v_8s(world: dict[str, Any]) -> None:
    _run(
        world,
        GenerateVideoRequest(
            prompt="a crane",
            mode=Mode.I2V,
            aspect=Aspect.LANDSCAPE,
            duration=8,
            start_image=world["frame"],
        ),
    )


@when("gflow video i2v runs with a local start frame and a local end frame")
def _i2v_both_frames(world: dict[str, Any]) -> None:
    _run(
        world,
        GenerateVideoRequest(
            prompt="a crane",
            mode=Mode.I2V,
            aspect=Aspect.LANDSCAPE,
            duration=8,
            start_image=world["frame"],
            end_image=world["end_frame"],
        ),
    )


# --- Then ---------------------------------------------------------------------


@then("the composer uploads the file and the maseQ reply names a media id")
def _uploaded(world: dict[str, Any]) -> None:
    assert "error" not in world, world.get("error")
    assert world["page"].dom.chosen_files == [str(world["frame"])]


@then(parsers.parse('the Start chip binds the asset listed under "{name}"'))
def _bound(world: dict[str, Any], name: str) -> None:
    dom = world["page"].dom
    assert dom.picked == [name] and dom.chip_bound


@then("the eb1hJf submit body carries that media id and an i2v model key")
def _i2v_body(world: dict[str, Any]) -> None:
    rpcid, body = world["page"].scripted_request
    assert rpcid == "eb1hJf" and MEDIA_UP in body and "_i2v_" in body
    assert world["page"].dom.submit_clicked == 1


@then("the result reports success with the workflow id")
def _success(world: dict[str, Any]) -> None:
    result = world["result"]
    assert result.status.succeeded and result.project_id == "p1"
    assert result.flow_operation_id == WF


@then("the run fails with exit 32 before any submit")
def _exit_32(world: dict[str, Any]) -> None:
    assert isinstance(world.get("error"), ReferenceNotFoundError), world.get("error")
    assert EXIT_CODE_MAP[ReferenceNotFoundError] == 32
    assert world["page"].dom.submit_clicked == 0


@then("the detail names the file and the picker")
def _detail_names(world: dict[str, Any]) -> None:
    text = str(world["error"])
    assert world["frame"].name in text and "picker" in text


@then("the run fails with exit 7 naming the t2v key on an i2v request")
def _exit_7(world: dict[str, Any]) -> None:
    exc = world.get("error")
    assert isinstance(exc, WireFormatError), exc
    assert EXIT_CODE_MAP[WireFormatError] == 7
    assert "t2v" in str(exc) and "i2v" in str(exc)


@then("the run fails with exit 27 naming route batchexecute:maseQ")
def _exit_27(world: dict[str, Any]) -> None:
    exc = world.get("error")
    assert isinstance(exc, MediaUploadRejectedError), exc
    assert EXIT_CODE_MAP[MediaUploadRejectedError] == 27
    assert exc.route == "batchexecute:maseQ"


@then("no submit was clicked")
def _no_submit(world: dict[str, Any]) -> None:
    assert world["page"].dom.submit_clicked == 0


@then("the run fails with exit 36 and the remediation names the end frame")
def _exit_36_end_frame(world: dict[str, Any]) -> None:
    exc = world.get("error")
    assert isinstance(exc, FlowHostMigratedError), exc
    assert EXIT_CODE_MAP[FlowHostMigratedError] == 36
    assert "end frame" in str(exc)
    assert world["page"].dom.submit_clicked == 0


@then("the labs driver serves the request")
def _labs_served(world: dict[str, Any]) -> None:
    assert isinstance(world.get("error"), _LabsDriverTouchedError), world.get("error")
    assert world["page"].dom.submit_clicked == 0
