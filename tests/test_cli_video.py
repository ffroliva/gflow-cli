"""Click-runner tests for `gflow video` subcommands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from flow_cli.api.dto import VideoOperation, VideoStatus


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_mock_client():
    """A fully-stubbed FlowApiClient that simulates one successful gen + download."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.create_project = AsyncMock(return_value=MagicMock(project_id="proj-1", title="t"))
    client.generate_video = AsyncMock(
        return_value=VideoOperation(
            media_name="media-1",
            project_id="proj-1",
            operation_name="op-1",
            workflow_id="wf-1",
        )
    )
    client.get_video_status = AsyncMock(
        return_value=[
            VideoStatus(
                media_name="media-1",
                project_id="proj-1",
                status="MEDIA_GENERATION_STATUS_COMPLETED",
                operation_name="op-1",
                workflow_id="wf-1",
            )
        ]
    )
    client.download = AsyncMock(side_effect=lambda name_or_url, out: out)
    return client


class TestVideoT2V:
    def test_happy_path(self, runner: CliRunner, tmp_path: Path) -> None:
        out = tmp_path / "result.mp4"
        client = _make_mock_client()
        with (
            patch("flow_cli.cli_video.FlowApiClient", return_value=client),
            patch("flow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
            patch("flow_cli.cli_video._resolve_profile", return_value="default"),
        ):
            from flow_cli.cli import main

            result = runner.invoke(
                main,
                ["video", "t2v", "test prompt", "-o", str(out), "--poll-interval", "0"],
                catch_exceptions=False,
            )
        assert result.exit_code == 0, result.output
        client.create_project.assert_awaited_once()
        client.generate_video.assert_awaited_once()
        client.download.assert_awaited_once()
        assert "Saved" in result.output


class TestVideoI2V:
    def test_happy_path(self, runner: CliRunner, tmp_path: Path) -> None:
        png = tmp_path / "in.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")  # not a real PNG but enough for the test
        out = tmp_path / "result.mp4"
        client = _make_mock_client()
        client.upload_image = AsyncMock(return_value=MagicMock(name="asset-1"))
        # mock returns a MagicMock; .name attribute is special — set explicitly:
        client.upload_image.return_value.name = "asset-1"

        with (
            patch("flow_cli.cli_video.FlowApiClient", return_value=client),
            patch("flow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
            patch("flow_cli.cli_video._resolve_profile", return_value="default"),
        ):
            from flow_cli.cli import main

            result = runner.invoke(
                main,
                [
                    "video",
                    "i2v",
                    str(png),
                    "push in",
                    "-o",
                    str(out),
                    "--poll-interval",
                    "0",
                ],
                catch_exceptions=False,
            )
        assert result.exit_code == 0, result.output
        client.upload_image.assert_awaited_once()
        # The generate_video request should carry start_asset_uuid
        call = client.generate_video.await_args
        req = call.kwargs.get("req") or call.args[1]
        assert req.start_asset_uuid == "asset-1"
