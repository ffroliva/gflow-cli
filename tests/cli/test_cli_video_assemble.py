"""Click argument contracts for local video assembly."""

import errno
from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli import cli_video
from gflow_cli.cli import main
from gflow_cli.services import video_assembly
from gflow_cli.services.video_assembly import AssemblyResult


def test_video_assemble_help_documents_normalization_and_options() -> None:
    result = CliRunner().invoke(main, ["video", "assemble", "--help"])

    assert result.exit_code == 0
    assert "first clip's dimensions and frame rate" in result.output
    assert "same-ratio clips can be resized" in result.output
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


def test_video_assemble_reports_stale_unlink_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time

    clip = tmp_path / "clip.mp4"
    clip.touch()
    stale_out = tmp_path / "stale.mp4"
    stale_out.touch()
    past = time.time() - 120.0
    os.utime(stale_out, (past, past))

    def denied_rename(src: Path, dst: Path) -> None:
        raise OSError(errno.EPERM, "stale file locked by antivirus")

    monkeypatch.setattr(video_assembly, "_platform_no_replace_rename", denied_rename)

    response = CliRunner().invoke(
        main, ["video", "assemble", str(clip), "--out-file", str(stale_out)]
    )
    assert response.exit_code != 0
    assert (
        "leftover could not be removed" in response.output
        or "could not remove stale output reservation" in response.output.lower()
    )
