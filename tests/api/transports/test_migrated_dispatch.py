"""Where the migrated composer is chosen (Task 5 of the migrated-host-driver plan).

`_generate_video_locked` decides the route twice: before entering the project
(the bootstrap page may already have hopped, or the host is forced) and after
(the hop is a client-side navigation the labs app performs once the project page
has loaded). `labs.google` as the setting is the kill switch — a moved account
keeps exit 36 exactly as before the driver existed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin
from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoResult,
    VideoStatus,
)
from gflow_cli.config import reset_settings
from gflow_cli.errors import ConfigurationError, FlowHostMigratedError

_LABS = "https://labs.google/fx/en/tools/flow/project/p1"
_MIGRATED = "https://flow.google.com/project/p1"


class _LabsDriverTouchedError(Exception):
    """Sentinel: the labs driver bind was reached."""


def _result() -> VideoResult:
    return VideoResult(
        status=VideoStatus(media_id="m1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=None,
        project_id="p1",
        flow_operation_id="wf1",
    )


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A transport whose editor-entry steps are stubbed and whose two possible
    destinations — the labs driver bind and the migrated composer — are sentinels."""
    transport = UiAutomationTransport()
    page = MagicMock()
    page.url = _LABS
    transport._page = page  # noqa: SLF001
    transport._setup_done = True  # noqa: SLF001
    state: dict[str, Any] = {"flow_host": "auto", "hop_on_enter": False, "run_video": []}

    async def _enter(_page: Any, _out: Any, *, project_id: str | None = None, **_: Any) -> None:
        state["entered"] = project_id
        if state["hop_on_enter"]:
            page.url = _MIGRATED

    monkeypatch.setattr(transport, "_enter_editor", _enter)
    monkeypatch.setattr(VideoGenerationMixin, "_wait_video_editor_ready", AsyncMock())
    monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())

    def _set_flow_host(value: str) -> None:
        # The real Settings object: the conftest teardown clears its cache, and
        # the labs path reads other fields (ui_mode) from the same instance.
        monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", value)
        reset_settings()

    state["set_flow_host"] = _set_flow_host
    _set_flow_host("auto")

    async def _labs_bind(*_: Any, **__: Any) -> Any:
        raise _LabsDriverTouchedError

    monkeypatch.setattr("gflow_cli.api.transports.drivers.factory.get_ui_driver", _labs_bind)

    async def _run_video(_t: Any, _p: Any, request: Any, **kw: Any) -> VideoResult:
        state["run_video"].append((request, kw))
        return _result()

    monkeypatch.setattr("gflow_cli.api.transports.migrated_composer.run_video", _run_video)
    state["transport"], state["page"] = transport, page
    return state


def _req(**kw: Any) -> GenerateVideoRequest:
    base: dict[str, Any] = {"prompt": "a crane", "mode": Mode.T2V, "aspect": Aspect.LANDSCAPE}
    base.update(kw)
    return GenerateVideoRequest(**base)


async def test_flagged_account_is_routed_to_the_composer_after_the_hop(
    harness: dict[str, Any],
) -> None:
    """A request the new host cannot take at first sight (no project → the labs
    gallery would create one) goes through labs project entry; when that entry
    hops to flow.google.com, the second route decision hands it to the composer
    (which then reports the missing project itself — stubbed here)."""
    harness["hop_on_enter"] = True
    result = await harness["transport"].generate_video(request=_req(), project_id=None)
    assert result.flow_operation_id == "wf1"
    assert "entered" in harness  # labs entry ran (project_id None)
    assert len(harness["run_video"]) == 1
    assert harness["run_video"][0][1]["project_id"] is None


async def test_unmoved_account_with_a_project_goes_to_flow_google_com_by_default(
    harness: dict[str, Any],
) -> None:
    """The new host is the default for what it can serve — t2v in an existing
    project — on an UNMOVED account too (proven live on the pt profile)."""
    await harness["transport"].generate_video(request=_req(), project_id="p1")
    assert "entered" not in harness  # the composer navigates directly
    assert len(harness["run_video"]) == 1


async def test_unmoved_account_without_a_project_keeps_the_labs_driver(
    harness: dict[str, Any],
) -> None:
    """Project creation is not ported to the new host, so the labs gallery does it."""
    with pytest.raises(_LabsDriverTouchedError):
        await harness["transport"].generate_video(request=_req(), project_id=None)
    assert harness["run_video"] == []


async def test_unmoved_account_i2v_keeps_the_labs_driver(harness: dict[str, Any]) -> None:
    with pytest.raises(_LabsDriverTouchedError):
        await harness["transport"].generate_video(
            request=_req(mode=Mode.I2V, start_image_ref_name="asset"), project_id="p1"
        )
    assert harness["run_video"] == []


async def test_unmoved_account_labs_only_model_keeps_the_labs_driver(
    harness: dict[str, Any],
) -> None:
    from gflow_cli.api.video import VideoModel

    with pytest.raises(_LabsDriverTouchedError):
        await harness["transport"].generate_video(
            request=_req(model=VideoModel.VEO_3_1_LITE_LOWER_PRIORITY), project_id="p1"
        )
    assert harness["run_video"] == []


async def test_kill_switch_keeps_the_labs_driver_on_an_unmoved_account(
    harness: dict[str, Any],
) -> None:
    harness["set_flow_host"]("labs.google")
    with pytest.raises(_LabsDriverTouchedError):
        await harness["transport"].generate_video(request=_req(), project_id="p1")
    assert harness["run_video"] == []


async def test_forced_host_skips_the_labs_project_entry(harness: dict[str, Any]) -> None:
    harness["set_flow_host"]("flow.google.com")
    await harness["transport"].generate_video(request=_req(), project_id="p1")
    assert "entered" not in harness  # the composer navigates directly
    assert len(harness["run_video"]) == 1


async def test_kill_switch_keeps_exit_36_on_a_moved_account(harness: dict[str, Any]) -> None:
    harness["set_flow_host"]("labs.google")
    harness["hop_on_enter"] = True
    with pytest.raises(FlowHostMigratedError) as exc_info:
        await harness["transport"].generate_video(request=_req(), project_id="p1")
    assert "GFLOW_CLI_FLOW_HOST" in exc_info.value.remediation_hint
    assert harness["run_video"] == []


async def test_bootstrap_page_already_on_the_migrated_host_routes_before_entry(
    harness: dict[str, Any],
) -> None:
    harness["page"].url = _MIGRATED
    await harness["transport"].generate_video(request=_req(), project_id="p1")
    assert "entered" not in harness
    assert len(harness["run_video"]) == 1


# --- run_video guards (direct) ------------------------------------------------


class _NoComposer:
    """run_video must reject these requests BEFORE touching the page."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"composer touched: {name}")


async def _run(
    request: GenerateVideoRequest, *, url: str = _MIGRATED, project_id: str | None = "p1"
) -> Any:
    from gflow_cli.api.transports import migrated_composer

    page = MagicMock()
    page.url = url
    return await migrated_composer.run_video(
        _NoComposer(),
        page,
        request,
        project_id=project_id,
        out_dir=Path("."),
        poll_timeout_s=1.0,
        download=False,
        on_started=None,
    )


async def test_run_video_rejects_modes_not_yet_ported_with_exit_36() -> None:
    with pytest.raises(FlowHostMigratedError, match="i2v"):
        await _run(_req(mode=Mode.I2V, start_image_ref_name="asset"))


async def test_run_video_needs_a_project_on_the_migrated_host() -> None:
    with pytest.raises(ConfigurationError, match="--project"):
        await _run(_req(), url="https://flow.google.com/", project_id=None)
