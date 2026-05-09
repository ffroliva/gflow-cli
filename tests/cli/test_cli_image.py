"""Click-runner tests for `gflow image upload`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from flow_cli.api.dto import AssetInfo


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_mock_client(*, asset_name: str = "asset-uuid-123") -> MagicMock:
    """Stub FlowApiClient: create_project + upload_image succeed."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.create_project = AsyncMock(
        return_value=MagicMock(project_id="proj-1", title="gflow-cli upload")
    )
    client.upload_image = AsyncMock(
        return_value=AssetInfo(
            name=asset_name,
            project_id="proj-1",
            workflow_id="wf-1",
            display_name="hero.png",
            width=1024,
            height=1536,
        )
    )
    return client


class TestImageUpload:
    def test_image_upload_prints_uuid(self, runner: CliRunner, tmp_path: Path) -> None:
        png = tmp_path / "hero.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")  # placeholder bytes; client is mocked
        client = _make_mock_client(asset_name="asset-uuid-123")

        with (
            patch("flow_cli.cli_image.FlowApiClient", return_value=client),
            patch("flow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("flow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            from flow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "upload", str(png)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        client.create_project.assert_awaited_once()
        client.upload_image.assert_awaited_once()
        # The UUID must be visible in stdout (primary user-facing value).
        assert "asset-uuid-123" in result.output
        # Dimensions ought to surface for human consumption.
        assert "1024" in result.output and "1536" in result.output

    def test_image_upload_uses_default_profile(self, runner: CliRunner, tmp_path: Path) -> None:
        png = tmp_path / "hero.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        client = _make_mock_client()

        # _resolve_profile is the single chokepoint — assert it's invoked with None
        # when --profile is omitted, which is what "uses default" means in this CLI.
        with (
            patch("flow_cli.cli_image.FlowApiClient", return_value=client),
            patch("flow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("flow_cli.cli_image._resolve_profile", return_value="default") as resolve_mock,
        ):
            from flow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "upload", str(png)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        resolve_mock.assert_called_once_with(None)

    def test_image_upload_errors_on_missing_file(self, runner: CliRunner, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.png"
        # No mocks needed — Click's `exists=True` should reject before any I/O.
        from flow_cli.cli import main

        result = runner.invoke(
            main,
            ["image", "upload", str(missing)],
            catch_exceptions=False,
        )

        assert result.exit_code == 2, result.output
        # Click writes a friendly error mentioning the offending path.
        assert str(missing) in result.output or "does not exist" in result.output.lower()
