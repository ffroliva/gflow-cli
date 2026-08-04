"""Tests for explicit predictable output flag (--output / -o) on generation commands.

Covers:
- `gflow image t2i` with single asset (--output res.png) and multi asset (--count 2)
- `gflow image i2i` with --output res.png
- `gflow video t2v` with --output clip.mp4
- `gflow video i2v` with --output clip.mp4
- Auto-creation of nested parent directories
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.api.dto import GeneratedImage, ProjectInfo
from gflow_cli.api.video import VideoResult, VideoStatus


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _make_gen_image(name: str) -> GeneratedImage:
    return GeneratedImage(
        media_name=name,
        workflow_id="wf-1",
        seed=123,
        prompt="test prompt",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://flow-content.google/img.png",
        dimensions=(1024, 1024),
    )


class TestPredictableOutputFlag:
    def test_t2i_explicit_output_single_file(self, runner: CliRunner, tmp_path: Path) -> None:
        target_file = tmp_path / "custom_output.png"

        async def _mock_download_image(img: GeneratedImage, out_path: Path) -> Path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"\x89PNG\r\n\x1a\n")
            return out_path

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.generate_image = AsyncMock(return_value=_make_gen_image("media-1"))
        client.download_image = AsyncMock(side_effect=_mock_download_image)

        with (
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch(
                "gflow_cli.cli_image._resolve_project",
                AsyncMock(return_value=(ProjectInfo(project_id="proj-123", title="t"), False)),
            ),
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image.OperationRecorder"),
        ):
            from gflow_cli.cli import main

            res = runner.invoke(
                main,
                ["image", "t2i", "A blue car", "--output", str(target_file)],
                catch_exceptions=False,
            )
            assert res.exit_code == 0
            assert target_file.exists()
            assert target_file.read_bytes() == b"\x89PNG\r\n\x1a\n"

    def test_t2i_explicit_output_multi_count(self, runner: CliRunner, tmp_path: Path) -> None:
        target_file = tmp_path / "output.png"
        file_1 = tmp_path / "output_1.png"
        file_2 = tmp_path / "output_2.png"

        async def _mock_download_image(img: GeneratedImage, out_path: Path) -> Path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(f"img_{img.media_name}".encode())
            return out_path

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.generate_images_batch = AsyncMock(
            return_value=[_make_gen_image("media-1"), _make_gen_image("media-2")]
        )
        client.download_image = AsyncMock(side_effect=_mock_download_image)

        with (
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch(
                "gflow_cli.cli_image._resolve_project",
                AsyncMock(return_value=(ProjectInfo(project_id="proj-123", title="t"), False)),
            ),
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image.OperationRecorder"),
        ):
            from gflow_cli.cli import main

            res = runner.invoke(
                main,
                ["image", "t2i", "A blue car", "--count", "2", "-o", str(target_file)],
                catch_exceptions=False,
            )
            assert res.exit_code == 0
            assert file_1.exists() and file_1.read_bytes() == b"img_media-1"
            assert file_2.exists() and file_2.read_bytes() == b"img_media-2"

    def test_t2i_explicit_output_nested_dir(self, runner: CliRunner, tmp_path: Path) -> None:
        nested_file = tmp_path / "deep" / "nested" / "output.png"

        async def _mock_download_image(img: GeneratedImage, out_path: Path) -> Path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"img_bytes")
            return out_path

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.generate_image = AsyncMock(return_value=_make_gen_image("media-1"))
        client.download_image = AsyncMock(side_effect=_mock_download_image)

        with (
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch(
                "gflow_cli.cli_image._resolve_project",
                AsyncMock(return_value=(ProjectInfo(project_id="proj-123", title="t"), False)),
            ),
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image.OperationRecorder"),
        ):
            from gflow_cli.cli import main

            res = runner.invoke(
                main,
                ["image", "t2i", "A blue car", "-o", str(nested_file)],
                catch_exceptions=False,
            )
            assert res.exit_code == 0
            assert nested_file.exists()
            assert nested_file.read_bytes() == b"img_bytes"

    def test_i2i_explicit_output_file(self, runner: CliRunner, tmp_path: Path) -> None:
        ref_img = tmp_path / "ref.png"
        ref_img.write_bytes(b"ref")
        target_file = tmp_path / "i2i_res.png"

        async def _mock_download_image(img: GeneratedImage, out_path: Path) -> Path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"i2i_bytes")
            return out_path

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.generate_image = AsyncMock(return_value=_make_gen_image("media-1"))
        client.download_image = AsyncMock(side_effect=_mock_download_image)

        with (
            patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
            patch(
                "gflow_cli.cli_image._resolve_project",
                AsyncMock(return_value=(ProjectInfo(project_id="proj-123", title="t"), False)),
            ),
            patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_image.OperationRecorder"),
        ):
            from gflow_cli.cli import main

            res = runner.invoke(
                main,
                [
                    "image",
                    "i2i",
                    "A cat",
                    "--ref",
                    str(ref_img),
                    "-o",
                    str(target_file),
                ],
                catch_exceptions=False,
            )
            assert res.exit_code == 0
            assert target_file.exists()
            assert target_file.read_bytes() == b"i2i_bytes"

    def test_video_t2v_explicit_output_file(self, runner: CliRunner, tmp_path: Path) -> None:
        target_file = tmp_path / "nested_video" / "clip.mp4"
        src_file = tmp_path / "temp_download.mp4"
        src_file.write_bytes(b"video_data")

        def _mock_generate_video(*args: object, **kwargs: object) -> VideoResult:
            return VideoResult(
                status=VideoStatus(media_id="vid-1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
                local_path=src_file,
            )

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.generate_video = AsyncMock(side_effect=_mock_generate_video)

        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_video.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_video.OperationRecorder"),
        ):
            from gflow_cli.cli import main

            res = runner.invoke(
                main,
                ["video", "t2v", "A flying drone", "-o", str(target_file)],
                catch_exceptions=False,
            )
            assert res.exit_code == 0
            assert target_file.exists()
            assert target_file.read_bytes() == b"video_data"
            assert not src_file.exists()

    def test_video_i2v_explicit_output_file(self, runner: CliRunner, tmp_path: Path) -> None:
        ref_img = tmp_path / "frame.png"
        ref_img.write_bytes(b"frame")
        target_file = tmp_path / "nested_i2v" / "i2v_clip.mp4"
        src_file = tmp_path / "temp_i2v_download.mp4"
        src_file.write_bytes(b"video_data_2")

        def _mock_generate_video(*args: object, **kwargs: object) -> VideoResult:
            return VideoResult(
                status=VideoStatus(media_id="vid-2", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
                local_path=src_file,
            )

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.generate_video = AsyncMock(side_effect=_mock_generate_video)

        with (
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_video.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_video.OperationRecorder"),
        ):
            from gflow_cli.cli import main

            res = runner.invoke(
                main,
                [
                    "video",
                    "i2v",
                    "Motion scene",
                    "--initial-frame",
                    str(ref_img),
                    "-o",
                    str(target_file),
                ],
                catch_exceptions=False,
            )
            assert res.exit_code == 0
            assert target_file.exists()
            assert target_file.read_bytes() == b"video_data_2"
