"""Click-runner tests for the `gflow video t2v` command (Phase B restoration)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.cli_video import video
from gflow_cli.data.models import AssetKind


def _make_result(succeeded: bool, local_path: Path | None = None) -> object:
    """Build a fake VideoResult."""
    from gflow_cli.api.video import VideoResult, VideoStatus

    status = VideoStatus(
        media_id="test-uuid",
        status=(
            "MEDIA_GENERATION_STATUS_SUCCESSFUL" if succeeded else "MEDIA_GENERATION_STATUS_FAILED"
        ),
    )
    return VideoResult(status=status, local_path=local_path)


def test_t2v_requires_prompt() -> None:
    runner = CliRunner()
    result = runner.invoke(video, ["t2v"])
    assert result.exit_code != 0


def test_t2v_invokes_transport_and_prints_path(tmp_path: Path) -> None:
    runner = CliRunner()
    expected_path = tmp_path / "test-uuid.mp4"
    expected_path.touch()
    _ = _make_result(succeeded=True, local_path=expected_path)

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._run_t2v", new_callable=AsyncMock) as mock_run,
    ):
        mock_run.return_value = None
        result = runner.invoke(video, ["t2v", "a golden sunset"])

    assert result.exit_code == 0
    mock_run.assert_awaited_once()


def test_t2v_accepts_aspect_option(tmp_path: Path) -> None:
    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._run_t2v", new_callable=AsyncMock),
    ):
        result = runner.invoke(video, ["t2v", "prompt", "--aspect", "16:9"])
    assert result.exit_code == 0


def test_t2v_rejects_invalid_aspect(tmp_path: Path) -> None:
    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
    ):
        result = runner.invoke(video, ["t2v", "prompt", "--aspect", "4:3"])
    assert result.exit_code != 0


def test_t2v_does_not_instantiate_ui_automation_transport_directly(tmp_path: Path) -> None:
    """After Task 7, _run_t2v must go through FlowApiClient, not UiAutomationTransport directly.

    We verify this by monkeypatching UiAutomationTransport.__init__ to blow up,
    and patching FlowApiClient.generate_video to return a stub result. If the CLI
    path still instantiates UiAutomationTransport directly, the test will fail.
    """
    from gflow_cli.api.video import VideoResult, VideoStatus

    stub_result = VideoResult(
        status=VideoStatus(media_id="test-uuid", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=tmp_path / "test-uuid.mp4",
    )
    (tmp_path / "test-uuid.mp4").touch()

    runner = CliRunner()

    def _sentinel_init(self: object, *args: object, **kwargs: object) -> None:
        raise AssertionError(
            "UiAutomationTransport.__init__ was called directly — "
            "_run_t2v must route through FlowApiClient"
        )

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch(
            "gflow_cli.api.transports.ui_automation.UiAutomationTransport.__init__",
            _sentinel_init,
        ),
        patch(
            "gflow_cli.api.client.FlowApiClient.generate_video",
            new_callable=AsyncMock,
            return_value=stub_result,
        ),
        patch(
            "gflow_cli.api.client.FlowApiClient.__aenter__",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_enter,
        patch(
            "gflow_cli.api.client.FlowApiClient.__aexit__",
            new_callable=AsyncMock,
        ),
    ):
        # Patch __aenter__ to return the client itself
        from gflow_cli.api.client import FlowApiClient

        mock_enter.return_value = FlowApiClient.__new__(FlowApiClient)
        mock_enter.return_value.generate_video = AsyncMock(return_value=stub_result)
        result = runner.invoke(video, ["t2v", "a golden sunset"])

    # The sentinel must NOT have fired — exit_code 0 proves it (or at least no
    # AssertionError from the sentinel).
    assert "UiAutomationTransport.__init__ was called directly" not in (result.output or "")
    # Exit code may be non-zero for other reasons (profile resolution, etc.) but
    # the sentinel assertion must not appear.
    assert result.exception is None or not isinstance(result.exception, AssertionError)


class FakeVideoRecorder:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.completed: list[dict] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def record_started_video(self, **kwargs):
        self.started.append(kwargs)

    def record_completed_video(self, **kwargs):
        self.completed.append(kwargs)


def test_t2v_records_started_then_completed(tmp_path: Path) -> None:
    """Recorder.record_started_video is called via on_started callback, then
    record_completed_video is called after generate_video returns."""
    from gflow_cli.api.video import VideoResult, VideoStarted, VideoStatus

    saved = tmp_path / "test-uuid.mp4"
    saved.touch()

    stub_result = VideoResult(
        status=VideoStatus(
            media_id="m1",
            status="MEDIA_GENERATION_STATUS_SUCCESSFUL",
        ),
        local_path=saved,
        project_id="p1",
        flow_operation_id="o1",
    )

    fake_recorder = FakeVideoRecorder()

    async def fake_generate_video(
        *, req, out_dir, project_id=None, poll_timeout_s=None, download, on_started
    ):
        if on_started is not None:
            import inspect

            result_or_coro = on_started(
                VideoStarted(media_id="m1", project_id="p1", flow_operation_id="o1")
            )
            if inspect.isawaitable(result_or_coro):
                await result_or_coro
        return stub_result

    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.data.recorder.OperationRecorder.open", return_value=fake_recorder),
        patch(
            "gflow_cli.api.client.FlowApiClient.__aenter__",
            new_callable=AsyncMock,
        ) as mock_enter,
        patch(
            "gflow_cli.api.client.FlowApiClient.__aexit__",
            new_callable=AsyncMock,
        ),
    ):
        from gflow_cli.api.client import FlowApiClient

        fake_client = MagicMock(spec=FlowApiClient)
        fake_client.generate_video = fake_generate_video
        mock_enter.return_value = fake_client

        result = runner.invoke(video, ["t2v", "x"])

    assert result.exit_code == 0, result.output
    assert len(fake_recorder.started) == 1
    started_kwargs = fake_recorder.started[0]
    assert started_kwargs["profile_name"] == "default"
    assert started_kwargs["started"].media_id == "m1"
    assert len(fake_recorder.completed) == 1
    completed_kwargs = fake_recorder.completed[0]
    assert completed_kwargs["result"] is stub_result
    assert fake_recorder.closed is True


# ---------------------------------------------------------------------------
# --json output (mirrors the image json output tests).
# ---------------------------------------------------------------------------


def test_t2v_json_emits_clean_machine_readable_result(tmp_path: Path) -> None:
    """`gflow video t2v --json` emits a pure-JSON document on stdout (no
    progress chatter) when the generation succeeds."""
    import json as _json

    from gflow_cli.api.video import VideoResult, VideoStarted, VideoStatus

    saved = tmp_path / "test-uuid.mp4"
    saved.touch()
    stub_result = VideoResult(
        status=VideoStatus(media_id="m1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=saved,
        project_id="p1",
        flow_operation_id="o1",
    )

    fake_recorder = FakeVideoRecorder()

    async def fake_generate_video(
        *, req, out_dir, project_id=None, poll_timeout_s=None, download, on_started
    ):
        if on_started is not None:
            import inspect

            res = on_started(VideoStarted(media_id="m1", project_id="p1", flow_operation_id="o1"))
            if inspect.isawaitable(res):
                await res
        return stub_result

    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.data.recorder.OperationRecorder.open", return_value=fake_recorder),
        patch(
            "gflow_cli.api.client.FlowApiClient.__aenter__",
            new_callable=AsyncMock,
        ) as mock_enter,
        patch("gflow_cli.api.client.FlowApiClient.__aexit__", new_callable=AsyncMock),
    ):
        from gflow_cli.api.client import FlowApiClient

        fake_client = MagicMock(spec=FlowApiClient)
        fake_client.generate_video = fake_generate_video
        mock_enter.return_value = fake_client

        result = runner.invoke(video, ["t2v", "a sunset", "--json"])

    assert result.exit_code == 0, result.output
    # Pure JSON: anything that doesn't parse cleanly means progress chatter leaked.
    data = _json.loads(result.output)
    assert data["status"] == "ok"
    assert data["command"] == "video t2v"
    assert data["succeeded"] is True
    assert data["media_id"] == "m1"
    assert data["request"]["mode"] == "t2v"


def test_t2v_json_failed_gen_emits_exactly_one_payload(tmp_path: Path) -> None:
    """A failed `video t2v --json` must emit EXACTLY ONE JSON document on
    stdout and exit 1 — not two.

    Regression guard for the bug where `_generate_and_report` emitted the
    failed `video_result` payload + raised `SystemExit(1)`, and
    `run_with_handlers(as_json=True)`'s `except BaseException` clause caught
    the SystemExit and appended a SECOND `UnexpectedError` JSON document
    behind the first — making `json.loads(stdout)` raise `Extra data` and
    defeating the whole point of `--json` for a programmatic caller.
    """
    import json as _json

    from gflow_cli.api.video import VideoResult, VideoStarted, VideoStatus

    failed_result = VideoResult(
        status=VideoStatus(
            media_id="m_fail",
            status="MEDIA_GENERATION_STATUS_FAILED",
            failure_reasons=("safety_filter",),
        ),
        local_path=None,
        project_id="p_fail",
        flow_operation_id="o_fail",
    )

    fake_recorder = FakeVideoRecorder()

    async def fake_generate_video(
        *, req, out_dir, project_id=None, poll_timeout_s=None, download, on_started
    ):
        if on_started is not None:
            import inspect

            res = on_started(
                VideoStarted(media_id="m_fail", project_id="p_fail", flow_operation_id="o_fail")
            )
            if inspect.isawaitable(res):
                await res
        return failed_result

    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.data.recorder.OperationRecorder.open", return_value=fake_recorder),
        patch(
            "gflow_cli.api.client.FlowApiClient.__aenter__",
            new_callable=AsyncMock,
        ) as mock_enter,
        patch("gflow_cli.api.client.FlowApiClient.__aexit__", new_callable=AsyncMock),
    ):
        from gflow_cli.api.client import FlowApiClient

        fake_client = MagicMock(spec=FlowApiClient)
        fake_client.generate_video = fake_generate_video
        mock_enter.return_value = fake_client

        result = runner.invoke(video, ["t2v", "a sunset", "--json"])

    # Exit code matches the failed-gen contract.
    assert result.exit_code == 1, result.output
    # `json.loads` succeeds iff stdout is exactly ONE JSON document.
    # If a SECOND `UnexpectedError` payload leaks in (the old bug),
    # `json.loads` raises ``json.JSONDecodeError: Extra data``.
    data = _json.loads(result.output)
    assert data["status"] == "fail"
    assert data["command"] == "video t2v"
    assert data["succeeded"] is False
    assert data["media_id"] == "m_fail"
    assert data["generation_status"] == "MEDIA_GENERATION_STATUS_FAILED"
    assert data["failure_reasons"] == ["safety_filter"]
    # Belt-and-braces: assert no second top-level JSON object follows.
    # `{...}{...}` would parse only the first object with `raw_decode`, then
    # leave non-whitespace trailing chars — the assertion below catches that.
    decoder = _json.JSONDecoder()
    _, end = decoder.raw_decode(result.output)
    trailing = result.output[end:].strip()
    assert trailing == "", (
        f"stdout had a second JSON document after the failed-gen payload: {trailing[:200]!r}"
    )


def test_t2v_records_cloud_storage_info_for_downloaded_video(tmp_path: Path) -> None:
    from gflow_cli.api.video import VideoResult, VideoStarted, VideoStatus
    from gflow_cli.storage import CloudStorageInfo

    saved = tmp_path / "test-uuid.mp4"
    cloud_info = CloudStorageInfo(
        uri="s3://bucket/prefix/videos/2026-05-28/test-uuid.mp4",
        provider="s3",
    )
    stub_result = VideoResult(
        status=VideoStatus(
            media_id="m1",
            status="MEDIA_GENERATION_STATUS_SUCCESSFUL",
        ),
        local_path=saved,
        project_id="p1",
        flow_operation_id="o1",
    )

    fake_recorder = FakeVideoRecorder()

    async def fake_generate_video(
        *, req, out_dir, project_id=None, poll_timeout_s=None, download, on_started
    ):
        if on_started is not None:
            import inspect

            result_or_coro = on_started(
                VideoStarted(media_id="m1", project_id="p1", flow_operation_id="o1")
            )
            if inspect.isawaitable(result_or_coro):
                await result_or_coro
        return stub_result

    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.data.recorder.OperationRecorder.open", return_value=fake_recorder),
        patch(
            "gflow_cli.cli_video.cloud_info_from_path",
            return_value=cloud_info,
        ) as cloud_info_mock,
        patch(
            "gflow_cli.api.client.FlowApiClient.__aenter__",
            new_callable=AsyncMock,
        ) as mock_enter,
        patch("gflow_cli.api.client.FlowApiClient.__aexit__", new_callable=AsyncMock),
    ):
        from gflow_cli.api.client import FlowApiClient

        fake_client = MagicMock(spec=FlowApiClient)
        fake_client.generate_video = fake_generate_video
        mock_enter.return_value = fake_client

        result = runner.invoke(video, ["t2v", "x"])

    assert result.exit_code == 0, result.output
    completed_kwargs = fake_recorder.completed[0]
    assert completed_kwargs["cloud_storage_info"] == cloud_info
    cloud_info_mock.assert_called_once_with(saved)


# ---------------------------------------------------------------------------
# r2v reference-cap CLI guard (mirrors the i2i ref-cap tests).
# ---------------------------------------------------------------------------


def test_r2v_rejects_over_cap_for_veo_fast(tmp_path: Path) -> None:
    """4 --ref against veo-fast (cap 3) -> exit 2 + UsageError message."""
    runner = CliRunner()
    refs: list[Path] = []
    for i in range(4):
        p = tmp_path / f"r{i}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        refs.append(p)
    args = ["r2v", "a prompt", "--model", "veo-fast"]
    for r in refs:
        args.extend(["--ref", str(r)])
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
    ):
        result = runner.invoke(video, args)
    assert result.exit_code == 2, result.output
    assert "at most 3 reference image" in result.output
    assert "got 4" in result.output


def test_r2v_rejects_quality_model(tmp_path: Path) -> None:
    """veo-quality does not support R2V (cap 0) -> exit 2 even with 1 --ref."""
    runner = CliRunner()
    ref = tmp_path / "r.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n")
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
    ):
        result = runner.invoke(
            video, ["r2v", "a prompt", "--model", "veo-quality", "--ref", str(ref)]
        )
    assert result.exit_code == 2, result.output
    assert "does not support R2V" in result.output


def test_r2v_accepts_seven_refs_for_omni_flash(tmp_path: Path) -> None:
    """omni-flash accepts up to 7 refs; the cap guard must pass them through."""
    runner = CliRunner()
    refs: list[Path] = []
    for i in range(7):
        p = tmp_path / f"r{i}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        refs.append(p)
    args = ["r2v", "a prompt", "--model", "omni-flash"]
    for r in refs:
        args.extend(["--ref", str(r)])
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._run_r2v", new_callable=AsyncMock) as mock_run,
    ):
        mock_run.return_value = None
        result = runner.invoke(video, args)
    assert result.exit_code == 0, result.output
    mock_run.assert_awaited_once()


# ---------------------------------------------------------------------------
# i2v model/mode compatibility (issue #125).
# ---------------------------------------------------------------------------


def test_i2v_model_choice_includes_omni_flash() -> None:
    """omni-flash is a selectable --model for i2v (start-only; the 2026-08-03
    wire re-capture resolved the #125 exclusion), and 10 is a valid duration."""
    import click

    i2v_cmd = video.commands["i2v"]
    model_param = next(p for p in i2v_cmd.params if p.name == "model")
    assert isinstance(model_param.type, click.Choice)
    assert "omni-flash" in list(model_param.type.choices)
    duration_param = next(p for p in i2v_cmd.params if p.name == "duration")
    assert isinstance(duration_param.type, click.Choice)
    assert "10" in list(duration_param.type.choices)
    assert "veo-lite" in list(model_param.type.choices)

    # t2v likewise keeps omni-flash as a valid choice.
    t2v_cmd = video.commands["t2v"]
    t2v_model = next(p for p in t2v_cmd.params if p.name == "model")
    assert isinstance(t2v_model.type, click.Choice)
    assert "omni-flash" in list(t2v_model.type.choices)


def test_i2v_accepts_omni_flash_end_frame_via_cli(tmp_path: Path) -> None:
    """`--model omni-flash --end-frame` runs through the real Click surface (#626).

    The unit-level test above drives `_run_i2v` directly; this one goes through
    argument parsing so a Click `Choice` or option-level guard reintroducing the
    rejection is caught too.
    """
    from gflow_cli.api.video import VideoModel

    start = tmp_path / "start.png"
    end = tmp_path / "end.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")
    end.write_bytes(b"\x89PNG\r\n\x1a\n")
    runner = CliRunner()
    captured: dict[str, object] = {}

    async def _capture(request: object, **_k: object) -> None:
        captured["request"] = request

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._generate_and_report", new=_capture),
    ):
        result = runner.invoke(
            video,
            [
                "i2v",
                str(start),
                "rise up",
                "--model",
                "omni-flash",
                "--end-frame",
                str(end),
            ],
        )
    assert result.exit_code == 0, result.output
    request = captured["request"]
    assert request.model is VideoModel.OMNI_FLASH  # type: ignore[attr-defined]
    assert request.end_image == end  # type: ignore[attr-defined]


