"""GHSA-hqfj-4jww-76f2: MCP ``output``, ``out_dir`` and ``profile`` stay inside gflow's dirs."""

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from gflow_cli.config import reset_settings
from gflow_cli.mcp import tools as tools_mod
from gflow_cli.mcp.tools import _confine_to_output_dir  # pyright: ignore[reportPrivateUsage]


def _link_dir(link: Path, target: Path) -> None:
    # A junction needs no privilege on Windows; a symlink does.
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.fixture()
def output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(out))
    reset_settings()
    yield out
    reset_settings()


@pytest.mark.parametrize("bad", ["../x.png", "a/../../x.png", ".."])
def test_rejects_relative_escape(output_dir: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        _confine_to_output_dir(bad)


def test_rejects_absolute_path_elsewhere(output_dir: Path) -> None:
    with pytest.raises(ValueError):
        _confine_to_output_dir(str(output_dir.parent / "elsewhere" / "x.png"))


def test_rejects_a_link_that_leads_out(output_dir: Path) -> None:
    outside = output_dir.parent / "outside"
    outside.mkdir()
    _link_dir(output_dir / "ln", outside)
    with pytest.raises(ValueError):
        _confine_to_output_dir("ln/x.png")


@pytest.mark.parametrize("ok", ["x.png", "sub/dir/x.png", "."])
def test_accepts_inside(output_dir: Path, ok: str) -> None:
    assert _confine_to_output_dir(ok).is_relative_to(output_dir.resolve())


def test_accepts_an_absolute_path_inside(output_dir: Path) -> None:
    assert _confine_to_output_dir(str(output_dir / "x.png")) == (output_dir / "x.png").resolve()


async def _call(tool: str, output: str) -> tuple[dict[str, Any], AsyncMock]:
    run = AsyncMock(return_value={"status": "completed"})
    with (
        patch.object(tools_mod, "_run_generation_task", run),
        patch.object(tools_mod, "_resolve_and_validate_profile", return_value="default"),
    ):
        result = await getattr(tools_mod, tool)(prompt="a dog", out_file=output)
    return result, run


async def _call_out_file(tool: str, out_file: str) -> tuple[dict[str, Any], AsyncMock]:
    run = AsyncMock(return_value={"status": "completed"})
    with (
        patch.object(tools_mod, "_run_generation_task", run),
        patch.object(tools_mod, "_resolve_and_validate_profile", return_value="default"),
    ):
        result = await getattr(tools_mod, tool)(prompt="a dog", out_file=out_file)
    return result, run


_TOOLS = ["gflow_generate_image", "gflow_generate_video"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
@pytest.mark.parametrize("bad", ["../escaped/x.bin", "a/../../x.bin"])
async def test_tool_refuses_an_escaping_output_before_queueing(
    output_dir: Path, tool: str, bad: str
) -> None:
    result, run = await _call(tool, bad)
    assert result["status"] == "error"
    assert result["error"]["title"] == "Invalid Output"
    run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
async def test_tool_refuses_an_absolute_output_outside(
    output_dir: Path, tmp_path: Path, tool: str
) -> None:
    result, run = await _call(tool, str(tmp_path / "elsewhere" / "x.bin"))
    assert result["error"]["title"] == "Invalid Output"
    run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
@pytest.mark.parametrize("names_a_dir", ["", ".", "clips"])
async def test_tool_refuses_an_output_naming_a_directory_in_the_output_dir(
    output_dir: Path, tool: str, names_a_dir: str
) -> None:
    # Resolved against the output dir, not the process CWD (#1114's check used the CWD).
    (output_dir / "clips").mkdir()
    result, run = await _call(tool, names_a_dir)
    assert result["error"]["title"] == "Invalid Output"
    run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
@pytest.mark.parametrize("names_the_root", ["", "."])
async def test_tool_refuses_the_output_dir_itself_before_it_exists(
    output_dir: Path, tool: str, names_the_root: str
) -> None:
    # A fresh install has no output dir yet; is_dir() alone would let the clip take its name.
    output_dir.rmdir()
    result, run = await _call(tool, names_the_root)
    assert result["error"]["title"] == "Invalid Output"
    run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
async def test_tool_queues_a_relative_out_file_resolved_inside(output_dir: Path, tool: str) -> None:
    _, run = await _call_out_file(tool, "sub/custom.png")
    run.assert_called_once()
    assert run.call_args.kwargs["payload"]["output_file"] == str(
        (output_dir / "sub" / "custom.png").resolve()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
async def test_tool_rejects_retired_output_parameter(tool: str) -> None:
    result = await getattr(tools_mod, tool)(prompt="a dog", output="legacy.png")
    assert result["status"] == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _TOOLS)
@pytest.mark.parametrize("bad", ["../escaped/x.bin", "a/../../x.bin"])
async def test_tool_refuses_an_escaping_out_file_before_queueing(
    output_dir: Path, tool: str, bad: str
) -> None:
    result, run = await _call_out_file(tool, bad)
    assert result["status"] == "error"
    assert result["error"]["title"] == "Invalid Output"
    run.assert_not_called()


_MEDIA = "11111111-2222-3333-4444-555555555555"


@pytest.mark.asyncio
async def test_download_media_refuses_an_escaping_out_dir(output_dir: Path) -> None:
    with patch.object(tools_mod, "download_media", AsyncMock()) as download:
        result = await tools_mod.gflow_download_media(media_id=_MEDIA, out_dir="../escaped")
    assert result["error"]["title"] == "Invalid out_dir"
    download.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["gflow_upscale_image", "gflow_upscale_video"])
async def test_upscale_refuses_an_escaping_out_dir(output_dir: Path, tool: str) -> None:
    with (
        patch.object(tools_mod, "FlowApiClient") as client,
        patch.object(tools_mod, "_resolve_and_validate_profile", return_value="default"),
    ):
        result = await getattr(tools_mod, tool)(
            media_id=_MEDIA, project=_MEDIA, out_dir="../escaped"
        )
    assert result["error"]["title"] == "Invalid out_dir"
    client.assert_not_called()


@pytest.fixture()
def gflow_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "home"
    (home / "profile_alice").mkdir(parents=True)
    (tmp_path / "victim").mkdir()  # exists, so only the name check can refuse it
    monkeypatch.setenv("GFLOW_CLI_HOME", str(home))
    monkeypatch.delenv("GFLOW_CLI_PROFILE", raising=False)
    reset_settings()
    yield home
    reset_settings()


@pytest.mark.parametrize("name", ["alice/../../victim", "alice/../../victim/"])
def test_profile_resolver_refuses_a_name_that_walks_out_of_home(
    gflow_home: Path, name: str
) -> None:
    # The profile becomes Chrome's user-data-dir: Chrome would fill the target with its files.
    result = tools_mod._resolve_and_validate_profile(name)  # pyright: ignore[reportPrivateUsage]
    assert isinstance(result, dict)
    assert result["error"]["title"] == "Invalid Profile"


def test_profile_resolver_keeps_a_plain_name(gflow_home: Path) -> None:
    assert tools_mod._resolve_and_validate_profile("alice") == "alice"  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_a_tool_refuses_a_traversing_profile_before_launching(gflow_home: Path) -> None:
    with patch.object(tools_mod, "FlowApiClient") as client:
        result = await tools_mod.gflow_get_credits(profile="alice/../../victim")
    assert result["error"]["title"] == "Invalid Profile"
    client.assert_not_called()
