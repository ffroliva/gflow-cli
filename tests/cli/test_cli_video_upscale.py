"""CLI tests for `gflow video upscale`."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.errors import UpscaleUnavailableError

_MEDIA_ID = "3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14"
_PROJECT_ID = "ffb768fb-cf2d-48b7-a135-92978667c37d"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _mock_client(saved: Path) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.upsample_video = AsyncMock(return_value=saved)
    return client


def _invoke(
    runner: CliRunner,
    args: list[str],
    *,
    client: MagicMock | None = None,
    tmp_path: Path | None = None,
    catalog_project: str | None = None,
):
    from gflow_cli.cli import main

    with ExitStack() as stack:
        stack.enter_context(patch("gflow_cli.cli_image._resolve_profile", return_value="default"))
        stack.enter_context(patch("gflow_cli.cli_video._resolve_profile", return_value="default"))
        stack.enter_context(
            patch("gflow_cli.cli_video._make_provider_dir", return_value=Path("/tmp/p"))
        )
        if catalog_project is not None or "lookup_project_in_catalog" in str(stack):
            stack.enter_context(
                patch(
                    "gflow_cli.cli_image._lookup_project_in_catalog",
                    return_value=catalog_project,
                )
            )
            stack.enter_context(
                patch(
                    "gflow_cli.cli_image.lookup_project_in_catalog",
                    return_value=catalog_project,
                )
            )
        if client is not None:
            stack.enter_context(patch("gflow_cli.cli_video.FlowApiClient", return_value=client))
        return runner.invoke(main, args, catch_exceptions=False)


def test_video_upscale_explicit_project(runner: CliRunner, tmp_path: Path) -> None:
    client = _mock_client(tmp_path / f"{_MEDIA_ID}_1080p.mp4")
    result = _invoke(
        runner,
        ["video", "upscale", _MEDIA_ID, "--scale", "1080p", "--project", _PROJECT_ID],
        client=client,
        tmp_path=tmp_path,
    )
    assert result.exit_code == 0, result.output
    assert "Saved:" in result.output
    client.upsample_video.assert_awaited_once()


def test_video_upscale_resolves_project_from_catalog(runner: CliRunner, tmp_path: Path) -> None:
    client = _mock_client(tmp_path / f"{_MEDIA_ID}_1080p.mp4")
    result = _invoke(
        runner,
        ["video", "upscale", _MEDIA_ID, "--scale", "1080p"],
        client=client,
        tmp_path=tmp_path,
        catalog_project=_PROJECT_ID,
    )
    assert result.exit_code == 0, result.output
    assert "Saved:" in result.output
    client.upsample_video.assert_awaited_once()


def test_video_upscale_missing_project_fails_fast(runner: CliRunner) -> None:
    result = _invoke(
        runner,
        ["video", "upscale", _MEDIA_ID, "--scale", "1080p"],
        catalog_project=None,
    )
    assert result.exit_code == 2
    assert "Could not resolve the owning project" in result.output


def test_video_upscale_invalid_scale(runner: CliRunner) -> None:
    result = _invoke(
        runner,
        ["video", "upscale", _MEDIA_ID, "--scale", "4k"],
    )
    assert result.exit_code == 2
    assert "Invalid value for '--scale'" in result.output


def test_video_upscale_invalid_media_id(runner: CliRunner) -> None:
    result = _invoke(
        runner,
        ["video", "upscale", "not-a-uuid", "--scale", "1080p"],
    )
    assert result.exit_code == 2
    assert "MEDIA_ID must be a bare UUID" in result.output


def test_video_upscale_unavailable_exit_22(runner: CliRunner, tmp_path: Path) -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.upsample_video = AsyncMock(
        side_effect=UpscaleUnavailableError(
            detail="1080p disabled",
            status=403,
            route="video_upscale",
        )
    )

    result = _invoke(
        runner,
        ["video", "upscale", _MEDIA_ID, "--scale", "1080p", "--project", _PROJECT_ID],
        client=client,
        tmp_path=tmp_path,
    )
    assert result.exit_code == 22, result.output