def test_i2v_run_defaults_to_veo_lite_when_model_omitted(tmp_path: Path) -> None:
    """_run_i2v with model=None resolves the request model to veo-lite (#125)."""
    import asyncio

    from gflow_cli.api.video import VideoModel
    from gflow_cli.cli_video import _I2VParams, _run_i2v

    captured: dict[str, object] = {}

    async def _capture(request: object, **_kwargs: object) -> None:
        captured["request"] = request

    start = tmp_path / "start.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")

    with patch("gflow_cli.cli_video._generate_and_report", new=_capture):
        asyncio.run(
            _run_i2v(
                profile_name="default",
                profile_dir=tmp_path,
                params=_I2VParams(image=str(start), prompt="rise up", aspect="9:16", model=None),
                out_dir=None,
            )
        )

    request = captured["request"]
    assert request.model is VideoModel.VEO_3_1_LITE  # type: ignore[attr-defined]


def test_i2v_run_accepts_omni_flash_start_only(tmp_path: Path) -> None:
    """Start-only i2v with omni-flash is accepted (2026-08-03 wire re-capture,
    refs #125) and the request carries OMNI_FLASH through to generation."""
    import asyncio

    from gflow_cli.api.video import VideoModel
    from gflow_cli.cli_video import _I2VParams, _run_i2v

    start = tmp_path / "start.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")
    captured: dict[str, object] = {}

    async def _capture(request: object, **_k: object) -> None:
        captured["request"] = request

    with patch("gflow_cli.cli_video._generate_and_report", new=_capture):
        asyncio.run(
            _run_i2v(
                profile_name="default",
                profile_dir=tmp_path,
                params=_I2VParams(
                    image=str(start), prompt="rise up", aspect="9:16", model="omni-flash"
                ),
                out_dir=None,
            )
        )

    request = captured["request"]
    assert request.model is VideoModel.OMNI_FLASH  # type: ignore[attr-defined]


