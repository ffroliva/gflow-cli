"""#1375 live: a generation on a model Flow is retiring still runs, and says so, on both doors.

Scenarios 3, 4 and 5 against real Flow. The CLI logs ``model.retiring`` on stderr while
``--json`` stdout stays one JSON document; the MCP ``wait=true`` reply carries a ``warnings``
entry. Neither refuses: the clip lands.

Bills two Omni 1.1 Flash 4 s clips. Opt in with ``GFLOW_CLI_E2E_RUN_VIDEO=1``. From Flow's
retirement day the module skips (``until_retired``), since the model is then gone.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gflow_cli.api.model_catalog import retires_on
from gflow_cli.api.video import VideoModel
from gflow_cli.mcp import tools as mcp_tools
from tests.e2e.conftest import until_retired

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_video, until_retired(VideoModel.OMNI_FLASH)]

_PROMPT = "a paper boat drifting across a still pond, slow push in"
_WHEN = str(retires_on(VideoModel.OMNI_FLASH))
_WARNING = {"type": "model_retiring", "model": "omni_flash", "retires_on": _WHEN}


def _require_video() -> None:
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to run (bills one Omni 1.1 4 s clip)")


def _project_args() -> list[str]:
    project = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    return ["--project", project] if project else []


def test_e2e_cli_warns_on_stderr_and_keeps_json_stdout_clean(
    e2e_env: dict[str, str], tmp_path: Path
) -> None:
    _require_video()
    env = e2e_env.copy()
    env.pop("GFLOW_CLI_HOME", None)  # the real home holds the live Chrome session

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "gflow_cli",
            "video",
            "t2v",
            _PROMPT,
            "--model",
            "omni-flash",
            "--duration",
            "4",
            "--aspect",
            "16:9",
            *_project_args(),
            "--out-dir",
            str(tmp_path / "out"),
            "--json",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )

    assert proc.returncode == 0, f"exit {proc.returncode}; stderr tail: {proc.stderr[-600:]!r}"
    data = json.loads(proc.stdout)  # one JSON document: the warning did not reach stdout
    assert data["succeeded"] is True
    assert Path(data["local_path"]).read_bytes()[4:8] == b"ftyp"
    warning = [line for line in proc.stderr.splitlines() if "model.retiring" in line]
    assert warning, f"no model.retiring on stderr; tail: {proc.stderr[-600:]!r}"
    assert _WHEN in warning[0]


@pytest.mark.asyncio
async def test_e2e_mcp_reply_carries_the_retirement_warning(e2e_profile_dir: Path) -> None:
    _require_video()
    del e2e_profile_dir  # resolves and checks the profile; the tool takes it by name

    result = await mcp_tools.gflow_generate_video(
        prompt=_PROMPT,
        mode="t2v",
        aspect="16:9",
        model="omni_flash",
        duration=4,
        profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
        project=os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip() or None,
        wait=True,
    )

    assert result["status"] == "completed", result
    assert result["warnings"] == [_WARNING], result
    assert result["files"], result
