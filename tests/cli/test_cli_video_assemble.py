"""Click argument contracts for local video assembly."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli import cli_video
from gflow_cli.services.video_assembly import AssemblyResult

from gflow_cli.cli import main


def test_video_assemble_help_documents_normalization_and_options() -> None:
    result = CliRunner().invoke(main, ["video", "assemble", "--help"])

    assert result.exit_code == 0
    assert "first clip's dimensions and frame rate" in result.output
    for option in ("--music", "--voiceover", "--clip-volume", "--music-volume", "--duck"):
        assert option in result.output


def test_video_assemble_requires_clips_and_output_file() -> None:
    result = CliRunner().invoke(main, ["video", "assemble"])

    assert result.exit_code == 2
    assert "CLIPS" in result.output


def test_video_assemble_rejects_volume_outside_unit_interval() -> None:
    result = CliRunner().invoke(
        main,
        ["video", "assemble", "input.mp4", "--out-file", "out.mp4", "--clip-volume", "1.1"],
    )

    assert result.exit_code == 2
    assert "range" in result.output


def test_video_assemble_preserves_non_mp4_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "take.v2"
    result = AssemblyResult(target, 1.0, 1, False, False, False, 64, 48, 24.0, 1)
    monkeypatch.setattr(cli_video, "resolve_assembly_input", lambda *_args, **_kwargs: target)
    called: list[Path] = []

    def assemble(_clips: object, output: Path, **_kwargs: object) -> AssemblyResult:
        called.append(output)
        return result

    monkeypatch.setattr(cli_video, "assemble_videos", assemble)
    invocation = CliRunner().invoke(
        main, ["video", "assemble", "clip.mp4", "--out-file", str(target)]
    )

    assert invocation.exit_code == 0, invocation.output
    assert called == [target]
