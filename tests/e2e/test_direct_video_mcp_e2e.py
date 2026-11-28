"""Live E2E for the MCP twins of the headless direct transport (Task 19).

The MCP tools run in this process against one real profile, headless, over
``GFLOW_CLI_TRANSPORT=direct_http`` with ``browser_visibility="auto"``:

* ``gflow_generate_image`` at a measured shape (zero credits, daily quota);
* ``gflow_generate_video`` at the measured t2v key (Omni 1.2 Flash, 4 s, 360p), once with
  ``wait=true`` and once queued (``wait=false``) and read back through the task store;
* ``gflow_upscale_video`` at 1080p on the ``wait=true`` clip, a 16:9 4 s Omni 1.2 Flash clip,
  the source shape the direct upscale allowlists.

Each asserts ``route == "direct"``, ``visibility == "headless"`` and a written file. The image
run needs only the profile; the video runs spend Veo credits:

    GFLOW_CLI_E2E_PROFILE=<profile> uv run pytest -m e2e_image -v \\
        tests/e2e/test_direct_video_mcp_e2e.py
    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_RUN_VIDEO=1 \\
        uv run pytest -m e2e_video -v tests/e2e/test_direct_video_mcp_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from gflow_cli.config import get_settings, reset_settings
from gflow_cli.mcp import tools as mcp_tools

if TYPE_CHECKING:
    from tests.e2e._redacted_env import RedactedEnv

pytestmark = [pytest.mark.e2e]

_T2V_KEY: dict[str, Any] = {
    "model": "omni_1_2_flash",
    "duration": 4,
    "resolution": "360p",
    "aspect": "16:9",
}
_QUEUED_TIMEOUT_S = 900.0


@pytest.fixture()
def mcp_env(e2e_env: RedactedEnv, e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """The MCP tools read this run's catalog and output dir; returns the profile name."""
    del e2e_profile_dir  # selects the real authenticated gflow home
    for key in ("GFLOW_CLI_DB_PATH", "GFLOW_CLI_OUTPUT_DIR"):
        monkeypatch.setenv(key, e2e_env[key])
    monkeypatch.setenv("GFLOW_CLI_TRANSPORT", "direct_http")
    monkeypatch.delenv("GFLOW_CLI_BROWSER_VISIBILITY", raising=False)
    monkeypatch.delenv("GFLOW_CLI_MAX_CREDITS", raising=False)
    reset_settings()
    return e2e_env["GFLOW_CLI_PROFILE"]


def _video_opt_in() -> None:
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to spend Veo credits on the MCP video e2e")


def _assert_direct_headless(result: dict[str, Any]) -> None:
    assert result["status"] in ("completed", "ok"), result
    assert result["route"] == "direct", result
    assert result["visibility"] == "headless", result


def _written(path: str) -> Path:
    out = Path(path)
    assert out.is_file(), f"{out} was not written"
    assert out.stat().st_size > 0, f"{out} is empty"
    return out


@pytest.mark.e2e_image
def test_mcp_image_auto_goes_direct_headless(mcp_env: str) -> None:
    result = asyncio.run(
        mcp_tools.gflow_generate_image(
            prompt="a paper boat on a pond",
            aspect="16:9",
            profile=mcp_env,
            browser_visibility="auto",
            wait=True,
        )
    )
    _assert_direct_headless(result)
    assert result["files"], result
    _written(result["files"][0])


async def _queued_until_done(profile: str, task_id: str) -> dict[str, Any]:
    """Read a ``wait=false`` job back the way ``tasks/get`` does, until it settles."""
    db_path = get_settings().resolved_db_path()
    deadline = time.monotonic() + _QUEUED_TIMEOUT_S
    while True:
        payload = mcp_tools.task_payload(profile, task_id, db_path)
        if payload.get("task_status") not in ("pending", "processing"):
            return payload
        assert time.monotonic() < deadline, f"queued job {task_id} did not settle"
        await asyncio.sleep(5)


@pytest.mark.e2e_video
def test_mcp_video_direct_wait_and_queued_then_1080p_upscale(mcp_env: str) -> None:
    _video_opt_in()

    async def run() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        waited = await mcp_tools.gflow_generate_video(
            prompt="a paper boat drifting on a pond",
            profile=mcp_env,
            browser_visibility="auto",
            wait=True,
            **_T2V_KEY,
        )
        handle = await mcp_tools.gflow_generate_video(
            prompt="a lantern floating on a lake at dusk",
            profile=mcp_env,
            browser_visibility="auto",
            wait=False,
            **_T2V_KEY,
        )
        assert handle["status"] == "pending", handle
        queued = await _queued_until_done(mcp_env, handle["task_id"])
        upscaled = await mcp_tools.gflow_upscale_video(
            media_id=waited["flow_media_id"],
            scale="1080p",
            project=waited["flow_project_id"],
            profile=mcp_env,
            browser_visibility="auto",
        )
        return waited, queued, upscaled

    waited, queued, upscaled = asyncio.run(run())

    for clip in (waited, queued):
        _assert_direct_headless(clip)
        assert clip["files"], clip
        assert _written(clip["files"][0]).read_bytes()[4:8] == b"ftyp"
    _assert_direct_headless(upscaled)
    assert _written(upscaled["path"]).read_bytes()[4:8] == b"ftyp"
