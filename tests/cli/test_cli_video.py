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


# ---------------------------------------------------------------------------
# i2v model/mode compatibility (issue #125).
# ---------------------------------------------------------------------------


def test_i2v_model_choice_excludes_omni_flash() -> None:
    """omni-flash must not be a selectable --model choice for i2v (issue #125).

    Introspect the Click Choice rather than scanning help prose — the help
    text deliberately MENTIONS omni-flash to explain why it's rejected.
    """
    import click

    i2v_cmd = video.commands["i2v"]
    model_param = next(p for p in i2v_cmd.params if p.name == "model")
    assert isinstance(model_param.type, click.Choice)
    choices = list(model_param.type.choices)
    assert "omni-flash" not in choices
    assert "veo-lite" in choices

    # Contrast: t2v keeps omni-flash as a valid choice.
    t2v_cmd = video.commands["t2v"]
    t2v_model = next(p for p in t2v_cmd.params if p.name == "model")
    assert isinstance(t2v_model.type, click.Choice)
    assert "omni-flash" in list(t2v_model.type.choices)


def test_i2v_rejects_omni_flash_via_click_choice(tmp_path: Path) -> None:
    """Passing --model omni-flash to i2v is a Click usage error (exit 2)."""
    start = tmp_path / "start.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")
    runner = CliRunner()
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
    ):
        result = runner.invoke(video, ["i2v", str(start), "rise up", "--model", "omni-flash"])
    assert result.exit_code == 2, result.output
    assert "omni-flash" in result.output  # Click lists it among invalid choices


def test_i2v_run_defaults_to_veo_lite_when_model_omitted(tmp_path: Path) -> None:
    """_run_i2v with model=None resolves the request model to veo-lite (#125)."""
    import asyncio

    from gflow_cli.api.video import VideoModel
    from gflow_cli.cli_video import _run_i2v

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
                image=str(start),
                prompt="rise up",
                aspect="9:16",
                out_dir=None,
                model=None,
            )
        )

    request = captured["request"]
    assert request.model is VideoModel.VEO_3_1_LITE  # type: ignore[attr-defined]


def test_i2v_run_rejects_omni_flash_from_stale_config(tmp_path: Path) -> None:
    """A direct/stale-config call that smuggles omni-flash past the Click Choice
    must still be rejected with ModelModeIncompatibilityError (exit code 17)."""
    import asyncio

    from gflow_cli.cli_video import _run_i2v
    from gflow_cli.errors import EXIT_CODE_MAP, ModelModeIncompatibilityError

    start = tmp_path / "start.png"
    start.write_bytes(b"\x89PNG\r\n\x1a\n")

    async def _should_not_run(*_a: object, **_k: object) -> None:
        raise AssertionError("_generate_and_report must not be reached")

    with patch("gflow_cli.cli_video._generate_and_report", new=_should_not_run):
        try:
            asyncio.run(
                _run_i2v(
                    profile_name="default",
                    profile_dir=tmp_path,
                    image=str(start),
                    prompt="rise up",
                    aspect="9:16",
                    out_dir=None,
                    model="omni-flash",
                )
            )
        except ModelModeIncompatibilityError as e:
            assert EXIT_CODE_MAP[ModelModeIncompatibilityError] == 17
            assert "#125" in (e.detail or "")
        else:
            raise AssertionError("expected ModelModeIncompatibilityError")


# ---------------------------------------------------------------------------
# i2v flag rename: --initial-frame / --end-frame (issue #122).
# ---------------------------------------------------------------------------


def test_i2v_positional_image_back_compat(tmp_path: Path) -> None:
    """Two-positional back-compat form (IMAGE PROMPT) routes start_image correctly."""
    import asyncio

    from gflow_cli.api.video import VideoModel
    from gflow_cli.cli_video import _run_i2v

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
                image=str(start),
                prompt="rise up",
                aspect="9:16",
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

    from gflow_cli.cli_video import _run_i2v

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
                image=str(start),
                prompt="pan left",
                aspect="9:16",
                out_dir=None,
                end_frame=str(end),
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
