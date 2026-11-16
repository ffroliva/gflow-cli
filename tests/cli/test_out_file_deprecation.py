"""Tests for #1158: --out-file flag, -o/--output selector, and video upscale -o remediation."""

from __future__ import annotations

import inspect
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.testing import CliRunner as _CliRunner

from gflow_cli._cli_helpers import (
    OutputFormatOption,
    UpscaleLegacyShortOption,
    out_file_option,
)
from gflow_cli.cli import main
from tests.cli.test_cli_data_errors_retention import _seed_failed_op
from tests.cli.test_cli_image import _make_generated_image, _make_t2i_client
from tests.mcp._cli_tree import cli_leaves


def CliRunner() -> _CliRunner:  # noqa: N802 - drop-in for click.testing.CliRunner
    """A runner with stderr captured apart on every Click version pyproject allows (>=8.1)."""
    if "mix_stderr" in inspect.signature(_CliRunner.__init__).parameters:
        return _CliRunner(mix_stderr=False)  # type: ignore[call-arg]  # Click < 8.2
    return _CliRunner()


_MEDIA_ID = "3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14"

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

# New verbs (the #1193 bridges): --out-file and no -o/--output PATH spelling at all.
# The walk sees every registered name, so the `gen` alias is listed too.
_OUT_FILE_ONLY = {"image generate", "video generate", "image gen", "video gen"}


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

    # 2. Old flag -o is rejected
    res_old_short = runner.invoke(dummy_cmd, ["-o", "bar.png"])
    assert res_old_short.exit_code == 2

    # 3. Old flag --output is rejected
    res_old_long = runner.invoke(dummy_cmd, ["--output", "baz.png"])
    assert res_old_long.exit_code == 2

    # 4. Help text
    res_help = runner.invoke(dummy_cmd, ["--help"])
    assert res_help.exit_code == 0
    assert "--out-file" in res_help.output
    assert "Deprecated" not in res_help.output


@pytest.mark.parametrize("cmd_path", _NINE_COMMANDS)
def test_nine_commands_have_out_file_option(cmd_path: tuple[str, ...]) -> None:
    cmd = _get_command(cmd_path)
    opt_names = {
        opt for param in cmd.params if isinstance(param, click.Option) for opt in param.opts
    }
    assert "--out-file" in opt_names, f"{' '.join(cmd_path)} missing --out-file"
    out_file_param = next(
        p for p in cmd.params if isinstance(p, click.Option) and "--out-file" in p.opts
    )
    assert isinstance(out_file_param.type, click.Path)


def test_click_tree_guard() -> None:
    """Click-tree guard: walk every leaf of gflow_cli.cli.main.

    Exactly the nine commands, plus the --out-file bridge verbs, have --out-file.
    Any option anywhere spelled -o or --output must NOT be a Path.
    Every -o/--output is an OutputFormatOption (except video upscale legacy -o guard).
    """
    commands: dict[str, click.Command] = {}

    def _walk(cmd: click.BaseCommand, path: str = "") -> None:
        if isinstance(cmd, click.Group):
            for name, sub in cmd.commands.items():
                _walk(sub, f"{path} {name}".strip())
        elif isinstance(cmd, click.Command):
            commands[path] = cmd

    _walk(main)

    expected_nine = {" ".join(path) for path in _NINE_COMMANDS}

    leaves = cli_leaves(main)
    assert expected_nine.issubset(leaves)

    commands_with_out_file = {
        path
        for path, cmd in commands.items()
        if any(isinstance(p, click.Option) and "--out-file" in p.opts for p in cmd.params)
    }
    assert commands_with_out_file == expected_nine | _OUT_FILE_ONLY

    # Check that NO -o or --output option takes a Path anywhere
    for path, cmd in commands.items():
        for param in cmd.params:
            if isinstance(param, click.Option):
                if any(opt in ("-o", "--output") for opt in param.opts):
                    assert not isinstance(param.type, click.Path), (
                        f"{path} option {param.opts} is an invalid path-typed -o/--output"
                    )
                    assert isinstance(param, (OutputFormatOption, UpscaleLegacyShortOption)), (
                        f"{path} option {param.opts} is not an OutputFormatOption"
                    )


