"""E2E: generating without a project on an account Flow serves from flow.google.com (#864).

labs.google's ``project.createProject`` answers 404 "Flow RPCs have been deprecated and
disabled" (or 401 for a session without a labs token), so every no-project path now
creates the project on flow.google.com. Each surface the change touches is driven here,
and each MCP twin separately — they share a service, not an adapter::

    GFLOW_CLI_E2E_PROFILE=<profile> uv run pytest -m e2e tests/e2e/test_project_create_e2e.py -v

Cost: $0 in credits. ``e2e_auth`` tests create empty projects (free) and, for video,
intercept the submit with ``route.abort()`` so Flow never sees it. ``e2e_image`` tests use
Flow's daily image quota. Every created project is titled ``gflow-e2e-864-*``; there is no
delete route, so remove them in Flow when convenient.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import structlog

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports import migrated_composer
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode
from gflow_cli.config import reset_settings
from gflow_cli.errors import TransportTimeoutError
from gflow_cli.mcp import tools as mcp_tools

pytestmark = pytest.mark.e2e

_UUID = migrated_composer.UUID_RE


def _gflow(env: dict[str, str], *args: str) -> dict[str, Any]:
    proc = subprocess.run(  # noqa: S603 - fixed argv, our own CLI
        [sys.executable, "-m", "gflow_cli.cli", *args, "--json"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
        check=False,
    )
    assert proc.returncode == 0, (
        f"exit {proc.returncode}\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )
    # `--json` pretty-prints one document; anything logged before it is not JSON.
    out = proc.stdout
    return json.loads(out[out.index("{") :])


def _title(kind: str) -> str:
    return f"gflow-e2e-864-{kind}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def auto_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    reset_settings()


async def _read_title(profile_dir: Path, project_id: str) -> str:
    async with FlowApiClient(profile_dir=profile_dir) as client:
        page = client._page  # noqa: SLF001 - the e2e reads the live page
        assert page is not None
        await page.goto(
            migrated_composer.MIGRATED_PROJECT_URL.format(project_id=project_id),
            wait_until="domcontentloaded",
        )
        field = page.locator(migrated_composer.PROJECT_TITLE_INPUT).first
        await field.wait_for(state="visible", timeout=45_000)
        return await field.input_value()


# --- project create / rename --------------------------------------------------------------


@pytest.mark.e2e_auth
async def test_cli_project_create_and_rename_land_on_flow(
    e2e_env: dict[str, str], e2e_profile_dir: Path, auto_host: None
) -> None:
    """The commands users run, then Flow's own header as the witness."""
    created = _gflow(e2e_env, "project", "create", "--name", _title("create"))
    project_id = created["project_id"]
    assert _UUID.fullmatch(project_id), created

    renamed = _title("renamed")
    _gflow(e2e_env, "project", "rename", project_id, renamed)

    assert await _read_title(e2e_profile_dir, project_id) == renamed


# --- image without a project ---------------------------------------------------------------


@pytest.mark.e2e_image
def test_cli_image_t2i_without_a_project_at_three_four(e2e_env: dict[str, str]) -> None:
    """Both halves of #864 on the image path: no `--project`, and the 3:4 radio that
    flow.google.com now renders (refused with exit 36 before)."""
    result = _gflow(
        e2e_env, "image", "t2i", "a teal origami crane on a wooden table", "--aspect", "3:4"
    )

    assert _UUID.fullmatch(result["project_id"]), result
    files = [Path(i["local_path"]) for i in result["images"]]
    assert files and all(p.exists() and p.stat().st_size > 10_000 for p in files), result
    dims = result["images"][0]["dimensions"]
    assert abs(dims["width"] / dims["height"] - 3 / 4) < 0.02, dims


@pytest.mark.e2e_image
async def test_mcp_image_without_a_project(e2e_profile_dir: Path, auto_host: None) -> None:
    del e2e_profile_dir  # fixture selects the real authenticated gflow home
    result = await mcp_tools.gflow_generate_image(
        prompt="a teal origami crane on a wooden table",
        profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
        wait=True,
    )

    assert result["status"] == "completed", result
    files = [Path(p) for p in result["files"]]
    assert files and all(p.exists() and p.stat().st_size > 10_000 for p in files)


# --- video without a project: up to the submit, never billed -------------------------------


def _abort_video_submits(monkeypatch: pytest.MonkeyPatch, captured: list[str]) -> None:
    """Every FlowApiClient in this process aborts a video submit before it leaves Chrome."""
    original = FlowApiClient.__aenter__

    async def enter(self: FlowApiClient) -> FlowApiClient:
        client = await original(self)

        async def block(route: Any, request: Any) -> None:
            body = getattr(request, "post_data", None) or ""
            rpcid = migrated_composer._rpcid(str(request.url)) or migrated_composer._body_rpcid(  # noqa: SLF001
                body
            )
            if rpcid in migrated_composer.SUBMIT_RPCS:
                captured.append(rpcid)
                await route.abort()
                return
            await route.continue_()

        assert client._context is not None  # noqa: SLF001
        await client._context.route("**/batchexecute*", block)  # noqa: SLF001
        return client

    monkeypatch.setattr(FlowApiClient, "__aenter__", enter)


def _created_a_project(capture: structlog.testing.LogCapture) -> bool:
    events = {str(e.get("event")) for e in capture.entries}
    return "migrated.project_created" in events


@pytest.mark.e2e_auth
async def test_video_without_a_project_reaches_the_submit(
    e2e_profile_dir: Path,
    auto_host: None,
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """The client path every CLI video command takes (`FlowApiClient.generate_video`)."""
    captured: list[str] = []
    _abort_video_submits(monkeypatch, captured)
    req = GenerateVideoRequest(
        prompt="a teal origami crane", mode=Mode.T2V, aspect=Aspect.LANDSCAPE
    )

    async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
        with pytest.raises(TransportTimeoutError):
            await client.generate_video(req=req, poll_timeout_s=20.0, download=False)

    assert captured, "no video submit was attempted"
    assert _created_a_project(install_log_capture)


@pytest.mark.e2e_auth
async def test_mcp_video_without_a_project_reaches_the_submit(
    e2e_profile_dir: Path,
    auto_host: None,
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """The MCP twin: queue payload -> worker -> client, with `project_name` honoured."""
    del e2e_profile_dir
    captured: list[str] = []
    _abort_video_submits(monkeypatch, captured)

    result = await mcp_tools.gflow_generate_video(
        prompt="a teal origami crane",
        mode="t2v",
        aspect="16:9",
        profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
        project_name=_title("mcp-video"),
        wait=True,
    )

    assert result["status"] != "completed", result
    assert captured, f"no video submit was attempted: {result}"
    assert _created_a_project(install_log_capture)
