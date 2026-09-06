"""E2E: Flow's migrated ``flow.google.com`` host through the real transport.

Google is moving accounts from labs.google onto flow.google.com (#639). Under the
default ``GFLOW_CLI_FLOW_HOST=auto`` the new host is where every ``video t2v`` with
an existing project runs — on moved and unmoved accounts alike — so these tests
hold for any logged-in profile. They need a project id on that host::

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_PROJECT=<project-uuid> \\
        uv run pytest -m e2e tests/e2e/test_migrated_host_e2e.py -v

Cost: the ``e2e_video`` test bills ONE 8 s clip (12 credits at the measured cohort
rate). The ``e2e_auth`` tests spend nothing — they stop before any submit.
Live evidence for the shipped build: ``docs/LIVE_VERIFICATION_v0.67.0.md``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import structlog

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.transports._common import flow_host_kind
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoResult
from gflow_cli.config import reset_settings
from gflow_cli.errors import FlowHostMigratedError

pytestmark = pytest.mark.e2e

_PROJECT_ENV = "GFLOW_CLI_E2E_PROJECT"
_PROMPT = "a teal origami crane on a wooden table, slow push in"
_POLL_TIMEOUT_S = 600.0


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


def _write_reference(path: Path) -> Path:
    """Write a real 1024x1024 PNG.

    Not a 1x1: Flow's upload answered ``maseQ`` 200 with **no media id** for a 64x64
    synthetic one (measured), which surfaces as MediaUploadRejectedError. The point of
    this test is the attach path, so the fixture has to clear whatever size floor the
    uploader applies rather than probe it.
    """
    import struct
    import zlib

    side = 1024

    def _chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (
            struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    # A simple gradient — real image content, and it compresses to a sane size.
    rows = bytearray()
    for y in range(side):
        rows.append(0)  # filter: none
        for x in range(side):
            rows += bytes(((x * 255) // side, (y * 255) // side, 128))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + _chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    return path


def _events(capture: structlog.testing.LogCapture, prefix: str) -> list[str]:
    return [str(e["event"]) for e in capture.entries if str(e["event"]).startswith(prefix)]


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_migrated_host_serves_this_account(
    e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """$0: a direct load of flow.google.com/project/<id> renders the migrated
    editor for this account — moved or not (measured on both kinds 2026-09-05)."""
    project = _project_id()
    _set_flow_host(monkeypatch, None)
    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_profile_dir)
        page = transport._page  # noqa: SLF001 - the e2e reads the live page
        assert page is not None
        await MigratedComposer().ensure_editor(page, project, timeout_s=45.0)
        assert flow_host_kind(page.url) == "migrated", page.url
        assert await page.locator(".settings-trigger-button").first.count() == 1
    finally:
        await transport.teardown()


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_kill_switch_keeps_exit_36_on_a_moved_account(
    e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """$0: with GFLOW_CLI_FLOW_HOST=labs.google a MOVED account must still get the
    distinct exit-36 error (never a selector-drift 23), before any submit.
    Skips on an unmoved account — there is nothing to switch off there."""
    project = _project_id()
    _set_flow_host(monkeypatch, "labs.google")
    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_profile_dir)
        page = transport._page  # noqa: SLF001
        assert page is not None
        if flow_host_kind(page.url) != "migrated":
            pytest.skip("profile is not on the migrated host; the kill switch has nothing to block")
        req = GenerateVideoRequest(prompt=_PROMPT, mode=Mode.T2V, aspect=Aspect.LANDSCAPE)
        with pytest.raises(FlowHostMigratedError) as exc_info:
            await transport.generate_video(request=req, project_id=project, download=False)
        assert "GFLOW_CLI_FLOW_HOST" in exc_info.value.remediation_hint
    finally:
        await transport.teardown()


@pytest.mark.asyncio
@pytest.mark.e2e_video
async def test_e2e_t2v_runs_on_flow_google_com_by_default(
    e2e_profile_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """Bills one clip. Under the default routing a t2v with a project is served by
    the new host on ANY account: the migrated composer must dispatch, observe the
    submit/status/result replies, and land a real mp4 — the five-layer ledger."""
    project = _project_id()
    _set_flow_host(monkeypatch, os.environ.get("GFLOW_CLI_E2E_FLOW_HOST") or None)
    req = GenerateVideoRequest(
        prompt=_PROMPT,
        mode=Mode.T2V,
        aspect=Aspect.LANDSCAPE,
        duration=int(os.environ.get("GFLOW_CLI_E2E_VIDEO_DURATION", "8")),
    )
    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_profile_dir)
        result: VideoResult = await transport.generate_video(
            request=req, project_id=project, out_dir=tmp_path, poll_timeout_s=_POLL_TIMEOUT_S
        )
    finally:
        await transport.teardown()

    # 1. The migrated composer handled it — not the labs driver.
    seen = _events(install_log_capture, "migrated.")
    for required in ("migrated.dispatch", "migrated.submit_observed", "migrated.result"):
        assert required in seen, f"{required} missing; migrated events: {seen}"
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


@pytest.mark.asyncio
@pytest.mark.e2e_video
async def test_e2e_r2v_binds_local_references_on_the_migrated_host(
    e2e_profile_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """Bills one clip. Two LOCAL ``--ref`` files must upload through the editor, attach
    as `@` mentions, and ride the ``MZZa6b`` submit.

    The layer that matters is the last one. Chips in the DOM and ids in the log only
    prove Flow ACCEPTED the references; ``_r2v_body_problem`` asserting the submit body
    carries every uploaded id is what proves the run is the one the caller asked for —
    the failure mode being a full-price clip with none of them on it. That assertion is
    armed inside ``submit_and_observe``, so a run that reached ``migrated.result``
    without it firing is the evidence.
    """
    refs = tuple(_write_reference(tmp_path / name) for name in ("ref_one.png", "ref_two.png"))
    project = _project_id()
    _set_flow_host(monkeypatch, os.environ.get("GFLOW_CLI_E2E_FLOW_HOST") or None)
    req = GenerateVideoRequest(
        prompt=_PROMPT,
        mode=Mode.R2V,
        aspect=Aspect.PORTRAIT,
        reference_images=refs,
    )
    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_profile_dir)
        result: VideoResult = await transport.generate_video(
            request=req, project_id=project, out_dir=tmp_path, poll_timeout_s=_POLL_TIMEOUT_S
        )
    finally:
        await transport.teardown()

    # 1. The migrated composer handled it, through the reference stage.
    seen = _events(install_log_capture, "migrated.")
    for required in (
        "migrated.dispatch",
        "migrated.references_attached",
        "migrated.submit_observed",
        "migrated.result",
    ):
        assert required in seen, f"{required} missing; migrated events: {seen}"

    # 2. Both references uploaded and were attached — not one, not zero.
    attached = [
        e for e in install_log_capture.entries if e.get("event") == "migrated.references_attached"
    ]
    assert attached and attached[-1]["count"] == len(refs), attached
    assert len(attached[-1]["media_ids"]) == len(refs), attached

    # 3. Terminal success and a real mp4, same contract as t2v.
    assert result.status.succeeded, result.status
    assert result.local_path is not None and result.local_path.exists()
    body = result.local_path.read_bytes()
    assert body[4:8] == b"ftyp", body[:12]
    assert len(body) > 100_000, len(body)


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_e2e_image_on_a_moved_account_exits_36_not_recaptcha(
    e2e_profile_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """$0 (#673): ``image t2i --project`` on a MOVED account must fail with the
    distinct exit-36 error, not a bare RecaptchaError. The labs client mints the
    reCAPTCHA token on the pool's bootstrap page before the transport runs; on a
    moved account that page is the flow.google.com grid, which has no
    recaptcha/enterprise.js. Measured 2026-09-05: exit 1 in 13 s before the fix.
    Skips on an unmoved account — the labs page carries the script there."""
    project = _project_id()
    _set_flow_host(monkeypatch, None)
    req = GenerateImageRequest(
        prompt="a teal origami crane on a wooden table", aspect=ImageAspect.LANDSCAPE
    )
    async with FlowApiClient(profile_dir=e2e_profile_dir, out_dir=tmp_path) as client:
        page = client._page  # noqa: SLF001 - the e2e reads the live page
        assert page is not None
        if flow_host_kind(page.url) != "migrated":
            pytest.skip("profile is not on the migrated host; the labs page mints fine")
        with pytest.raises(FlowHostMigratedError):
            await client.generate_image(project_id=project, req=req)

    bails = [
        e for e in install_log_capture.entries if e.get("event") == "ui_driver.migrated_host_bail"
    ]
    assert bails and bails[0].get("at") == "mint_recaptcha_token", bails
    # The page the mint saw is the migrated origin (the grid, route "/", in the
    # reporter's bundle) — not a labs page that merely lost its script.
    assert flow_host_kind(str(bails[0].get("url"))) == "migrated", bails[0]