@pytest.mark.parametrize(
    ("argv", "count", "exit_code", "remediation", "written"),
    [
        (["--out-file", "new.png"], 1, 0, None, ["new.png"]),
        (["-o", "old.png"], 1, 2, "--output now selects the format; use --out-file", []),
        (["--output", "old.png"], 1, 2, "--output now selects the format; use --out-file", []),
        (["--out-file", "new.png", "-o", "json"], 1, 0, None, ["new.png"]),
        (
            ["-n", "4", "--out-file", "multi.png"],
            4,
            0,
            None,
            [f"multi_{i}.png" for i in range(1, 5)],
        ),
    ],
    ids=[
        "new-flag",
        "legacy-short-flag-rejected",
        "legacy-long-flag-rejected",
        "out-file-with-json-format",
        "multi-image-naming",
    ],
)
def test_cli_runner_image_t2i(
    tmp_path: Path,
    argv: list[str],
    count: int,
    exit_code: int,
    remediation: str | None,
    written: list[str],
) -> None:
    media = str(uuid.uuid4())
    images = [_make_generated_image(media_name=f"{media}_{i}") for i in range(count)]
    client = _make_t2i_client(images=images)
    args = [str(tmp_path / a) if a.endswith(".png") else a for a in argv]

    with (
        patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
    ):
        result = CliRunner().invoke(main, ["image", "t2i", "a dog", *args])

    assert result.exit_code == exit_code, result.output
    if remediation:
        assert remediation in result.output
        client.generate_image.assert_not_called()
    for name in written:
        assert (tmp_path / name).exists(), name


def test_cli_runner_video_t2v(tmp_path: Path) -> None:
    runner = CliRunner()
    file_new = tmp_path / "new.mp4"
    file_old = tmp_path / "old.mp4"

    mock_run = AsyncMock(return_value=None)
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_video._run_t2v", mock_run),
    ):
        res_new = runner.invoke(
            main, ["video", "t2v", "a running horse", "--out-file", str(file_new)]
        )
        assert res_new.exit_code == 0, res_new.output
        assert mock_run.await_args.kwargs["output_file"] == file_new

        mock_run.reset_mock()
        res_old = runner.invoke(main, ["video", "t2v", "a flying eagle", "-o", str(file_old)])
        assert res_old.exit_code == 2, res_old.output
        assert "--output now selects the format; use --out-file" in res_old.output
        mock_run.assert_not_called()


def test_cli_runner_scene_create(tmp_path: Path) -> None:
    runner = CliRunner()
    file_new = tmp_path / "scene_new.mp4"
    file_old = tmp_path / "scene_old.mp4"

    mock_run_create = AsyncMock(return_value=None)
    with (
        patch("gflow_cli.cli_scene._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_scene._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_scene._run_create", mock_run_create),
    ):
        res_new = runner.invoke(
            main,
            ["scene", "create", "--project", "proj-1", "wf-1", "--out-file", str(file_new)],
        )
        assert res_new.exit_code == 0, res_new.output
        assert mock_run_create.await_args.kwargs["output"] == file_new

        mock_run_create.reset_mock()
        res_old = runner.invoke(
            main,
            ["scene", "create", "--project", "proj-1", "wf-1", "-o", str(file_old)],
        )
        assert res_old.exit_code == 2, res_old.output
        assert "--output now selects the format; use --out-file" in res_old.output
        mock_run_create.assert_not_called()


def test_cli_runner_data_errors_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "gflow.db"
    _seed_failed_op(db)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))

    runner = CliRunner()
    file_new = tmp_path / "export_new.jsonl"
    file_old = tmp_path / "export_old.jsonl"

    res_new = runner.invoke(main, ["data", "errors", "export", "--out-file", str(file_new)])
    assert res_new.exit_code == 0, res_new.output
    assert file_new.exists()
    assert len(file_new.read_text(encoding="utf-8").strip().splitlines()) >= 1

    res_old = runner.invoke(main, ["data", "errors", "export", "-o", str(file_old)])
    assert res_old.exit_code == 2, res_old.output
    assert "--out-file" in res_old.output.lower()


def test_video_upscale_out_deprecation(tmp_path: Path) -> None:
    runner = CliRunner()
    dir1 = tmp_path / "d1"
    dir2 = tmp_path / "d2"
    dir1.mkdir()
    dir2.mkdir()

    # Legacy -o flag alone -> exit 2 before any work
    res_old = runner.invoke(
        main, ["video", "upscale", _MEDIA_ID, "-o", str(dir2), "--project", "PROJ1"]
    )
    assert res_old.exit_code == 2
    assert "use --out" in res_old.output

    # Both flags -> exit 2 before any work
    res_both = runner.invoke(
        main, ["video", "upscale", _MEDIA_ID, "--out", str(dir1), "-o", str(dir2)]
    )
    assert res_both.exit_code == 2
    assert "use --out" in res_both.output

    # New flag alone -> no error
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.upsample_video = AsyncMock(return_value=dir1 / "upscaled.mp4")

    with (
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video.FlowApiClient", return_value=mock_client),
    ):
        res_new = runner.invoke(
            main, ["video", "upscale", _MEDIA_ID, "--out", str(dir1), "--project", "PROJ1"]
        )
        assert res_new.exit_code == 0, res_new.output
        out_path = mock_client.upsample_video.await_args.kwargs["out_path"]
        assert out_path.is_relative_to(dir1), out_path
