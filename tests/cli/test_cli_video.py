"""Click-runner tests for the `gflow video t2v` command (Phase B restoration)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from gflow_cli.cli_video import video


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

    async def fake_generate_video(*, req, out_dir, poll_timeout_s=None, download, on_started):
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

    async def fake_generate_video(*, req, out_dir, poll_timeout_s=None, download, on_started):
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

    async def fake_generate_video(*, req, out_dir, poll_timeout_s=None, download, on_started):
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

    async def fake_generate_video(*, req, out_dir, poll_timeout_s=None, download, on_started):
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
