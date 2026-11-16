"""Tests for #1158: output format flag (-o/--output json|table|id) and path remediation."""

from __future__ import annotations

import inspect

import click
import pytest
from click.testing import CliRunner as _CliRunner

from gflow_cli._cli_helpers import (
    output_format_option,
)


def CliRunner() -> _CliRunner:  # noqa: N802 - drop-in for click.testing.CliRunner
    """A runner with stderr captured apart on every Click version pyproject allows (>=8.1)."""
    if "mix_stderr" in inspect.signature(_CliRunner.__init__).parameters:
        return _CliRunner(mix_stderr=False)  # type: ignore[call-arg]  # Click < 8.2
    return _CliRunner()


def test_output_format_default() -> None:
    @click.command()
    @output_format_option()
    def cmd(output_format: str) -> None:
        click.echo(f"format={output_format}")

    runner = CliRunner()
    res = runner.invoke(cmd, [])
    assert res.exit_code == 0
    assert res.stdout == "format=table\n"


@pytest.mark.parametrize(
    ("args", "expected_format"),
    [
        (["-o", "json"], "json"),
        (["--output", "json"], "json"),
        (["--json"], "json"),
        (["-o", "table"], "table"),
        (["--output", "table"], "table"),
        (["-o", "id"], "id"),
        (["--output", "id"], "id"),
        (["-o", "JSON"], "json"),
        (["--output", "Table"], "table"),
        (["-o", "ID"], "id"),
        (["--json", "-o", "json"], "json"),
        (["--json", "--output", "json"], "json"),
    ],
)
def test_output_format_valid_options(args: list[str], expected_format: str) -> None:
    @click.command()
    @output_format_option()
    def cmd(output_format: str) -> None:
        click.echo(f"format={output_format}")

    runner = CliRunner()
    res = runner.invoke(cmd, args)
    assert res.exit_code == 0
    assert res.stdout == f"format={expected_format}\n"


@pytest.mark.parametrize(
    "args",
    [
        ["--json", "-o", "table"],
        ["--json", "--output", "table"],
        ["--json", "-o", "id"],
        ["--json", "--output", "id"],
    ],
)
def test_output_format_conflict_with_json_flag(args: list[str]) -> None:
    @click.command()
    @output_format_option()
    def cmd(output_format: str) -> None:
        click.echo(f"format={output_format}")

    runner = CliRunner()
    res = runner.invoke(cmd, args)
    assert res.exit_code == 2
    assert "Cannot combine --json with -o/--output" in res.stderr


def test_output_format_invalid_choice() -> None:
    @click.command()
    @output_format_option()
    def cmd(output_format: str) -> None:
        click.echo(f"format={output_format}")

    runner = CliRunner()
    res = runner.invoke(cmd, ["-o", "yaml"])
    assert res.exit_code == 2
    assert "Invalid value for '-o' / '--output'" in res.stderr or "is not one of" in res.stderr


def test_output_format_remediation_for_file_path() -> None:
    @click.command()
    @output_format_option(has_out_file=True)
    def cmd(output_format: str) -> None:
        click.echo(f"format={output_format}")

    runner = CliRunner()
    res = runner.invoke(cmd, ["-o", "clip.mp4"])
    assert res.exit_code == 2
    assert "--output now selects the format; use --out-file clip.mp4" in res.stderr

    res_long = runner.invoke(cmd, ["--output", "image.png"])
    assert res_long.exit_code == 2
    assert "--output now selects the format; use --out-file image.png" in res_long.stderr


def test_output_format_remediation_for_video_upscale_dir() -> None:
    @click.command()
    @output_format_option(is_upscale_out=True)
    def cmd(output_format: str) -> None:
        click.echo(f"format={output_format}")

    runner = CliRunner()
    res = runner.invoke(cmd, ["-o", "./out_dir"])
    assert res.exit_code == 2
    assert "use --out ./out_dir" in res.stderr


def test_output_format_allow_id_false() -> None:
    @click.command()
    @output_format_option(allow_id=False)
    def cmd(output_format: str) -> None:
        click.echo(f"format={output_format}")

    runner = CliRunner()
    res_table = runner.invoke(cmd, ["-o", "table"])
    assert res_table.exit_code == 0
    assert res_table.stdout == "format=table\n"

    res_json = runner.invoke(cmd, ["-o", "json"])
    assert res_json.exit_code == 0
    assert res_json.stdout == "format=json\n"

    res_id = runner.invoke(cmd, ["-o", "id"])
    assert res_id.exit_code == 2
    assert (
        "Invalid value for '-o' / '--output': 'id' is not one of 'json', 'table'." in res_id.stderr
    )
