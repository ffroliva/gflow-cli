"""Live E2E for direct text-to-video and image-to-video (headless direct transport, Task 14).

Two billed runs on one profile, headless, over ``GFLOW_CLI_TRANSPORT=direct_http``:

* t2v at an explicit measured key (Omni 1.2 Flash, 4 s, 360p, 9:16: ``YhhmEf``), a
  360x640 mp4, 4 credits measured;
* the default ``gflow video i2v`` shape (no ``--model``: Veo 3.1 Lite, 9:16; ``maseQ`` then
  ``eb1hJf``), a 720x1280 mp4, 10 credits measured.

Each asserts exit 0, route ``direct`` and visibility ``headless`` in the ``--json`` result,
the mp4's dimensions, and a balance drop equal to the key's measured cost (spike 2026-11-27,
"The default video shape over direct HTTP"). Spends Veo credits; opt in with:

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_RUN_VIDEO=1 \\
        uv run pytest -m e2e_video -v tests/e2e/test_direct_video_e2e.py
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import zlib
from typing import TYPE_CHECKING

import pytest

from gflow_cli.api.transports.migrated_video_upscale import mp4_track_dimensions

if TYPE_CHECKING:
    from pathlib import Path

    from tests.e2e._redacted_env import RedactedEnv

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_video]

_T2V_KEY = ("--model", "omni-1.2-flash", "--duration", "4", "--resolution", "360p")
_T2V_COST, _I2V_COST = 4, 10  # macaron_cookie_t2v_4s_360p, veo_3_1_i2v_lite (one account)


@pytest.fixture(autouse=True)
def _opt_in() -> None:
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to spend Veo credits on the direct video e2e")


def _gflow(env: RedactedEnv, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "gflow_cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env | {"GFLOW_CLI_TRANSPORT": "direct_http", "GFLOW_CLI_BROWSER_VISIBILITY": "auto"},
    )


def _balance(env: RedactedEnv) -> int:
    proc = _gflow(env, "credits", "user", "--json")
    assert proc.returncode == 0, f"gflow credits failed (exit {proc.returncode})"
    return int(json.loads(proc.stdout)["credits"])


def _start_frame(path: Path, width: int = 1200, height: int = 630) -> Path:
    """A plain 1200x630 PNG: the frame the spike's crop box was measured on."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + bytes((40, 90, 160)) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows))
    path.write_bytes(png + chunk(b"IEND", b""))
    return path


def _assert_direct_clip(
    proc: subprocess.CompletedProcess[str], out: Path, dims: tuple[int, int]
) -> None:
    assert proc.returncode == 0, f"gflow video failed (exit {proc.returncode})"
    payload = json.loads(proc.stdout)  # --json keeps stdout to the one document
    assert payload.get("route") == "direct"
    assert payload.get("visibility") == "headless"
    data = out.read_bytes()
    assert data[4:8] == b"ftyp", f"{out} is not an mp4"
    assert mp4_track_dimensions(data) == dims


def test_t2v_at_a_measured_key_goes_direct_headless(e2e_env: RedactedEnv, tmp_path: Path) -> None:
    before = _balance(e2e_env)
    out = tmp_path / "t2v.mp4"
    proc = _gflow(
        e2e_env,
        *("video", "t2v", "a paper boat drifting on a pond"),
        *_T2V_KEY,
        *("--aspect", "9:16", "--out-file", str(out), "--json"),
    )
    _assert_direct_clip(proc, out, (360, 640))
    assert before - _balance(e2e_env) == _T2V_COST


def test_default_i2v_goes_direct_headless(e2e_env: RedactedEnv, tmp_path: Path) -> None:
    before = _balance(e2e_env)
    out = tmp_path / "i2v.mp4"
    start = _start_frame(tmp_path / "start.png")
    proc = _gflow(
        e2e_env,
        *("video", "i2v", "the sky slowly brightens"),
        *("--initial-frame", str(start), "--out-file", str(out), "--json"),
    )
    _assert_direct_clip(proc, out, (720, 1280))
    assert before - _balance(e2e_env) == _I2V_COST