def test_i2v_run_accepts_omni_flash_with_end_frame(tmp_path: Path) -> None:
    """omni-flash + --end-frame reaches generation with BOTH frames bound (#626).

    Flow shipped first+last for Omni 1.1 Flash, and a route-aborted capture on
    2026-09-02 proved the wire route: the submit fired
    ``video:batchAsyncGenerateVideoStartAndEndImage`` with a non-null
    ``startImage`` AND ``endImage``. The pre-spend rejection this replaces
    (exit 17) is gone for i2v; asserting both images survive onto the request
    is what would fail if the guard were reinstated.
    """
    import asyncio

    from gflow_cli.api.video import VideoModel
    from gflow_cli.cli_video import _I2VParams, _run_i2v

    start = tmp_path / "start.png"
    end = tmp_path / "end.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")
    end.write_bytes(b"\x89PNG\r\n\x1a\n")
    captured: dict[str, object] = {}

    async def _capture(request: object, **_k: object) -> None:
        captured["request"] = request

    with patch("gflow_cli.cli_video._generate_and_report", new=_capture):
        asyncio.run(
            _run_i2v(
                profile_name="default",
                profile_dir=tmp_path,
                params=_I2VParams(
                    image=str(start),
                    prompt="rise up",
                    aspect="9:16",
                    model="omni-flash",
                    end_frame=str(end),
                ),
                out_dir=None,
            )
        )

    request = captured["request"]
    assert request.model is VideoModel.OMNI_FLASH  # type: ignore[attr-defined]
    assert request.start_image == start  # type: ignore[attr-defined]
    assert request.end_image == end  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# i2v flag rename: --initial-frame / --end-frame (issue #122).
