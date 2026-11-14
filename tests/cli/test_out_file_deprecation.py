"""Tests for #1158: --out-file flag, -o/--output deprecation, and video upscale -o deprecation."""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from gflow_cli._cli_helpers import out_file_option
from gflow_cli.cli import main

_EXPECTED_DEPRECATION_WARNING = (
    "-o/--output PATH is deprecated and will select the output format in a future release "
    "(-o json|table|id). Use --out-file PATH."
)

_NINE_COMMANDS = (
    ("data", "errors", "export"),
    ("image", "t2i"),
    ("image", "i2i"),
    ("scene", "create"),
    ("video", "t2v"),
    ("video", "i2v"),
    ("video", "r2v"),
    ("video", "chain"),
    ("video", "extend"),
)


def _get_command(path: tuple[str, ...]) -> click.Command:
    current: click.BaseCommand = main
    for part in path:
        assert isinstance(current, click.Group)
        sub = current.get_command(click.Context(current), part)
        assert sub is not None, f"Command {part} not found in {current.name}"
        current = sub
    assert isinstance(current, click.Command)
    return current


def test_out_file_decorator_direct() -> None:
    @click.command()
    @out_file_option(
        dest="output_file",
        help="Explicit output file path for the generated asset.",
    )
    def dummy_cmd(output_file: Path | None) -> None:
        click.echo(f"dest={output_file}")

    runner = CliRunner()

    # 1. New flag
    res_new = runner.invoke(dummy_cmd, ["--out-file", "foo.png"])
    assert res_new.exit_code == 0
    assert res_new.stdout == "dest=foo.png\n"
    assert res_new.stderr == ""

    # 2. Old flag -o
    res_old_short = runner.invoke(dummy_cmd, ["-o", "bar.png"])
    assert res_old_short.exit_code == 0
    assert res_old_short.stdout == "dest=bar.png\n"
    assert res_old_short.stderr == f"{_EXPECTED_DEPRECATION_WARNING}\n"

    # 3. Old flag --output
    res_old_long = runner.invoke(dummy_cmd, ["--output", "baz.png"])
    assert res_old_long.exit_code == 0
    assert res_old_long.stdout == "dest=baz.png\n"
    assert res_old_long.stderr == f"{_EXPECTED_DEPRECATION_WARNING}\n"

    # 4. Both flags
    res_both1 = runner.invoke(dummy_cmd, ["--out-file", "foo.png", "-o", "bar.png"])
    assert res_both1.exit_code == 2
    assert "--out-file" in res_both1.stderr

    res_both2 = runner.invoke(dummy_cmd, ["-o", "bar.png", "--out-file", "foo.png"])
    assert res_both2.exit_code == 2
    assert "--out-file" in res_both2.stderr

    # 5. Help text
    res_help = runner.invoke(dummy_cmd, ["--help"])
    assert res_help.exit_code == 0
    assert "--out-file" in res_help.output
    assert "Deprecated: use --out-file." in res_help.output


@pytest.mark.parametrize("cmd_path", _NINE_COMMANDS)
def test_nine_commands_have_out_file_option(cmd_path: tuple[str, ...]) -> None:
    cmd = _get_command(cmd_path)
    opt_names = {
        opt for param in cmd.params if isinstance(param, click.Option) for opt in param.opts
    }
    assert "--out-file" in opt_names, f"{' '.join(cmd_path)} missing --out-file"
    assert "-o" in opt_names, f"{' '.join(cmd_path)} missing -o"
    assert "--output" in opt_names, f"{' '.join(cmd_path)} missing --output"
