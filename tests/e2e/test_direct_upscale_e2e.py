"""Live E2E for the direct 1080p upscale (headless direct transport, Task 15).

One profile, headless, over ``GFLOW_CLI_TRANSPORT=direct_http``. The source clip is a
billed t2v at the measured upscale source (Omni 1.2 Flash, 4 s, 360p, 16:9:
``macaron_cookie_t2v_4s_360p``), recorded in this test's own catalog; then
``gflow video upscale --scale 1080p`` on it. Asserts the route was direct (the transport's
``direct.upscale`` log event), a 1920x1080 mp4, and a balance the upscale left unchanged
(a 1080p upscale measured 0 credits; spike 2026-11-27, "Direct video works end to end").
The source clip spends Veo credits; opt in with:

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_RUN_VIDEO=1 \\
        uv run pytest -m e2e_video -v tests/e2e/test_direct_upscale_e2e.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from gflow_cli.api.transports.migrated_video_upscale import mp4_track_dimensions

if TYPE_CHECKING:
    from pathlib import Path

    from tests.e2e._redacted_env import RedactedEnv

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_video]

_SOURCE_KEY = ("--model", "omni-1.2-flash", "--duration", "4", "--resolution", "360p")


@pytest.fixture(autouse=True)
def _opt_in() -> None:
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to spend Veo credits on the source clip")


def _gflow(env: RedactedEnv, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "gflow_cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env
        | {
            "GFLOW_CLI_TRANSPORT": "direct_http",
            "GFLOW_CLI_BROWSER_VISIBILITY": "auto",
            "GFLOW_CLI_LOG_LEVEL": "INFO",
        },
    )


def _balance(env: RedactedEnv) -> int:
    proc = _gflow(env, "credits", "user", "--json")
    assert proc.returncode == 0, f"gflow credits failed (exit {proc.returncode})"
    return int(json.loads(proc.stdout)["credits"])


def _catalogued_clip(env: RedactedEnv) -> tuple[str, str]:
    """The source clip's media id and project, from this test's catalog."""
    with sqlite3.connect(env["GFLOW_CLI_DB_PATH"]) as conn:
        row = conn.execute(
            "SELECT flow_media_id, flow_project_id FROM assets WHERE kind = 'video' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None, "the source clip was not recorded in the catalog"
    return str(row[0]), str(row[1])


def test_1080p_upscale_goes_direct_at_no_cost(e2e_env: RedactedEnv, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    proc = _gflow(
        e2e_env,
        *("video", "t2v", "a paper boat drifting on a pond"),
        *_SOURCE_KEY,
        *("--aspect", "16:9", "--out-file", str(source)),
    )
    assert proc.returncode == 0, f"source clip failed (exit {proc.returncode})"
    media_id, project_id = _catalogued_clip(e2e_env)

    before = _balance(e2e_env)
    out_dir = tmp_path / "upscaled"
    proc = _gflow(
        e2e_env,
        *("video", "upscale", media_id, "--scale", "1080p"),
        *("--project", project_id, "--out", str(out_dir)),
    )
    assert proc.returncode == 0, f"gflow video upscale failed (exit {proc.returncode})"
    assert "direct.upscale" in proc.stderr, "the upscale did not take the direct route"

    (clip,) = out_dir.rglob(f"{media_id}_1080p.mp4")
    data = clip.read_bytes()
    assert data[4:8] == b"ftyp", f"{clip} is not an mp4"
    assert mp4_track_dimensions(data) == (1920, 1080)
    assert _balance(e2e_env) == before