# ---------------------------------------------------------------------------


def test_i2v_positional_image_back_compat(tmp_path: Path) -> None:
    """Two-positional back-compat form (IMAGE PROMPT) routes start_image correctly."""
    import asyncio

    from gflow_cli.api.video import VideoModel
    from gflow_cli.cli_video import _I2VParams, _run_i2v

    captured: dict[str, object] = {}

    async def _capture(request: object, **_kwargs: object) -> None:
        captured["request"] = request

    start = tmp_path / "start.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")

    # CliRunner-level: two positionals, no --initial-frame
    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._generate_and_report", new=_capture),
    ):
        result = runner.invoke(video, ["i2v", str(start), "rise up"])
    assert result.exit_code == 0, result.output

    # Also verify _run_i2v internal path for completeness
    with patch("gflow_cli.cli_video._generate_and_report", new=_capture):
        asyncio.run(
            _run_i2v(
                profile_name="default",
                profile_dir=tmp_path,
                params=_I2VParams(image=str(start), prompt="rise up", aspect="9:16"),
                out_dir=None,
            )
        )
    request = captured["request"]
    assert request.model is VideoModel.VEO_3_1_LITE  # type: ignore[attr-defined]
    assert request.start_image == start  # type: ignore[attr-defined]


def test_i2v_initial_frame_flag(tmp_path: Path) -> None:
    """--initial-frame resolves as the start image (swap logic verification)."""
    captured: dict[str, object] = {}

    async def _capture(request: object, **_kwargs: object) -> None:
        captured["request"] = request

    start = tmp_path / "hero.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")

    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._generate_and_report", new=_capture),
    ):
        result = runner.invoke(
            video,
            ["i2v", "--initial-frame", str(start), "slow push-in"],
        )
    assert result.exit_code == 0, result.output
    assert captured["request"].start_image == start  # type: ignore[attr-defined]
    assert captured["request"].prompt == "slow push-in"  # type: ignore[attr-defined]


def test_i2v_initial_frame_takes_precedence_over_positional(tmp_path: Path) -> None:
    """When both --initial-frame and positional IMAGE are given, --initial-frame wins."""
    captured: dict[str, object] = {}

    async def _capture(request: object, **_kwargs: object) -> None:
        captured["request"] = request

    flag_img = tmp_path / "flag.png"
    flag_img.write_bytes(b"\x89PNG\r\n\x1a\n")
    positional_img = tmp_path / "positional.png"
    positional_img.write_bytes(b"\x89PNG\r\n\x1a\n")

    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._generate_and_report", new=_capture),
    ):
        result = runner.invoke(
            video,
            ["i2v", str(positional_img), "--initial-frame", str(flag_img), "motion prompt"],
        )
    assert result.exit_code == 0, result.output
    assert captured["request"].start_image == flag_img  # type: ignore[attr-defined]
    assert captured["request"].prompt == "motion prompt"  # type: ignore[attr-defined]


def test_i2v_project_name_flag_reaches_the_request(tmp_path: Path) -> None:
    """#287: `--project-name` is the picker project-menu display-name override
    (the menu lists projects by NAME; unnamed projects show only creation
    timestamps) — it must land on the GenerateVideoRequest."""
    captured: dict[str, object] = {}

    async def _capture(request: object, **_kwargs: object) -> None:
        captured["request"] = request

    start = tmp_path / "hero.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")

    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._generate_and_report", new=_capture),
    ):
        result = runner.invoke(
            video,
            ["i2v", str(start), "pan", "--project-name", "Chalkboard Spike"],
        )
    assert result.exit_code == 0, result.output
    assert captured["request"].project_name == "Chalkboard Spike"  # type: ignore[attr-defined]


def test_i2v_project_name_env_var(tmp_path: Path) -> None:
    """GFLOW_CLI_PROJECT_NAME is the env-var form of `--project-name`."""
    captured: dict[str, object] = {}

    async def _capture(request: object, **_kwargs: object) -> None:
        captured["request"] = request

    start = tmp_path / "hero.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")

    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._generate_and_report", new=_capture),
    ):
        result = runner.invoke(
            video,
            ["i2v", str(start), "pan"],
            env={"GFLOW_CLI_PROJECT_NAME": "Env Given Name"},
        )
    assert result.exit_code == 0, result.output
    assert captured["request"].project_name == "Env Given Name"  # type: ignore[attr-defined]


def test_media_picker_metadata_ignores_unknown_and_unnamed_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy rows without a Flow name cannot become browser search terms."""
    from gflow_cli import cli_video

    class _FakeStore:
        def __enter__(self) -> _FakeStore:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

    class _FakeRepo:
        def __init__(self, _store: object) -> None: ...

        def get_asset_by_flow_media_id(self, _profile: str, media_id: str) -> object:
            if media_id == "uuid-unnamed":
                return SimpleNamespace(
                    kind=AssetKind.IMAGE,
                    metadata_json={},
                    local_files=[],
                )
            return None

    monkeypatch.setattr(cli_video.DataStore, "open", staticmethod(lambda _p: _FakeStore()))
    monkeypatch.setattr(cli_video, "DataRepository", _FakeRepo)

    assert cli_video._media_picker_metadata(["uuid-unnamed", "uuid-unknown", None], "ffroliva") == (
        {},
        {},
    )


def test_media_picker_metadata_swallows_catalog_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli import cli_video

    def _boom(_path: object) -> object:
        raise OSError("catalog unavailable")

    monkeypatch.setattr(cli_video.DataStore, "open", staticmethod(_boom))

    assert cli_video._media_picker_metadata(["uuid-1"], "ffroliva") == ({}, {})


def test_media_picker_metadata_resolves_names_per_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each frame UUID carries its own catalog name into picker search."""
    from gflow_cli import cli_video

    class _FakeStore:
        def __enter__(self) -> _FakeStore:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

    class _FakeRepo:
        def __init__(self, _store: object) -> None: ...

        def get_asset_by_flow_media_id(self, profile: str, media_id: str) -> object:
            assert profile == "ffroliva"
            names = {
                "uuid-start": "Brass key on marble surface",
                "uuid-end": "Brass key on wooden bench",
            }
            name = names.get(media_id)
            return (
                None
                if name is None
                else SimpleNamespace(
                    kind=AssetKind.IMAGE,
                    metadata_json={"display_name": name},
                    local_files=[],
                )
            )

    monkeypatch.setattr(cli_video.DataStore, "open", staticmethod(lambda _p: _FakeStore()))
    monkeypatch.setattr(cli_video, "DataRepository", _FakeRepo)

    names, local_paths = cli_video._media_picker_metadata(
        ["uuid-start", "uuid-end", "uuid-unknown", None], "ffroliva"
    )

    assert names == {
        "uuid-start": "Brass key on marble surface",
        "uuid-end": "Brass key on wooden bench",
    }
    assert local_paths == {}


