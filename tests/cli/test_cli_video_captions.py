"""CLI behavior for local transcription and caption burn-in."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from gflow_cli.cli_video import video
from gflow_cli.services.video_captions import CaptionsResult, TranscribeResult


def test_video_captions_json_and_id_output(tmp_path: Path) -> None:
    output = tmp_path / "captioned.mp4"
    srt = tmp_path / "clip.srt"
    result = CaptionsResult(tmp_path / "clip.mp4", output, srt, 2.0, 20, 5, 1234)
    with patch("gflow_cli.cli_video_captions.caption_video", return_value=result):
        response = CliRunner().invoke(
            video, ["captions", str(result.source_file), "--srt", str(srt), "--json"]
        )
    assert response.exit_code == 0, response.output
    assert json.loads(response.output)["frames_burned"] == 5


def test_transcribe_reports_srt_path_for_id_output(tmp_path: Path) -> None:
    result = TranscribeResult(tmp_path / "clip.mp4", tmp_path / "clip.srt", "en", 2.0, ())
    with patch("gflow_cli.cli_video_captions.transcribe_video", return_value=result):
        response = CliRunner().invoke(video, ["transcribe", str(result.source_file), "-o", "id"])
    assert response.exit_code == 0, response.output
    assert response.output.strip() == str(result.srt_file)


def test_video_caption_commands_are_credit_free() -> None:
    assert "transcribe" in video.commands
    assert "captions" in video.commands


def test_video_captions_table_escapes_bracketed_output_paths(tmp_path: Path) -> None:
    from click.testing import CliRunner

    source = tmp_path / "clip.mp4"
    result = CaptionsResult(source, "[/x].mp4", tmp_path / "captions.srt", 1.0, 10, 2, 500)
    with patch("gflow_cli.cli_video_captions.caption_video", return_value=result):
        output = CliRunner().invoke(video, ["captions", str(source), "--srt", "captions.srt"])
    assert output.exit_code == 0, output.exception
    assert output.exit_code == 0


def test_cli_caption_commands_refuse_explicit_out_file_under_cloud_storage(
    tmp_path: Path,
) -> None:
    import pytest

    from gflow_cli.cli import main
    from gflow_cli.config import reset_settings

    mp = pytest.MonkeyPatch()
    mp.setenv("GFLOW_CLI_STORAGE_URI", "s3://bucket/output")
    reset_settings()
    source = tmp_path / "clip.mp4"
    srt = tmp_path / "captions.srt"
    caption_res = CaptionsResult(source, tmp_path / "clip-captions.mp4", srt, 1.0, 10, 2, 500)
    transcribe_res = TranscribeResult(source, srt, "en", 1.0, ())
    try:
        with (
            patch("gflow_cli.cli_video_captions.transcribe_video", return_value=transcribe_res),
            patch("gflow_cli.cli_video_captions.caption_video", return_value=caption_res),
        ):
            for args in (
                [
                    "video",
                    "transcribe",
                    str(source),
                    "--out-file",
                    str(tmp_path / "out.srt"),
                    "--json",
                ],
                [
                    "video",
                    "captions",
                    str(source),
                    "--srt",
                    str(srt),
                    "--out-file",
                    str(tmp_path / "out.mp4"),
                    "--json",
                ],
            ):
                response = CliRunner().invoke(main, args)
                assert response.exit_code == 11, response.stdout
                error = json.loads(response.stdout)["error"]
                assert "GFLOW_CLI_STORAGE_URI" in error["detail"]
                assert error["charged"] == "no"

            plain = CliRunner().invoke(
                main, ["video", "captions", str(source), "--srt", str(srt), "--json"]
            )
            assert plain.exit_code == 0, plain.stdout
    finally:
        mp.undo()
        reset_settings()
