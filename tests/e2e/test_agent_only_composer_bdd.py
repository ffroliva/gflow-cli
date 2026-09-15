"""E2E for #799: gflow drives Flow's agent-only composer (text-to-image, text-to-video).

Binds ``tests/features/agent_only_composer_live.feature`` — see ``docs/E2E_TESTING.md``
§ BDD-bound e2e. Needs a profile whose project is served the agent-only composer::

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_PROJECT=<project-uuid> \\
        uv run pytest -m e2e_image tests/e2e/test_agent_only_composer_bdd.py -v
    GFLOW_CLI_E2E_RUN_VIDEO=1 ... -m e2e_video ...    # one 4 s clip, ~7 credits measured

Every scenario skips when the project renders the classic composer — that account is covered
by ``test_migrated_host_e2e.py``. The CLI scenarios run the real ``gflow`` in a subprocess;
the MCP scenario calls the tool with ``wait=True`` so the queued worker path runs too.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.mcp import tools as mcp_tools

scenarios("../features/agent_only_composer_live.feature")

_PROJECT_ENV = "GFLOW_CLI_E2E_PROJECT"
_PROMPT = "a small blue paper boat on a calm pond, soft morning light"


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


def _gflow(args: list[str], env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    child = {k: v for k, v in env.items() if k != "GFLOW_CLI_HOME"}  # the real profile home
    return subprocess.run(
        [sys.executable, "-m", "gflow_cli", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env={**child, "GFLOW_CLI_LOG_FORMAT": "json"},
    )


def _events(stderr: str) -> list[str]:
    names: list[str] = []
    for line in stderr.splitlines():
        if line.strip().startswith("{"):
            try:
                names.append(str(json.loads(line).get("event", "")))
            except json.JSONDecodeError:
                continue
    return names


def _image_size(path: Path) -> tuple[int, int]:
    """(width, height) of a JPEG or PNG, read from its header."""
    data = path.read_bytes()
    if data.startswith(b"\x89PNG"):
        return struct.unpack(">II", data[16:24])
    i = 2
    while i < len(data):
        marker, length = data[i + 1], struct.unpack(">H", data[i + 2 : i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    raise AssertionError(f"{path.name}: no JPEG frame header")


async def _composer_kind(profile_dir: Path, project: str) -> str:
    transport = UiAutomationTransport()
    try:
        await transport.setup(profile_dir)
        page = transport._page  # noqa: SLF001 - the e2e reads the live page
        assert page is not None
        return await MigratedComposer().ensure_editor(page, project, timeout_s=45.0)
    finally:
        await transport.teardown()


# --------------------------------------------------------------------------- given


@given("a profile whose Flow project serves the agent-only composer")
def _agent_only_project(world: dict[str, Any], e2e_profile_dir: Path) -> None:
    project = os.environ.get(_PROJECT_ENV, "").strip()
    if not project:
        pytest.skip(f"{_PROJECT_ENV} must name an existing Flow project id (see module doc)")
    if asyncio.run(_composer_kind(e2e_profile_dir, project)) != "agent_only":
        pytest.skip("this project renders the classic composer; #799 needs an agent-only account")
    world["project"] = project


# ---------------------------------------------------------------------------- when


@when("`gflow image t2i` runs with aspect 3:4 and count 2")
def _cli_t2i(world: dict[str, Any], e2e_env: dict[str, str], tmp_path: Path) -> None:
    out = tmp_path / "out"
    world["proc"] = _gflow(
        [
            "image",
            "t2i",
            _PROMPT,
            "--project",
            world["project"],
            "--aspect",
            "3:4",
            "-n",
            "2",
            "--out",
            str(out),
            "--json",
        ],
        e2e_env,
        timeout=420,
    )


@when("the MCP tool gflow_generate_image runs with aspect 1:1 and count 1")
def _mcp_t2i(world: dict[str, Any]) -> None:
    world["mcp"] = asyncio.run(
        mcp_tools.gflow_generate_image(
            prompt=_PROMPT,
            aspect="1:1",
            count=1,
            profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
            project=world["project"],
            wait=True,
        )
    )


@when("`gflow video t2v` runs for 4 seconds at 9:16")
def _cli_t2v(world: dict[str, Any], e2e_env: dict[str, str], tmp_path: Path) -> None:
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to spend credits on the agent-only t2v e2e")
    out = tmp_path / "out"
    world["proc"] = _gflow(
        [
            "video",
            "t2v",
            _PROMPT,
            "--project",
            world["project"],
            "--duration",
            "4",
            "--aspect",
            "9:16",
            "--out-dir",
            str(out),
            "--json",
        ],
        e2e_env,
        timeout=900,
    )


# ---------------------------------------------------------------------------- then


def _ok(world: dict[str, Any]) -> dict[str, Any]:
    proc: subprocess.CompletedProcess[str] = world["proc"]
    assert proc.returncode == 0, f"exit {proc.returncode}; stderr tail: {proc.stderr[-800:]!r}"
    assert "migrated.agent_only_composer" in _events(proc.stderr)
    return json.loads(proc.stdout)


@then("it exits 0 with 2 images in a 3:4 shape")
def _two_three_four(world: dict[str, Any]) -> None:
    data = _ok(world)
    paths = [Path(img["local_path"]) for img in data["images"]]
    assert len(paths) == 2 and len({img["media_name"] for img in data["images"]}) == 2
    for path in paths:
        width, height = _image_size(path)
        assert abs(width / height - 3 / 4) < 0.02, f"{path.name}: {width}x{height}"


@then("the run restored the Agent-settings defaults")
def _restored(world: dict[str, Any]) -> None:
    events = _events(world["proc"].stderr)
    assert "migrated.agent_only.defaults_restored" in events
    assert "migrated.agent_only.defaults_restore_failed" not in events


@then("the tool reports completed with 1 image file")
def _mcp_completed(world: dict[str, Any]) -> None:
    result = world["mcp"]
    assert result["status"] == "completed", result
    files = [Path(p) for p in result["files"]]
    assert len(files) == 1 and files[0].stat().st_size > 10_000


@then("it exits 0 with one portrait MP4")
def _one_mp4(world: dict[str, Any]) -> None:
    data = _ok(world)
    assert data["succeeded"] is True
    clip = Path(data["local_path"])
    assert clip.read_bytes()[4:8] == b"ftyp", clip
