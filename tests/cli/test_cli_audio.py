from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.services.audio_narration import NarrationResult


def test_audio_narrate_help_discloses_provider_data_transfer() -> None:
    result = CliRunner().invoke(main, ["audio", "narrate", "--help"])
    assert result.exit_code == 0, result.output
    assert "sent to the selected provider" in result.output.lower()
    assert "flow credits" in result.output.lower()


def test_audio_narrate_json_reports_provider_and_output(tmp_path: Path) -> None:
    output = tmp_path / "voice.mp3"
    result = NarrationResult(output, "edge", "audio/mpeg")
    with patch("gflow_cli.cli_audio.narrate", return_value=result):
        response = CliRunner().invoke(
            main, ["audio", "narrate", "hello", "--out-file", str(output), "--json"]
        )
    assert response.exit_code == 0, response.output
    assert json.loads(response.output) == {
        "out_file": str(output),
        "provider": "edge",
        "media_type": "audio/mpeg",
    }


def test_audio_narrate_text_and_text_file_are_exclusive(tmp_path: Path) -> None:
    output = tmp_path / "voice.mp3"
    response = CliRunner().invoke(
        main,
        ["audio", "narrate", "hello", "--text-file", "script.txt", "--out-file", str(output)],
    )
    assert response.exit_code == 2
    assert "mutually exclusive" in response.output.lower()
