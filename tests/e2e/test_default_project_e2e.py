"""#1039: with only GFLOW_CLI_PROJECT set, both doors generate into that project.

Needs GFLOW_CLI_E2E_PROFILE and GFLOW_CLI_E2E_PROJECT (an existing project of that
profile). Costs one image of daily quota per test, 0 credits, and creates no project.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from gflow_cli.config import reset_settings
from gflow_cli.mcp import tools as mcp_tools
from tests.e2e._redacted_env import RedactedEnv

from .test_image_uuid_ref_e2e import _run_gflow

pytestmark = pytest.mark.e2e

_PROMPT = "a single green apple on a white plate, soft daylight"


def _pinned() -> str:
    pid = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if not pid:
        pytest.skip("GFLOW_CLI_E2E_PROJECT must name an existing Flow project id")
    return pid


@pytest.mark.e2e_image
def test_e2e_cli_t2i_generates_into_the_pinned_project(e2e_env: RedactedEnv) -> None:
    pinned = _pinned()
    env = e2e_env | {"GFLOW_CLI_PROJECT": pinned}
    proc = _run_gflow(["image", "t2i", _PROMPT, "--aspect", "1:1", "--json"], env)

    assert proc.returncode == 0, proc.stderr[-800:]
    assert f"Using the default project {pinned}" in proc.stderr
    listed = _run_gflow(["data", "list", "images", "--json"], env)
    rows = [json.loads(line) for line in listed.stdout.splitlines() if line.startswith("{")]
    assert rows, listed.stderr[-400:]
    assert {r["project_id"] for r in rows} == {pinned}, rows


@pytest.mark.e2e_image
def test_e2e_mcp_image_generates_into_the_pinned_project(
    e2e_env: RedactedEnv, e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del e2e_profile_dir  # selects the real authenticated gflow home
    pinned = _pinned()
    for key in ("GFLOW_CLI_DB_PATH", "GFLOW_CLI_OUTPUT_DIR"):
        monkeypatch.setenv(key, e2e_env[key])
    monkeypatch.setenv("GFLOW_CLI_PROJECT", pinned)
    reset_settings()

    result = asyncio.run(
        mcp_tools.gflow_generate_image(
            prompt=_PROMPT, aspect="1:1", profile=e2e_env["GFLOW_CLI_PROFILE"], wait=True
        )
    )

    assert result["status"] == "completed", result
    assert result["params"]["project"] == pinned
    assert result.get("flow_project_id", pinned) == pinned, result
