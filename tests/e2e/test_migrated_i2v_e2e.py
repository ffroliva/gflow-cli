"""E2E: image-to-video from a LOCAL start frame on Flow's migrated ``flow.google.com``
host (#639, slice 1), through the real transport.

The start frame goes in the way the editor takes it — the toolbar Upload entry, the
app's own ``maseQ`` upload, the Frames picker searched by file name — and the run is
trusted only once the app's submit *request* carries that upload's media id with an
i2v model key. These tests need a project id on that host::

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_PROJECT=<project-uuid> \\
        uv run pytest -m e2e tests/e2e/test_migrated_i2v_e2e.py -v

Cost: the ``e2e_video`` test bills ONE clip at the account's cohort rate. The
``e2e_auth`` test spends nothing — it uploads and binds a probe image, then stops
before any submit (the upload stays in the project's library, like any upload).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
import structlog

from gflow_cli.api.transports._common import flow_host_kind
from gflow_cli.api.transports.migrated_composer import BOUND_CHIP, MigratedComposer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoResult
from gflow_cli.config import reset_settings

pytestmark = pytest.mark.e2e

_PROJECT_ENV = "GFLOW_CLI_E2E_PROJECT"
_PROMPT = "the sphere slowly rolls to the right, soft studio light"
_POLL_TIMEOUT_S = 600.0
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _project_id() -> str:
    pid = os.environ.get(_PROJECT_ENV, "").strip()
    if not pid:
        pytest.skip(f"{_PROJECT_ENV} must name an existing Flow project id (see module doc)")
    return pid


def _set_flow_host(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    else:
        monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", value)
    reset_settings()


def _probe_png(directory: Path, name: str) -> Path:
    """A real 256x256 PNG (a blue sphere on grey) — Flow's upload endpoint rejects
    a bare header-only file, and the picker lists the upload under this name."""
    from PIL import Image, ImageDraw  # dev dependency; e2e runs with the dev group

    img = Image.new("RGB", (256, 256), (70, 70, 70))
    ImageDraw.Draw(img).ellipse((64, 64, 192, 192), fill=(40, 90, 220))
    path = directory / name
    img.save(path, format="PNG")
    return path


def _events(capture: structlog.testing.LogCapture, prefix: str) -> list[dict[str, object]]:
    return [dict(e) for e in capture.entries if str(e["event"]).startswith(prefix)]


def _dump_events(capture: structlog.testing.LogCapture, out_dir: Path) -> None:
    """The captured timeline is the verification evidence; the LogCapture processor
    keeps it off stdout, so write it next to the run's other artifacts."""
    (out_dir / "migrated-events.json").write_text(
        json.dumps(
            [
                {k: str(v) for k, v in e.items()}
                for e in capture.entries
                if str(e["event"]).startswith(("migrated.", "ui_driver."))
            ],
            indent=1,
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_start_frame_uploads_and_binds_on_the_migrated_host(
    e2e_profile_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """$0: on the real migrated editor the attach stage uploads a probe PNG (the
    app's ``maseQ`` reply names a media id), finds it in the Frames picker under its
    file name and binds it on the Start chip — measured, not submitted.

    Also proves the #125 default live: the request carries no model, and the editor
    must be driven to veo-lite rather than left on whatever tier it remembered."""
    project = _project_id()
    _set_flow_host(monkeypatch, None)
    frame = _probe_png(tmp_path, f"gflow-e2e-probe-{os.getpid()}.png")
    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_profile_dir)
        page = transport._page  # noqa: SLF001 - the e2e reads the live page
        assert page is not None
        composer = MigratedComposer()
        await composer.ensure_editor(page, project, timeout_s=45.0)
        assert flow_host_kind(page.url) == "migrated", page.url
        await composer.apply_video_settings(
            page,
            GenerateVideoRequest(
                prompt=_PROMPT, mode=Mode.I2V, aspect=Aspect.LANDSCAPE, start_image=frame
            ),
        )
        media_id = await composer.attach_start_frame(page, project, frame)
        assert _UUID.match(media_id), media_id
        assert await page.locator(BOUND_CHIP).count() >= 1
    finally:
        await transport.teardown()

    _dump_events(install_log_capture, tmp_path)
    uploaded = _events(install_log_capture, "migrated.frame_uploaded")
    bound = _events(install_log_capture, "migrated.frame_bound")
    assert uploaded and uploaded[0]["media_id"] == media_id
    assert bound and bound[0]["media_id"] == media_id
    assert frame.name not in str(uploaded)  # the file name never reaches a log line

    # #125 live: no model was requested, so the composer must have bound the i2v
    # default and driven the picker to it (or read it back as already selected).
    assert _events(install_log_capture, "migrated.i2v_model_defaulted")
    selected = _events(install_log_capture, "migrated.model_selected") or _events(
        install_log_capture, "migrated.model_already_selected"
    )
    assert selected, "the model picker was never touched for an i2v run with no --model"
    assert "Lite" in str(selected[0]["model"]), selected[0]


@pytest.mark.asyncio
@pytest.mark.e2e_video
async def test_e2e_i2v_from_a_local_start_frame_runs_on_flow_google_com(
    e2e_profile_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """Bills one clip. Under the default routing an i2v with a local start frame and
    a project is served by the new host: upload observed, chip bound, the ``eb1hJf``
    submit body carrying the upload's media id, then the five-layer ledger."""
    project = _project_id()
    _set_flow_host(monkeypatch, os.environ.get("GFLOW_CLI_E2E_FLOW_HOST") or None)
    frame = _probe_png(tmp_path, f"gflow-e2e-i2v-{os.getpid()}.png")
    # No duration unless asked: in the Frames submode this cohort's pane renders no
    # duration row for the default tier (#650), and forcing one is a $0 exit 11.
    duration_env = os.environ.get("GFLOW_CLI_E2E_VIDEO_DURATION", "").strip()
    req = GenerateVideoRequest(
        prompt=_PROMPT,
        mode=Mode.I2V,
        aspect=Aspect.LANDSCAPE,
        start_image=frame,
        duration=int(duration_env) if duration_env else None,
    )
    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_profile_dir)
        result: VideoResult = await transport.generate_video(
            request=req, project_id=project, out_dir=tmp_path, poll_timeout_s=_POLL_TIMEOUT_S
        )
    finally:
        await transport.teardown()
        _dump_events(install_log_capture, tmp_path)

    # 1. The migrated composer handled it as i2v — upload, bind, then the i2v submit.
    events = [str(e["event"]) for e in install_log_capture.entries]
    for required in (
        "migrated.dispatch",
        "migrated.frame_uploaded",
        "migrated.frame_bound",
        "migrated.submit_observed",
        "migrated.result",
    ):
        assert required in events, f"{required} missing; migrated events: {events}"
    observed = _events(install_log_capture, "migrated.submit_observed")[0]
    assert observed["rpc"] == "eb1hJf", observed
    assert not _events(install_log_capture, "ui_driver.migrated_host_bail")

    # 2. Terminal-success contract, same as the labs path.
    assert result.status.succeeded, result.status
    assert result.status.media_id and result.flow_operation_id
    assert result.project_id == project

    # 3. File-on-disk: an mp4 with its container magic, not a poster JPEG.
    assert result.local_path is not None and result.local_path.exists()
    body = result.local_path.read_bytes()
    assert body[4:8] == b"ftyp", body[:12]
    assert len(body) > 100_000, len(body)