def test_media_picker_metadata_filters_non_images_and_stale_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli import cli_video

    valid = tmp_path / "frame.png"
    valid.write_bytes(b"\x89PNG\r\n\x1a\n")

    class _FakeStore:
        def __enter__(self) -> _FakeStore:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

    def _asset(kind: AssetKind, path: Path) -> SimpleNamespace:
        return SimpleNamespace(
            kind=kind,
            metadata_json={},
            local_files=[
                SimpleNamespace(
                    path=path,
                    storage_provider=None,
                    bytes=path.stat().st_size if path.is_file() else None,
                    sha256=(
                        hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                    ),
                )
            ],
        )

    class _FakeRepo:
        def __init__(self, _store: object) -> None: ...

        def get_asset_by_flow_media_id(self, _profile: str, media_id: str) -> object:
            assets = {
                "uuid-image": _asset(AssetKind.IMAGE, valid),
                "uuid-mutated": SimpleNamespace(
                    kind=AssetKind.IMAGE,
                    metadata_json={},
                    local_files=[
                        SimpleNamespace(
                            path=valid,
                            storage_provider=None,
                            bytes=valid.stat().st_size,
                            sha256="0" * 64,
                        )
                    ],
                ),
                "uuid-stale": _asset(AssetKind.IMAGE, tmp_path / "missing.png"),
                "uuid-video": _asset(AssetKind.VIDEO, tmp_path / "clip.mp4"),
            }
            return assets.get(media_id)

    monkeypatch.setattr(cli_video.DataStore, "open", staticmethod(lambda _p: _FakeStore()))
    monkeypatch.setattr(cli_video, "DataRepository", _FakeRepo)

    assert cli_video._media_picker_metadata(
        ["uuid-image", "uuid-mutated", "uuid-stale", "uuid-video", None], "default"
    ) == ({}, {"uuid-image": (valid, hashlib.sha256(valid.read_bytes()).hexdigest())})


def test_i2v_uuid_frame_gets_catalog_display_name(tmp_path: Path) -> None:
    """A media UUID carries its catalog name into the transport request."""
    captured: dict[str, object] = {}

    async def _capture(request: object, **_kwargs: object) -> None:
        captured["request"] = request

    uuid_ref = "d6f1927a-3eae-4626-bc90-9a6ea7637bab"
    recorded = tmp_path / "recorded-frame.png"
    content = b"\x89PNG\r\n\x1a\n"
    recorded.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._generate_and_report", new=_capture),
        patch(
            "gflow_cli.cli_video._media_picker_metadata",
            return_value=(
                {uuid_ref: "Brass key on marble surface"},
                {uuid_ref: (recorded, digest)},
            ),
        ) as names,
    ):
        result = runner.invoke(
            video,
            ["i2v", "--initial-frame", uuid_ref, "pan", "--project", "f6caf027-aaaa"],
        )
    assert result.exit_code == 0, result.output
    assert (  # type: ignore[attr-defined]
        captured["request"].start_image_ref_display_name == "Brass key on marble surface"
    )
    assert captured["request"].start_image_ref_local_path == recorded  # type: ignore[attr-defined]
    assert captured["request"].start_image_ref_local_sha256 == digest  # type: ignore[attr-defined]
    assert captured["request"].start_image_ref_id == uuid_ref  # type: ignore[attr-defined]
    assert uuid_ref in names.call_args.args[0]


def test_i2v_unnamed_uuid_preserves_identity_and_recorded_fallback(tmp_path: Path) -> None:
    """An unnamed UUID remains identity while its exact bytes remain fallback."""
    captured: dict[str, object] = {}

    async def _capture(request: object, **_kwargs: object) -> None:
        captured["request"] = request

    uuid_ref = "d6f1927a-3eae-4626-bc90-9a6ea7637bab"
    recorded = tmp_path / "recorded-frame.png"
    content = b"\x89PNG\r\n\x1a\n"
    recorded.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video._generate_and_report", new=_capture),
        patch(
            "gflow_cli.cli_video._media_picker_metadata",
            return_value=({}, {uuid_ref: (recorded, digest)}),
        ),
    ):
        result = runner.invoke(
            video,
            ["i2v", "--initial-frame", uuid_ref, "pan", "--project", "f6caf027-aaaa"],
        )

    assert result.exit_code == 0, result.output
    request = captured["request"]
    assert request.start_image is None  # type: ignore[attr-defined]
    assert request.start_image_ref_id == uuid_ref  # type: ignore[attr-defined]
    assert request.start_image_ref_local_path == recorded  # type: ignore[attr-defined]
    assert request.start_image_ref_local_sha256 == digest  # type: ignore[attr-defined]


def test_i2v_no_image_raises_usage_error(tmp_path: Path) -> None:
    """Omitting both the positional IMAGE and --initial-frame is a usage error."""
    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
    ):
        # Single positional with no --initial-frame → prompt slot never filled → error
        result = runner.invoke(video, ["i2v", "some motion prompt"])
    assert result.exit_code != 0, result.output


def test_i2v_positional_image_nonexistent_raises_bad_parameter(tmp_path: Path) -> None:
    """Positional IMAGE that isn't a real file raises BadParameter."""
    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
    ):
        result = runner.invoke(video, ["i2v", "not_a_file.png", "motion prompt"])
    assert result.exit_code != 0, result.output
    assert "not_a_file.png" in result.output


def test_i2v_end_frame_flag(tmp_path: Path) -> None:
    """--end-frame sets end_image on the GenerateVideoRequest."""
    import asyncio

    from gflow_cli.cli_video import _I2VParams, _run_i2v

    captured: dict[str, object] = {}

    async def _capture(request: object, **_kwargs: object) -> None:
        captured["request"] = request

    start = tmp_path / "start.png"
    end = tmp_path / "end.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")
    end.write_bytes(b"\x89PNG\r\n\x1a\n")

    with patch("gflow_cli.cli_video._generate_and_report", new=_capture):
        asyncio.run(
            _run_i2v(
                profile_name="default",
                profile_dir=tmp_path,
                params=_I2VParams(
                    image=str(start), prompt="pan left", aspect="9:16", end_frame=str(end)
                ),
                out_dir=None,
            )
        )

    request = captured["request"]
    assert request.end_image == end  # type: ignore[attr-defined]


def test_i2v_end_image_deprecated_emits_warning(tmp_path: Path) -> None:
    """--end-image emits DeprecationWarning and is treated as --end-frame.

    warnings.warn fires synchronously in the Click handler body (before the
    run_with_handlers lambda), so catch_warnings captures it reliably.
    """
    import warnings

    start = tmp_path / "start.png"
    end = tmp_path / "end.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")
    end.write_bytes(b"\x89PNG\r\n\x1a\n")

    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video.run_with_handlers"),
        warnings.catch_warnings(record=True) as w,
    ):
        warnings.simplefilter("always")
        result = runner.invoke(
            video,
            ["i2v", str(start), "pan left", "--end-image", str(end)],
        )

    assert result.exit_code == 0, result.output
    deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
    assert len(deprecation_warnings) >= 1, "expected at least one DeprecationWarning"
    assert any("--end-image" in str(x.message) for x in deprecation_warnings)
    assert any("--end-frame" in str(x.message) for x in deprecation_warnings)


