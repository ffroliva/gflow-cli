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

    async def _goto(url: str, **_: Any) -> None:
        page.url = url

    page.goto = _goto
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

    async def _run_video(_p: Any, request: Any, **kw: Any) -> VideoResult:
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


async def test_a_composer_run_does_not_route_the_next_request_by_its_page(
    harness: dict[str, Any],
) -> None:
    """D1 council: after a composer run the pooled page sat on flow.google.com, so
    the next request on the same client was routed by that URL. It is parked."""
    await harness["transport"].generate_video(request=_req(), project_id="p1")
    assert harness["page"].url == "about:blank"
    with pytest.raises(_LabsDriverTouchedError):  # i2v on the same client → labs
        await harness["transport"].generate_video(
            request=_req(mode=Mode.I2V, start_image_ref_name="asset"), project_id="p1"
        )
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


async def _run(
    request: GenerateVideoRequest, *, url: str = _MIGRATED, project_id: str | None = "p1"
) -> Any:
    from gflow_cli.api.transports import migrated_composer

    page = MagicMock()
    page.url = url
    return await migrated_composer.run_video(
        page,
        request,
        project_id=project_id,
        out_dir=Path("."),
        poll_timeout_s=1.0,
        download=False,
        on_started=None,
    )


async def test_run_video_rejects_modes_not_yet_ported_with_exit_36() -> None:
    # r2v from LOCAL files is ported; a reference given by name is not, for the same
    # reason a frame by UUID is not — the picker exposes no media id, so there is
    # nothing to anchor the pick on and nothing to assert on the submit body.
    with pytest.raises(FlowHostMigratedError, match="by name"):
        await _run(_req(mode=Mode.R2V, ref_names=("asset",)))
    with pytest.raises(FlowHostMigratedError, match="character references"):
        await _run(_req(mode=Mode.R2V, reference_entities=("ent-1",)))


async def test_run_video_needs_a_project_on_the_migrated_host() -> None:
    with pytest.raises(ConfigurationError, match="--project"):
        await _run(_req(), url="https://flow.google.com/", project_id=None)


# --- i2v (slice 1: a local start frame) ---------------------------------------


def _png(tmp_path: Path, name: str = "hero.png") -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    return path


_UUID = "33333333-3333-4333-8333-333333333333"


def test_migrated_can_serve_takes_i2v_only_with_a_local_start_frame(tmp_path: Path) -> None:
    from gflow_cli.api.transports.migrated_composer import migrated_can_serve
    from gflow_cli.api.video import VideoModel

    png = _png(tmp_path)
    assert migrated_can_serve(_req(mode=Mode.I2V, start_image=png), "p1")
    assert migrated_can_serve(
        _req(mode=Mode.I2V, start_image=png, model=VideoModel.VEO_3_1_LITE), "p1"
    )
    assert not migrated_can_serve(_req(mode=Mode.I2V, start_image=png), None)
    assert not migrated_can_serve(_req(mode=Mode.I2V, start_image=png, end_image=png), "p1")
    assert not migrated_can_serve(_req(mode=Mode.I2V, start_image_ref_id=_UUID), "p1")
    assert not migrated_can_serve(_req(mode=Mode.I2V, start_image_ref_name="hero"), "p1")
    assert not migrated_can_serve(
        _req(mode=Mode.I2V, start_image=png, model=VideoModel.VEO_3_1_LITE_LOWER_PRIORITY), "p1"
    )


async def test_i2v_with_a_local_start_frame_is_served_by_the_migrated_host(
    harness: dict[str, Any], tmp_path: Path
) -> None:
    await harness["transport"].generate_video(
        request=_req(mode=Mode.I2V, start_image=_png(tmp_path)), project_id="p1"
    )
    assert "entered" not in harness  # the composer navigates directly
    assert len(harness["run_video"]) == 1


async def test_i2v_with_an_end_frame_keeps_the_labs_driver_on_an_unmoved_account(
    harness: dict[str, Any], tmp_path: Path
) -> None:
    png = _png(tmp_path)
    with pytest.raises(_LabsDriverTouchedError):
        await harness["transport"].generate_video(
            request=_req(mode=Mode.I2V, start_image=png, end_image=png), project_id="p1"
        )
    assert harness["run_video"] == []


async def test_i2v_by_uuid_keeps_the_labs_driver_on_an_unmoved_account(
    harness: dict[str, Any],
) -> None:
    with pytest.raises(_LabsDriverTouchedError):
        await harness["transport"].generate_video(
            request=_req(mode=Mode.I2V, start_image_ref_id=_UUID), project_id="p1"
        )
    assert harness["run_video"] == []


async def test_run_video_names_the_end_frame_in_the_exit_36_detail(tmp_path: Path) -> None:
    png = _png(tmp_path)
    with pytest.raises(FlowHostMigratedError, match="end frame") as ei:
        await _run(_req(mode=Mode.I2V, start_image=png, end_image=png))
    assert "--initial-frame" in str(ei.value)


async def test_run_video_names_the_uuid_form_in_the_exit_36_detail() -> None:
    with pytest.raises(FlowHostMigratedError, match="UUID"):
        await _run(_req(mode=Mode.I2V, start_image_ref_id=_UUID))
    with pytest.raises(FlowHostMigratedError, match="@Name"):
        await _run(_req(mode=Mode.I2V, start_image_ref_name="hero"))


# --- `gflow video chain` on the new host (SCENARIO row 11) ---------------------
# A chain link is an i2v request whose start frame is the previous clip's extracted
# last frame — the served form — but `chain.py` calls `generate_video(req=...)`
# WITHOUT a project id (`_build_link_request`, `_generate_one`). Both halves of that
# are pinned here: the shape routes to labs on an unmoved account, and on a moved one
# it aborts naming `--project` instead of silently generating somewhere else.


async def test_a_chain_shaped_link_keeps_the_labs_driver_on_an_unmoved_account(
    harness: dict[str, Any], tmp_path: Path
) -> None:
    with pytest.raises(_LabsDriverTouchedError):
        await harness["transport"].generate_video(
            request=_req(mode=Mode.I2V, start_image=_png(tmp_path, "link-1.png")), project_id=None
        )
    assert harness["run_video"] == []


async def test_a_chain_shaped_link_on_a_moved_account_names_the_missing_project(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError, match="--project"):
        await _run(
            _req(mode=Mode.I2V, start_image=_png(tmp_path, "link-1.png")),
            url="https://flow.google.com/",
            project_id=None,
        )
