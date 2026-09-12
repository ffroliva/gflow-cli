"""Where the migrated composer is chosen (Task 5 of the migrated-host-driver plan).

`_generate_video_locked` decides the route twice: before entering the project
(the bootstrap page may already have hopped, or the host is forced) and after
(the hop is a client-side navigation the labs app performs once the project page
has loaded). `labs.google` as the setting is the kill switch — a moved account
keeps exit 36 exactly as before the driver existed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports import ui_automation_video
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
from gflow_cli.errors import (
    ConfigurationError,
    FlowHostMigratedError,
    UiSelectorDriftError,
)

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


async def test_run_video_refuses_character_references_on_every_mode() -> None:
    """#716: the refusal was behind the R2V branch, so t2v carried an entity into a
    BILLED generation that never attached it.

    T2V returned early from ``_unported_form`` before ``reference_entities`` was ever
    inspected, and nothing downstream attaches one — ``attach_start_frame`` is i2v-only,
    ``attach_references`` is r2v-only, and the ``read_chips`` verification is r2v-only.
    So the user paid for a clip of a stranger with no warning, which is strictly worse
    than exit 36: a refusal is free and honest.

    ``migrated_can_serve`` does refuse on ``reference_entities``, but it only feeds
    ``prefer_migrated``, and an account Flow has already moved is routed by its URL
    without consulting it — so the refusal was unreachable for exactly the accounts
    that needed it.
    """
    for mode in (Mode.T2V, Mode.R2V):
        with pytest.raises(FlowHostMigratedError, match="character references"):
            await _run(_req(mode=mode, reference_entities=("ent-1",)))
    # I2V is absent from that loop on purpose, and the reason is worth pinning: the DTO
    # itself refuses the combination (`_validate_i2v_symmetry`, api/video.py), so an i2v
    # request carrying entities cannot be constructed at all. Broadening the gate to every
    # mode therefore could not regress i2v -- there was no reachable i2v-with-entities
    # request to regress. Asserted rather than asserted-in-a-comment, so a future
    # relaxation of the DTO surfaces here instead of silently widening the gate's reach.
    with pytest.raises(ValueError, match="must not carry"):
        _req(mode=Mode.I2V, start_image_ref_name="hero", reference_entities=("ent-1",))


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


async def test_a_failed_composer_run_leaves_the_page_unparked_for_the_incident_capture(
    harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#792: the incident bundle is staged by ``FlowApiClient._capture_incident``
    "while the page is still alive". The park ran in a bare ``finally``, so on the
    FAILURE path it navigated to about:blank before the capture — every migrated
    video failure shipped ``tag_counts.div = 0``, a white screenshot and
    ``host_category = "other"`` while the run's own network journal showed the app
    alive. The park is deferred; the next run drains it."""

    async def _boom(*_: Any, **__: Any) -> VideoResult:
        raise UiSelectorDriftError(
            detail="migrated host: the frame picker stayed open 15s after picking 'k.jpg'"
        )

    monkeypatch.setattr("gflow_cli.api.transports.migrated_composer.run_video", _boom)
    with pytest.raises(UiSelectorDriftError):
        await harness["transport"].generate_video(request=_req(), project_id="p1")
    assert harness["page"].url != "about:blank", "evidence destroyed before capture"
    assert harness["transport"]._deferred_park_pending is True  # noqa: SLF001


async def test_the_deferred_park_is_drained_before_the_next_route_decision(
    harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deferring must not drop the park: a stale project URL would route the NEXT
    request (the invariant ``test_a_composer_run_does_not_route_the_next_request_by
    _its_page`` pins). Draining at the client's failure boundary was NOT enough —
    ``generate_images`` is retried inside ``post_with_retry``, so a retryable 5xx
    never reaches that boundary and attempt 2 resumed on a mounted composer. The
    drain therefore runs at the top of the next locked body, and this pins that it
    happens BEFORE the route decision reads ``page.url``."""
    routed: list[str] = []
    real_route = ui_automation_video.migrated_route

    def _spy(url: str, *a: Any, **kw: Any) -> Any:
        routed.append(url)
        return real_route(url, *a, **kw)

    monkeypatch.setattr(ui_automation_video, "migrated_route", _spy)

    async def _boom(*_: Any, **__: Any) -> VideoResult:
        raise UiSelectorDriftError(detail="drift")

    monkeypatch.setattr("gflow_cli.api.transports.migrated_composer.run_video", _boom)
    with pytest.raises(UiSelectorDriftError):
        await harness["transport"].generate_video(request=_req(), project_id="p1")
    assert routed[0] == _LABS  # the failing run routed by the live URL
    assert harness["page"].url != "about:blank"

    async def _ok(_p: Any, request: Any, **kw: Any) -> VideoResult:
        harness["run_video"].append((request, kw))
        return _result()

    monkeypatch.setattr("gflow_cli.api.transports.migrated_composer.run_video", _ok)
    await harness["transport"].generate_video(request=_req(), project_id="p1")

    assert routed[1] == "about:blank", "the next run routed by the stale project URL"
    assert harness["transport"]._deferred_park_pending is False  # noqa: SLF001


async def test_a_cancelled_composer_run_still_parks_inline(
    harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The BaseException arm is the only one still parking inline — nothing stages a
    bundle for a cancel, so nothing is waiting on the page. It also latches, because
    a re-delivered cancel can pre-empt the park at its own `await`."""

    async def _cancel(*_: Any, **__: Any) -> VideoResult:
        raise asyncio.CancelledError

    monkeypatch.setattr("gflow_cli.api.transports.migrated_composer.run_video", _cancel)
    with pytest.raises(asyncio.CancelledError):
        await harness["transport"].generate_video(request=_req(), project_id="p1")
    assert harness["page"].url == "about:blank"
    assert harness["transport"]._deferred_park_pending is True  # noqa: SLF001