def test_i2v_help_uses_initial_end_frame_wording() -> None:
    """Help text uses 'initial frame' / 'end frame'; must not contain 'start image'."""
    runner = CliRunner()
    result = runner.invoke(video, ["i2v", "--help"])
    assert result.exit_code == 0
    help_text = result.output.lower()
    assert "initial frame" in help_text
    assert "end frame" in help_text
    assert "start image" not in help_text
    assert "--initial-frame" in result.output
    assert "--end-frame" in result.output
    assert "--end-image" not in result.output  # deprecated flag is hidden


def test_t2v_help_shows_tool_option() -> None:
    from click.testing import CliRunner

    from gflow_cli.cli import main

    result = CliRunner().invoke(main, ["video", "t2v", "--help"])
    assert "--tool" in result.output
    assert "--expand" not in result.output


# ---------------------------------------------------------------------------
# --tool broaden (PR2 §8): i2v / r2v / chain.
# ---------------------------------------------------------------------------


def _tool_sentinel() -> object:
    from gflow_cli.tools.invocation import AppliedTool

    return AppliedTool(
        name="creative-director", version="1", model="gemini-2.5-flash", config_hash="z" * 64
    )


def test_i2v_run_threads_tool_provenance(tmp_path: Path) -> None:
    import asyncio

    from gflow_cli.cli_video import _I2VParams, _run_i2v

    captured: dict[str, object] = {}

    async def _capture(request: object, **_kwargs: object) -> None:
        captured["request"] = request

    start = tmp_path / "start.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")
    tool = _tool_sentinel()

    with patch("gflow_cli.cli_video._generate_and_report", new=_capture):
        asyncio.run(
            _run_i2v(
                profile_name="default",
                profile_dir=tmp_path,
                params=_I2VParams(
                    image=str(start),
                    prompt="EXPANDED",
                    aspect="9:16",
                    original_prompt="cat",
                    tool=tool,  # type: ignore[arg-type]
                ),
                out_dir=None,
            )
        )

    request = captured["request"]
    assert request.original_prompt == "cat"  # type: ignore[attr-defined]
    assert request.tool is tool  # type: ignore[attr-defined]


def test_r2v_run_threads_tool_provenance(tmp_path: Path) -> None:
    import asyncio

    from gflow_cli.cli_video import _run_r2v

    captured: dict[str, object] = {}

    async def _capture(request: object, **_kwargs: object) -> None:
        captured["request"] = request

    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n")
    tool = _tool_sentinel()

    with patch("gflow_cli.cli_video._generate_and_report", new=_capture):
        asyncio.run(
            _run_r2v(
                profile_name="default",
                profile_dir=tmp_path,
                prompt="EXPANDED",
                refs=(str(ref),),
                aspect="9:16",
                out_dir=None,
                model="omni-flash",
                original_prompt="cat",
                tool=tool,  # type: ignore[arg-type]
            )
        )

    request = captured["request"]
    assert request.original_prompt == "cat"  # type: ignore[attr-defined]
    assert request.tool is tool  # type: ignore[attr-defined]


def test_apply_tools_to_chain_links_rewrites_each_link(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli import cli_video
    from gflow_cli.chain import ChainLinkSpec

    def fake_apply(text, tool_specs, *, category, quiet):  # noqa: ANN001, ANN202
        assert category == "video"
        return f"X:{text}", text, "TOOLOBJ"

    monkeypatch.setattr(cli_video, "apply_tool_option", fake_apply)
    links = [ChainLinkSpec(prompt="a"), ChainLinkSpec(prompt="b")]
    out = cli_video._apply_tools_to_chain_links(links, ("creative-director",))
    assert [link.prompt for link in out] == ["X:a", "X:b"]
    assert [link.original_prompt for link in out] == ["a", "b"]
    assert all(link.tool == "TOOLOBJ" for link in out)


def test_tool_option_present_on_video_generation_commands() -> None:
    runner = CliRunner()
    for cmd in ("t2v", "i2v", "r2v", "chain"):
        result = runner.invoke(video, [cmd, "--help"])
        assert "--tool" in result.output, f"{cmd} --help missing --tool"


# ---------------------------------------------------------------------------
# --project flag (issue #233): parity with `image t2i`/`i2i`.
# ---------------------------------------------------------------------------


class TestVideoProjectFlag:
    """`--project <id>` threads project_id through to client.generate_video."""

    def test_t2v_threads_project_id_to_generate_and_report(self, tmp_path: Path) -> None:
        captured: dict[str, object] = {}

        async def _capture(request: object, **kwargs: object) -> None:
            captured["request"] = request
            captured["kwargs"] = kwargs

        runner = CliRunner()
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._generate_and_report", new=_capture),
        ):
            result = runner.invoke(video, ["t2v", "a sunset", "--project", "PROJ123"])

        assert result.exit_code == 0, result.output
        assert captured["kwargs"]["project_id"] == "PROJ123"  # type: ignore[index]

    def test_i2v_threads_project_id_to_generate_and_report(self, tmp_path: Path) -> None:
        captured: dict[str, object] = {}

        async def _capture(request: object, **kwargs: object) -> None:
            captured["request"] = request
            captured["kwargs"] = kwargs

        start = tmp_path / "hero.png"
        start.write_bytes(b"\x89PNG\r\n\x1a\n")

        runner = CliRunner()
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._generate_and_report", new=_capture),
        ):
            result = runner.invoke(
                video,
                ["i2v", str(start), "slow push-in", "--project", "PROJ123"],
            )

        assert result.exit_code == 0, result.output
        assert captured["kwargs"]["project_id"] == "PROJ123"  # type: ignore[index]

    def test_r2v_threads_project_id_to_generate_and_report(self, tmp_path: Path) -> None:
        captured: dict[str, object] = {}

        async def _capture(request: object, **kwargs: object) -> None:
            captured["request"] = request
            captured["kwargs"] = kwargs

        ref = tmp_path / "armor.png"
        ref.write_bytes(b"\x89PNG\r\n\x1a\n")

        runner = CliRunner()
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._generate_and_report", new=_capture),
        ):
            result = runner.invoke(
                video,
                ["r2v", "knight walks forward", "--ref", str(ref), "--project", "PROJ123"],
            )

        assert result.exit_code == 0, result.output
        assert captured["kwargs"]["project_id"] == "PROJ123"  # type: ignore[index]

    def test_t2v_without_project_passes_none(self, tmp_path: Path) -> None:
        """Omitting --project keeps the historical scratch-project behavior."""
        captured: dict[str, object] = {}

        async def _capture(request: object, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

        runner = CliRunner()
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._generate_and_report", new=_capture),
        ):
            result = runner.invoke(video, ["t2v", "a sunset"])

        assert result.exit_code == 0, result.output
        assert captured["kwargs"]["project_id"] is None  # type: ignore[index]

    def test_t2v_forwards_project_id_to_client_generate_video(self, tmp_path: Path) -> None:
        """End-to-end: --project reaches FlowApiClient.generate_video(project_id=...)."""
        from gflow_cli.api.video import VideoResult, VideoStarted, VideoStatus

        saved = tmp_path / "test-uuid.mp4"
        saved.touch()
        stub_result = VideoResult(
            status=VideoStatus(media_id="m1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
            local_path=saved,
            project_id="PROJ123",
            flow_operation_id="o1",
        )

        fake_recorder = FakeVideoRecorder()
        generate_video_mock = AsyncMock(return_value=stub_result)

        async def fake_generate_video(**kwargs: object) -> object:
            if kwargs.get("on_started") is not None:
                import inspect

                started = VideoStarted(media_id="m1", project_id="PROJ123", flow_operation_id="o1")
                res = kwargs["on_started"](started)  # type: ignore[operator]
                if inspect.isawaitable(res):
                    await res
            return await generate_video_mock(**kwargs)

        runner = CliRunner()
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.data.recorder.OperationRecorder.open", return_value=fake_recorder),
            patch(
                "gflow_cli.api.client.FlowApiClient.__aenter__",
                new_callable=AsyncMock,
            ) as mock_enter,
            patch("gflow_cli.api.client.FlowApiClient.__aexit__", new_callable=AsyncMock),
        ):
            from gflow_cli.api.client import FlowApiClient

            fake_client = MagicMock(spec=FlowApiClient)
            fake_client.generate_video = fake_generate_video
            mock_enter.return_value = fake_client

            result = runner.invoke(video, ["t2v", "a sunset", "--project", "PROJ123"])

        assert result.exit_code == 0, result.output
        call = generate_video_mock.await_args
        assert call is not None
        assert call.kwargs["project_id"] == "PROJ123"

    def test_t2v_rejects_bad_project_id(self) -> None:
        runner = CliRunner()
        result = runner.invoke(video, ["t2v", "a cat", "--project", "bad/id"])
        assert result.exit_code == 2, result.output
        assert "project id" in result.output.lower()

    def test_project_help_text_present_on_video_generation_commands(self) -> None:
        runner = CliRunner()
        for cmd in ("t2v", "i2v", "r2v"):
            result = runner.invoke(video, [cmd, "--help"])
            assert "--project" in result.output, f"{cmd} --help missing --project"


