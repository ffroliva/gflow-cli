"""Tests for #1158: output format flag (-o/--output json|table|id) and path remediation."""

from __future__ import annotations

import inspect
from pathlib import Path

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


def test_data_list_explicit_table_in_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.cli import main
    from gflow_cli.config import reset_settings
    from gflow_cli.data.models import ProjectRecord
    from gflow_cli.data.repository import DataRepository
    from gflow_cli.data.store import DataStore

    db = tmp_path / "gflow.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    reset_settings()
    with DataStore.open(db) as store:
        repo = DataRepository(store)
        repo.upsert_profile("p1", tmp_path / "profile_p1")
        repo.upsert_project(
            ProjectRecord(
                id="rec-1",
                profile_name="p1",
                flow_project_id="proj-explicit",
                title="My Title",
                source="gflow-cli",
            )
        )

    runner = CliRunner()
    res = runner.invoke(main, ["data", "list", "projects", "-o", "table"])
    assert res.exit_code == 0
    assert "proj-explicit" in res.stdout
    assert "{" not in res.stdout


def test_output_format_type_to_info_dict() -> None:
    from gflow_cli._cli_helpers import OutputFormatType

    t_all = OutputFormatType(allow_id=True)
    assert list(t_all.to_info_dict()["choices"]) == ["id", "json", "table"]

    t_no_id = OutputFormatType(allow_id=False)
    assert list(t_no_id.to_info_dict()["choices"]) == ["json", "table"]


def test_command_tree_reflects_output_format_choices() -> None:
    from gflow_cli._cli_helpers import command_tree
    from gflow_cli.cli import main

    tree = command_tree(main)
    # Find image t2i in tree
    t2i_cmd = next(c for c in tree["commands"] if c["path"] == ["image", "t2i"])
    out_param = next(p for p in t2i_cmd["params"] if "--output" in p["opts"])
    assert out_param["choices"] == ["id", "json", "table"]
