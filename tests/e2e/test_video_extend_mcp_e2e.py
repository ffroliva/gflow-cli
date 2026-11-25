"""E2E: the MCP twin ``gflow_extend_video`` through the worker, on flow.google.com.

The CLI's extend tests (``test_video_extend_migrated_e2e.py``) do not cover this surface:
the tool enqueues a payload, the in-process worker decodes it and drives the same
transport, so a payload key the worker never reads or a codec that drops ``max_credits``
type-checks and silently no-ops. Only a run shows it.

* ``e2e_auth`` ($0 on any account): ``max_credits=0`` is refused with exit 45 and
  ``charged: no``, and the composer's submit is never reached, so no ``fZytfe`` is sent.
* ``e2e_video`` (SPENDS VEO CREDITS, opt in with ``GFLOW_CLI_E2E_RUN_VIDEO=1``): one real
  extend through the worker, whose result lists the downloaded file in ``files``.

Env: ``GFLOW_CLI_E2E_PROFILE`` (profile served flow.google.com), ``GFLOW_CLI_E2E_PROJECT``,
``GFLOW_CLI_E2E_EXTEND_MEDIA`` (a finished video in that project).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.config import reset_settings

pytestmark = pytest.mark.e2e

_PROMPT = "the camera keeps drifting forward, same light"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} must be set (see module doc)")
    return value


def _isolate(e2e_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """The real authenticated home, with this test's own catalog and output directory."""
    for key in ("GFLOW_CLI_DB_PATH", "GFLOW_CLI_OUTPUT_DIR"):
        monkeypatch.setenv(key, e2e_env[key])
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    monkeypatch.delenv("GFLOW_CLI_MAX_CREDITS", raising=False)
    reset_settings()


@pytest.mark.asyncio
@pytest.mark.e2e_auth
async def test_mcp_extend_with_a_zero_cap_is_refused_before_any_submit(
    e2e_env: dict[str, str], e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """$0: Flow quotes every extend above 0 credits, so the cap refuses before the click."""
    del e2e_profile_dir  # selects the real authenticated gflow home
    from gflow_cli.mcp import video_extend_tools

    project, media = _env("GFLOW_CLI_E2E_PROJECT"), _env("GFLOW_CLI_E2E_EXTEND_MEDIA")
    _isolate(e2e_env, monkeypatch)
    submits: list[Any] = []

    async def never_submit(_self: Any, _page: Any, **kwargs: Any) -> Any:
        submits.append(kwargs)
        raise AssertionError("the zero cap must refuse before the submit is clicked")

    monkeypatch.setattr(MigratedComposer, "submit_and_observe", never_submit)
    result = await video_extend_tools.gflow_extend_video(
        source=media,
        prompt=_PROMPT,
        project=project,
        max_credits=0,
        profile=e2e_env["GFLOW_CLI_PROFILE"],
        wait=True,
    )

    assert result["status"] == "failed", result
    assert result["error"]["exit_code"] == 45, result
    assert result["error"]["charged"] == "no", result
    assert submits == []


@pytest.mark.asyncio
@pytest.mark.e2e_video
async def test_mcp_one_paid_extend_returns_the_downloaded_file(
    e2e_env: dict[str, str], e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPENDS VEO CREDITS. One segment through the worker; ``files`` names the mp4."""
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO") != "1":
        pytest.skip("spends Veo credits — set GFLOW_CLI_E2E_RUN_VIDEO=1 (owner consent)")
    del e2e_profile_dir
    from gflow_cli.mcp import video_extend_tools

    project, media = _env("GFLOW_CLI_E2E_PROJECT"), _env("GFLOW_CLI_E2E_EXTEND_MEDIA")
    _isolate(e2e_env, monkeypatch)
    result = await video_extend_tools.gflow_extend_video(
        source=media,
        prompt=_PROMPT,
        project=project,
        duration=5,
        profile=e2e_env["GFLOW_CLI_PROFILE"],
        wait=True,
    )

    assert result["status"] == "completed", result
    files = result.get("files") or []
    assert len(files) == 1, result
    data = Path(files[0]).read_bytes()
    assert data[4:8] == b"ftyp", files[0]