# ---------------------------------------------------------------------------
# #287: i2v accepts an in-project asset UUID for --initial-frame/--end-frame.
# ---------------------------------------------------------------------------

_ASSET_UUID = "d6f1927a-3eae-4626-bc90-9a6ea7637bab"


class TestI2VAssetRef:
    def _invoke(self, tmp_path: Path, args: list[str]) -> tuple[object, dict[str, object]]:
        captured: dict[str, object] = {}

        async def _capture(request: object, **kwargs: object) -> None:
            captured["request"] = request
            captured["kwargs"] = kwargs

        runner = CliRunner()
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._generate_and_report", new=_capture),
        ):
            result = runner.invoke(video, args)
        return result, captured

    def test_uuid_initial_frame_becomes_ref_id(self, tmp_path: Path) -> None:
        result, captured = self._invoke(
            tmp_path, ["i2v", "--initial-frame", _ASSET_UUID, "slow push-in"]
        )
        assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
        request = captured["request"]
        assert request.start_image is None  # type: ignore[attr-defined]
        assert request.start_image_ref_id == _ASSET_UUID  # type: ignore[attr-defined]

    def test_uuid_positional_image_becomes_ref_id(self, tmp_path: Path) -> None:
        result, captured = self._invoke(tmp_path, ["i2v", _ASSET_UUID, "slow push-in"])
        assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
        assert captured["request"].start_image_ref_id == _ASSET_UUID  # type: ignore[attr-defined]

    def test_local_start_with_uuid_end_frame(self, tmp_path: Path) -> None:
        start = tmp_path / "hero.png"
        start.write_bytes(b"\x89PNG\r\n\x1a\n")
        result, captured = self._invoke(
            tmp_path, ["i2v", str(start), "pan left", "--end-frame", _ASSET_UUID]
        )
        assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
        request = captured["request"]
        assert request.start_image is not None  # type: ignore[attr-defined]
        assert request.end_image is None  # type: ignore[attr-defined]
        assert request.end_image_ref_id == _ASSET_UUID  # type: ignore[attr-defined]

    def test_nonexistent_path_still_usage_error(self, tmp_path: Path) -> None:
        result, _ = self._invoke(tmp_path, ["i2v", "--initial-frame", "no/such/file.png", "prompt"])
        assert result.exit_code == 2  # type: ignore[attr-defined]

    def test_nonexistent_end_frame_path_usage_error(self, tmp_path: Path) -> None:
        start = tmp_path / "hero.png"
        start.write_bytes(b"\x89PNG\r\n\x1a\n")
        result, _ = self._invoke(
            tmp_path, ["i2v", str(start), "prompt", "--end-frame", "no/such/file.png"]
        )
        assert result.exit_code == 2  # type: ignore[attr-defined]

    def test_deprecated_end_image_accepts_uuid(self, tmp_path: Path) -> None:
        start = tmp_path / "hero.png"
        start.write_bytes(b"\x89PNG\r\n\x1a\n")
        with pytest.warns(DeprecationWarning):
            result, captured = self._invoke(
                tmp_path, ["i2v", str(start), "prompt", "--end-image", _ASSET_UUID]
            )
        assert result.exit_code == 0, result.output  # type: ignore[attr-defined]
        assert captured["request"].end_image_ref_id == _ASSET_UUID  # type: ignore[attr-defined]

    def test_deprecated_end_image_bad_value_names_end_image(self, tmp_path: Path) -> None:
        """#283 follow-up: the error must name the flag the user typed."""
        start = tmp_path / "hero.png"
        start.write_bytes(b"\x89PNG\r\n\x1a\n")
        with pytest.warns(DeprecationWarning):
            result, _ = self._invoke(
                tmp_path, ["i2v", str(start), "prompt", "--end-image", "no/such/file.png"]
            )
        assert result.exit_code == 2  # type: ignore[attr-defined]
        assert "--end-image" in result.output  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# `video batch` removal (Task 1, production-readiness-hardening): the stub
# never worked (always printed "not yet available" and exited 1). It must be
# gone from the Click group entirely — not just erroring a different way.
# ---------------------------------------------------------------------------


def test_video_help_does_not_advertise_batch() -> None:
    """`batch` must not be registered on the video Click group, and must not
    appear in `gflow video --help`'s output (registration-level check, not
    just a substring scan, so this can't false-positive on incidental prose)."""
    assert "batch" not in video.commands
    result = CliRunner().invoke(video, ["--help"])
    assert result.exit_code == 0
    assert "batch" not in result.output


