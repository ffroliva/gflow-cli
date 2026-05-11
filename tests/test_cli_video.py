"""Click-runner tests for `gflow video` subcommands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.api.dto import VideoOperation, VideoStatus


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
            patch("gflow_cli.cli_video.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        ):
            from gflow_cli.cli import main

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
            patch("gflow_cli.cli_video.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        ):
            from gflow_cli.cli import main

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


class TestVideoBatch:
    def test_three_clip_manifest(self, runner: CliRunner, tmp_path: Path) -> None:
        png = tmp_path / "in.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        out_dir = tmp_path / "out"
        manifest = tmp_path / "m.tsv"
        manifest.write_text(
            f"# header\n\tfirst t2v\t\t\t\n{png}\tsecond i2v\t\t\t\n\tthird t2v\t\t16:9\t\n",
            encoding="utf-8",
        )
        client = _make_mock_client()
        client.upload_image = AsyncMock()
        client.upload_image.return_value = MagicMock()
        client.upload_image.return_value.name = "asset-x"

        with (
            patch("gflow_cli.cli_video.FlowApiClient", return_value=client),
            patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
            patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        ):
            from gflow_cli.cli import main

            result = runner.invoke(
                main,
                [
                    "video",
                    "batch",
                    str(manifest),
                    "--out-dir",
                    str(out_dir),
                    "--poll-interval",
                    "0",
                ],
                catch_exceptions=False,
            )
        assert result.exit_code == 0, result.output
        # single shared project for the whole batch
        client.create_project.assert_awaited_once()
        # 3 generations, 1 upload (only the i2v row)
        assert client.generate_video.await_count == 3
        assert client.upload_image.await_count == 1


def test_run_batch_fans_out_via_asyncio_gather(tmp_path: Path) -> None:
    """`_run_batch` MUST fan out manifest entries via ``asyncio.gather`` —
    not a sequential for-loop. With 4 entries and an artificial hold inside
    ``generate_video`` we should observe peak in-flight >= 2 (proves at least
    two coroutines run concurrently). Without fan-out the peak would be 1.

    The per-worker Page pool in ``FlowApiClient`` (T2) gates the actual
    in-flight count to ``Settings.concurrency``; this test mocks the client
    so the pool isn't engaged — what we're proving is that ``_run_batch``
    creates concurrent tasks for ``asyncio.gather`` to schedule.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from gflow_cli.api.dto import VideoOperation
    from gflow_cli.api.video import Aspect
    from gflow_cli.cli_video import _run_batch
    from gflow_cli.manifest import ManifestEntry

    in_flight = {"current": 0, "peak": 0}

    async def _gen_video_with_hold(*_: object, **__: object) -> VideoOperation:
        in_flight["current"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["current"])
        # Hold the slot briefly so sibling coroutines have a chance to enter.
        await asyncio.sleep(0.01)
        try:
            i = in_flight["peak"]
            return VideoOperation(
                media_name=f"media-{i}",
                project_id="proj-1",
                operation_name=f"op-{i}",
                workflow_id=f"wf-{i}",
            )
        finally:
            in_flight["current"] -= 1

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.create_project = AsyncMock(return_value=MagicMock(project_id="proj-1", title="t"))
    client.generate_video = AsyncMock(side_effect=_gen_video_with_hold)
    # _poll_and_download is patched to a no-op below, so get_video_status /
    # download are never called — leave them as plain MagicMocks.
    client.download = AsyncMock(return_value=None)

    entries = [ManifestEntry(prompt=f"clip {i}", aspect=Aspect.PORTRAIT) for i in range(4)]

    with (
        patch("gflow_cli.cli_video.FlowApiClient", return_value=client),
        patch("gflow_cli.cli_video._poll_and_download", new=AsyncMock(return_value=None)),
    ):
        asyncio.run(
            _run_batch(
                profile_dir=tmp_path,
                headless=True,
                entries=entries,
                out_root=tmp_path,
                poll_interval=0.0,
            )
        )

    # All 4 entries processed and at least 2 concurrent (proves asyncio.gather).
    assert client.generate_video.await_count == 4
    assert in_flight["peak"] >= 2, (
        f"_run_batch ran sequentially — peak in-flight = {in_flight['peak']}. "
        "Expected ≥ 2 (asyncio.gather fan-out)."
    )
