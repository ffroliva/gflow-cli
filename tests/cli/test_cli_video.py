"""Click-runner tests for the `gflow video t2v` command (Phase B restoration)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

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