def test_video_batch_is_rejected_before_profile_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """`video batch` must be rejected by Click's own command routing (exit 2,
    "No such command") without ever reaching profile resolution.

    A tripwire on `_resolve_profile` (rather than merely asserting on the exit
    code) rules out the coincidental case where a still-registered stub fails
    for an unrelated reason (e.g. no profile configured) that happens to also
    exit 2.
    """

    def _must_not_run(*_a: object, **_k: object) -> str:
        raise AssertionError("_resolve_profile must not run for a removed command")

    monkeypatch.setattr("gflow_cli.cli_video._resolve_profile", _must_not_run)
    result = CliRunner().invoke(video, ["batch", "manifest.tsv"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_image_batch_remains_a_leaf_command() -> None:
    """`gflow image batch` (the working manifest path, image_batch.py) must be
    completely unaffected by the video-batch stub removal."""
    from gflow_cli.cli_image import image

    result = CliRunner().invoke(image, ["batch", "--help"])
    assert result.exit_code == 0


class TestUiModeOption:
    """#299 PR-A: --ui-mode on the video commands. classic/auto thread through;
    agentic is rejected at the CLI edge (exit 2) — no agentic video driver
    exists, so exit 28's "retry may land it" remediation would mislead."""

    def test_t2v_ui_mode_classic_threads(self, tmp_path: Path) -> None:
        from gflow_cli.config import UiMode

        runner = CliRunner()
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._run_t2v", new_callable=AsyncMock) as mock_run,
        ):
            result = runner.invoke(video, ["t2v", "prompt", "--ui-mode", "classic"])
        assert result.exit_code == 0
        assert mock_run.call_args.kwargs["ui_mode"] is UiMode.CLASSIC

    def test_t2v_ui_mode_auto_threads(self, tmp_path: Path) -> None:
        from gflow_cli.config import UiMode

        runner = CliRunner()
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._run_t2v", new_callable=AsyncMock) as mock_run,
        ):
            result = runner.invoke(video, ["t2v", "prompt", "--ui-mode", "auto"])
        assert result.exit_code == 0
        assert mock_run.call_args.kwargs["ui_mode"] is UiMode.AUTO

    def test_t2v_ui_mode_agentic_rejected_pre_profile(self) -> None:
        runner = CliRunner()
        with (
            patch(
                "gflow_cli.cli_video._resolve_profile",
                side_effect=AssertionError("must reject before any profile work"),
            ),
            patch("gflow_cli.cli_video._run_t2v", new_callable=AsyncMock) as mock_run,
        ):
            result = runner.invoke(video, ["t2v", "prompt", "--ui-mode", "agentic"])
        assert result.exit_code == 2
        assert "agentic" in result.output
        mock_run.assert_not_awaited()

    def test_i2v_ui_mode_classic_threads(self, tmp_path: Path) -> None:
        from gflow_cli.config import UiMode

        img = tmp_path / "a.png"
        img.touch()
        runner = CliRunner()
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._run_i2v", new_callable=AsyncMock) as mock_run,
        ):
            result = runner.invoke(video, ["i2v", str(img), "motion", "--ui-mode", "classic"])
        assert result.exit_code == 0
        assert mock_run.call_args.kwargs["params"].ui_mode is UiMode.CLASSIC

    def test_i2v_ui_mode_agentic_rejected(self, tmp_path: Path) -> None:
        img = tmp_path / "a.png"
        img.touch()
        runner = CliRunner()
        with patch("gflow_cli.cli_video._run_i2v", new_callable=AsyncMock) as mock_run:
            result = runner.invoke(video, ["i2v", str(img), "motion", "--ui-mode", "agentic"])
        assert result.exit_code == 2
        assert "agentic" in result.output
        mock_run.assert_not_awaited()


class TestDurationCapabilityGuard:
    """Duration caps fail at the CLI edge before browser work."""

    @pytest.mark.parametrize("command", ["t2v", "i2v", "r2v"])
    @pytest.mark.parametrize("model", ["veo-lite", "veo-fast", "veo-quality", "veo-lite-lp"])
    def test_duration_10_on_a_veo_model_exits_2_with_the_reason(
        self, command: str, model: str, tmp_path: Path
    ) -> None:
        ref = tmp_path / "ref.png"
        ref.write_bytes(b"\x89PNG\r\n\x1a\n")
        extra = {
            "t2v": [],
            "i2v": ["--initial-frame", str(ref)],
            "r2v": ["--ref", str(ref)],
        }[command]
        result = CliRunner().invoke(
            video, [command, "a prompt", "--model", model, "--duration", "10", *extra]
        )
        assert result.exit_code == 2, result.output
        assert "caps at 8s" in result.output
        assert "duration 10 is only available for omni_flash" in result.output
        assert "Unexpected error" not in result.output
        assert model in result.output

    def test_duration_10_on_i2v_without_model_exits_2(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.png"
        ref.write_bytes(b"\x89PNG\r\n\x1a\n")
        result = CliRunner().invoke(
            video, ["i2v", "a prompt", "--initial-frame", str(ref), "--duration", "10"]
        )
        assert result.exit_code == 2, result.output
        assert "caps at 8s" in result.output
        assert "duration 10 is only available for omni_flash" in result.output
        assert "Unexpected error" not in result.output

    @pytest.mark.parametrize("duration", [4, 6, 8])
    def test_veo_duration_is_accepted_by_cli_preflight(self, duration: int, tmp_path: Path) -> None:
        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
            patch("gflow_cli.cli_video._run_t2v", new_callable=AsyncMock) as mock_run,
        ):
            result = CliRunner().invoke(
                video,
                ["t2v", "a prompt", "--model", "veo-lite", "--duration", str(duration)],
            )
        assert result.exit_code == 0, result.output
        assert mock_run.call_args.kwargs["duration"] == duration
        assert "Unexpected error" not in result.output

    def test_duration_on_t2v_without_model_is_left_alone(self, tmp_path: Path) -> None:
        """Negative control: t2v with no ``--model`` must NOT be rejected here.

        t2v inherits Flow's sticky UI default, which gflow cannot know, so the
        effective model is genuinely unresolvable — guarding it would reject a
        run that may be perfectly valid. Only i2v has a gflow-side default to
        resolve. Without this, "resolve the default" could quietly grow into
        "assume veo-lite everywhere".
        """
        result = CliRunner().invoke(video, ["t2v", "a prompt", "--duration", "8"])
        assert "caps at" not in result.output

    def test_duration_on_omni_flash_passes_the_guard(self, tmp_path: Path) -> None:
        """Negative control: the guard must not over-reject omni-flash, whose
        10s duration is valid. It should get past the guard and fail later
        on the missing frame instead."""
        result = CliRunner().invoke(
            video,
            [
                "i2v",
                "a prompt",
                "--initial-frame",
                str(tmp_path / "missing.png"),
                "--model",
                "omni-flash",
                "--duration",
                "8",
            ],
        )
        assert "caps at" not in result.output
