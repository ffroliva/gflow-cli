"""`gflow image t2i|i2i --character FOLDER` wiring (#639, option C). The service itself is
pinned in tests/services/test_character_portability.py::TestApplyCharacterBundles."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from gflow_cli.cli import main


def _invoke(*args: str) -> Any:
    return CliRunner().invoke(main, ["image", *args], catch_exceptions=False)


def test_t2i_hands_the_folders_to_the_runner(tmp_path: Path) -> None:
    nora, kael = tmp_path / "nora", tmp_path / "kael"
    nora.mkdir()
    kael.mkdir()
    with (
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "p"),
        patch("gflow_cli.cli_image.run_with_handlers") as run,
        patch("gflow_cli.cli_image._run_t2i") as runner,
    ):
        result = _invoke("t2i", "Nora and Kael", "--character", str(nora), "--character", str(kael))
        run.call_args.args[0]()  # build the coroutine the handler would run

    assert result.exit_code == 0, result.output
    assert runner.call_args.kwargs["characters"] == (nora, kael)


def test_t2i_refuses_a_character_with_several_prompts(tmp_path: Path) -> None:
    nora = tmp_path / "nora"
    nora.mkdir()
    with patch("gflow_cli.cli_image._resolve_profile") as resolve:
        result = _invoke("t2i", "one", "two", "--character", str(nora))
    assert result.exit_code == 2
    assert "--character is single-prompt only" in result.output
    resolve.assert_not_called()


def test_a_missing_folder_is_a_usage_error(tmp_path: Path) -> None:
    result = _invoke("t2i", "x", "--character", str(tmp_path / "nope"))
    assert result.exit_code == 2


def test_i2i_counts_characters_against_the_reference_budget(tmp_path: Path) -> None:
    from PIL import Image

    folders = []
    for i in range(20):
        d = tmp_path / f"c{i}"
        d.mkdir()
        folders += ["--character", str(d)]
    ref = tmp_path / "r.png"
    Image.new("RGB", (8, 8)).save(ref)
    with patch("gflow_cli.cli_image._resolve_profile") as resolve:
        result = _invoke("i2i", "x", "--ref", str(ref), *folders)
    assert result.exit_code == 2
    assert "reference item" in result.output
    resolve.assert_not_called()
