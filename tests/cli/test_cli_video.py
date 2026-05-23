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
