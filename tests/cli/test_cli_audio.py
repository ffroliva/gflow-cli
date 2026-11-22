from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.services.audio_narration import NarrationResult


def test_audio_narrate_help_discloses_provider_data_transfer() -> None:
    result = CliRunner().invoke(main, ["audio", "narrate", "--help"])
    assert result.exit_code == 0, result.output
    assert "sent to the selected third-party provider" in result.output.lower()
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
        "status": "ok",
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


def test_audio_narrate_defaults_to_edge_and_accepts_text_file(tmp_path: Path) -> None:
    output = tmp_path / "voice.mp3"
    script = tmp_path / "script.txt"
    script.write_text("Hello", encoding="utf-8")
    result = NarrationResult(output, "edge", "audio/mpeg")
    with patch("gflow_cli.cli_audio.narrate", return_value=result) as handler:
        response = CliRunner().invoke(
            main,
            ["audio", "narrate", "--text-file", str(script), "--out-file", str(output)],
        )
    assert response.exit_code == 0, response.output
    assert handler.call_args.kwargs["text_file"] == script
    assert handler.call_args.kwargs["provider"] == "edge"


def test_audio_narrate_output_id_prints_only_the_output_path(tmp_path: Path) -> None:
    output = tmp_path / "voice.mp3"
    result = NarrationResult(output, "edge", "audio/mpeg")
    with patch("gflow_cli.cli_audio.narrate", return_value=result):
        response = CliRunner().invoke(
            main, ["audio", "narrate", "hello", "--out-file", str(output), "-o", "id"]
        )
    assert response.exit_code == 0, response.output
    assert response.output.strip() == str(output)


def test_audio_narrate_expands_home_in_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    result = NarrationResult(tmp_path / "voice.mp3", "edge", "audio/mpeg")
    with patch("gflow_cli.cli_audio.narrate", return_value=result) as handler:
        response = CliRunner().invoke(
            main,
            ["audio", "narrate", "--text-file", "~/script.txt", "--out-file", "~/voice.mp3"],
        )
    assert response.exit_code == 0, response.output
    assert handler.call_args.kwargs["out_file"] == tmp_path / "voice.mp3"
    assert handler.call_args.kwargs["text_file"] == tmp_path / "script.txt"
