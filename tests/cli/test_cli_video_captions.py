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
    srt = tmp_path / "[/x].srt"
    result = TranscribeResult(source, srt, None, 1.0, ())
    with patch("gflow_cli.cli_video_captions.transcribe_video", return_value=result):
        output = CliRunner().invoke(video, ["transcribe", str(source)])
    assert output.exit_code == 0, output.exception
    assert "Srt File" in output.output and "].srt" in output.output
