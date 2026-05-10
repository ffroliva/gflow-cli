"""Click-runner tests for `gflow image upload` and `gflow image t2i`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from flow_cli.api.dto import AssetInfo, GeneratedImage


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
        # Lock the wire contract: project must be titled "gflow-cli upload" so
        # uploads don't pollute the user's library with untitled drafts.
        client.create_project.assert_awaited_once_with(title="gflow-cli upload")
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


# ---------------------------------------------------------------------------
# t2i subcommand
# ---------------------------------------------------------------------------


def _make_generated_image(
    *,
    media_name: str = "img-uuid-1",
    seed: int = 12345,
    model: str = "NARWHAL",
    width: int = 768,
    height: int = 1344,
) -> GeneratedImage:
    return GeneratedImage(
        media_name=media_name,
        workflow_id="wf-1",
        seed=seed,
        prompt="a cat",
        model_name_type=model,
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://flow-content.google/x?Signature=abc",
        dimensions=(width, height),
    )


def _make_t2i_client(
    *,
    images: list[GeneratedImage] | None = None,
) -> MagicMock:
    """Stub FlowApiClient: create_project + generate_image[s_batch] + download_image."""
    images = images or [_make_generated_image()]
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.create_project = AsyncMock(
        return_value=MagicMock(project_id="proj-1", title="gflow-cli t2i")
    )
    client.generate_image = AsyncMock(return_value=images[0])
    client.generate_images_batch = AsyncMock(return_value=images)

    async def _fake_download(image: GeneratedImage, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return out_path

    client.download_image = AsyncMock(side_effect=_fake_download)
    return client


class TestImageT2I:
    def test_t2i_single_image_writes_one_file(self, runner: CliRunner, tmp_path: Path) -> None:
        images = [_make_generated_image(media_name="m1")]
        client = _make_t2i_client(images=images)
        out_dir = tmp_path / "out"

        with (
            patch("flow_cli.cli_image.FlowApiClient", return_value=client),
            patch("flow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("flow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            from flow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "t2i", "a cat", "--out", str(out_dir)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        client.generate_image.assert_awaited_once()
        client.generate_images_batch.assert_not_called()
        # download_image called once for the single image
        assert client.download_image.await_count == 1
        # File should be at out_dir/m1_1.png (no date subdir when --out passed explicitly)
        written = list(out_dir.rglob("*.png"))
        assert len(written) == 1, written

    def test_t2i_multi_image_writes_n_files(self, runner: CliRunner, tmp_path: Path) -> None:
        images = [
            _make_generated_image(media_name="m1", seed=1),
            _make_generated_image(media_name="m2", seed=2),
            _make_generated_image(media_name="m3", seed=3),
        ]
        client = _make_t2i_client(images=images)
        out_dir = tmp_path / "out"

        with (
            patch("flow_cli.cli_image.FlowApiClient", return_value=client),
            patch("flow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("flow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            from flow_cli.cli import main

            result = runner.invoke(
                main,
                ["image", "t2i", "a cat", "-n", "3", "--out", str(out_dir)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        client.generate_images_batch.assert_awaited_once()
        client.generate_image.assert_not_called()
        assert client.download_image.await_count == 3
        written = sorted(p.name for p in out_dir.rglob("*.png"))
        assert written == ["m1_1.png", "m2_2.png", "m3_3.png"], written

    def test_t2i_seed_with_n_gt_1_errors(self, runner: CliRunner, tmp_path: Path) -> None:
        from flow_cli.cli import main

        # No mocks — should fail Click validation BEFORE any I/O.
        result = runner.invoke(
            main,
            ["image", "t2i", "a cat", "--seed", "42", "-n", "2"],
            catch_exceptions=False,
        )

        assert result.exit_code == 2, result.output
        assert "seed" in result.output.lower()

    def test_t2i_invalid_aspect_errors(self, runner: CliRunner) -> None:
        from flow_cli.cli import main

        result = runner.invoke(
            main,
            ["image", "t2i", "a cat", "--aspect", "5:7"],
            catch_exceptions=False,
        )

        assert result.exit_code == 2, result.output

    @pytest.mark.parametrize("count", ["0", "5"])
    def test_t2i_invalid_count_errors(self, runner: CliRunner, count: str) -> None:
        from flow_cli.cli import main

        result = runner.invoke(
            main,
            ["image", "t2i", "a cat", "-n", count],
            catch_exceptions=False,
        )

        assert result.exit_code == 2, result.output

    def test_t2i_passes_model_correctly(self, runner: CliRunner, tmp_path: Path) -> None:
        images = [_make_generated_image(media_name="m1", model="GEM_PIX_2")]
        client = _make_t2i_client(images=images)
        out_dir = tmp_path / "out"

        with (
            patch("flow_cli.cli_image.FlowApiClient", return_value=client),
            patch("flow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch("flow_cli.cli_image._resolve_profile", return_value="default"),
        ):
            from flow_cli.cli import main

            result = runner.invoke(
                main,
                [
                    "image",
                    "t2i",
                    "a cat",
                    "--model",
                    "nano-pro",
                    "--out",
                    str(out_dir),
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        # Inspect the GenerateImageRequest passed to generate_image.
        from flow_cli.api.image import Model

        call = client.generate_image.await_args
        assert call is not None
        req = call.kwargs["req"]
        assert req.model == Model.GEM_PIX_2
        assert req.model.value == "GEM_PIX_2"
