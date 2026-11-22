"""#941 part 2: a signed clip URL that answers an HTTP error is a recoverable download.

Before: ``WireFormatError`` (exit 7) and the billed clip stayed ``pending`` in the catalog.
After: ``MediaDownloadError`` (exit 6, not retryable) naming the clip, the catalog marks it
generated, and ``gflow data download`` fetches it for free.

**How the error is provoked.** ``page.route`` does not see ``APIRequestContext`` requests,
so a ``sitecustomize`` on the child's ``PYTHONPATH`` makes ``APIRequestContext.get`` answer
403 for ``flow-content.google/video/`` URLs, the way an expired link does. Everything else,
the generation, the recorder and the CLI's exit mapping, is the real path. The recovery
run has no hook.

**Cost:** one Veo lite clip. Opt in with ``GFLOW_CLI_E2E_RUN_VIDEO=1``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.e2e._redacted_env import RedactedEnv

from .test_image_uuid_ref_e2e import _run_gflow

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_video]

_HOOK = """
from playwright.async_api._generated import APIRequestContext

_real_get = APIRequestContext.get


class _Expired:
    status = 403
    headers: dict[str, str] = {}

    def __init__(self, url: str) -> None:
        self.url = url

    async def body(self) -> bytes:
        return b""

    async def text(self) -> str:
        return ""


async def _get(self, url, *args, **kwargs):
    if str(url).startswith("https://flow-content.google/video/"):
        return _Expired(str(url))
    return await _real_get(self, url, *args, **kwargs)


APIRequestContext.get = _get
"""


def test_an_expired_clip_link_exits_6_marks_the_clip_and_is_recovered(
    e2e_env: RedactedEnv, tmp_path: Path
) -> None:
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to spend one Veo lite clip on this e2e")
    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(_HOOK, encoding="utf-8")
    hooked = e2e_env | {"PYTHONPATH": os.pathsep.join([str(hook), e2e_env.get("PYTHONPATH", "")])}

    run = _run_gflow(
        [
            "video",
            "t2v",
            "a paper boat on a calm pond, static camera",
            "--model",
            "veo-lite",
            "--json",
        ],
        hooked,
    )

    assert run.returncode == 6, (run.returncode, run.stderr[-1500:])
    error = json.loads(run.stdout[run.stdout.index("{") :])["error"]
    assert error["class"] == "MediaDownloadError", error
    assert error["retryable"] is False, error
    assert "403" in error["detail"], error

    listed = _run_gflow(["data", "list", "videos", "--json"], e2e_env)
    rows = [json.loads(line) for line in listed.stdout.splitlines() if line.startswith("{")]
    assert len(rows) == 1, (rows, listed.stderr[-600:])
    media_id = rows[0]["media_id"]
    assert rows[0]["status"] != "pending", rows[0]

    out = tmp_path / "recovered"
    recovered = _run_gflow(["data", "download", media_id, "--out", str(out), "--json"], e2e_env)
    assert recovered.returncode == 0, recovered.stderr[-1500:]
    clips = list(out.rglob("*.mp4"))
    assert len(clips) == 1 and clips[0].read_bytes()[4:8] == b"ftyp", clips
