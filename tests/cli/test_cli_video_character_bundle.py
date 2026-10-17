"""`gflow video t2v|r2v --character FOLDER` wiring (#639): several folders, one clip.
The service is pinned in tests/services/test_character_portability.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from gflow_cli.cli import main


def _invoke(*args: str) -> Any:
    return CliRunner().invoke(main, ["video", *args], catch_exceptions=False)


def _folders(tmp_path: Path) -> tuple[Path, Path]:
    nora, kael = tmp_path / "nora", tmp_path / "kael"
    nora.mkdir()
    kael.mkdir()
    return nora, kael


def test_t2v_hands_every_folder_to_the_runner(tmp_path: Path) -> None:
    nora, kael = _folders(tmp_path)
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "p"),
        patch("gflow_cli.cli_video.run_with_handlers") as run,
        patch("gflow_cli.cli_video._run_t2v") as runner,
    ):
        result = _invoke("t2v", "Nora and Kael", "--character", str(nora), "--character", str(kael))
        run.call_args.args[0]()

    assert result.exit_code == 0, result.output
    assert runner.call_args.kwargs["characters"] == (nora, kael)


def test_r2v_hands_the_folders_to_the_runner(tmp_path: Path) -> None:
    from PIL import Image

    nora, _ = _folders(tmp_path)
    ref = tmp_path / "cafe.png"
    Image.new("RGB", (8, 8)).save(ref)
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "p"),
        patch("gflow_cli.cli_video.run_with_handlers") as run,
        patch("gflow_cli.cli_video._run_r2v") as runner,
    ):
        result = _invoke("r2v", "Nora in the cafe", "--ref", str(ref), "--character", str(nora))
        run.call_args.args[0]()

    assert result.exit_code == 0, result.output
    assert runner.call_args.kwargs["characters"] == (nora,)


def test_i2v_has_no_character_option(tmp_path: Path) -> None:
    nora, _ = _folders(tmp_path)
    result = _invoke("i2v", "x", "--character", str(nora))
    assert result.exit_code == 2
    assert "No such option" in result.output
