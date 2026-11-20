"""Click argument contracts for local video assembly."""

from click.testing import CliRunner

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
