# SPDX-License-Identifier: MIT
"""The MCP twin of ``--browser-visibility``: parameter, queued route, and reported path.

SCENARIO.md rows 31-33 of the headless direct transport plan: the generation tools take
``browser_visibility`` like the CLI takes the option, the queued payload carries it to the
worker's client, a credit-capped video routes to the UI as on the CLI, the result says which
path ran, and a final refusal reaches the agent as a not-retryable, not-charged problem.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.submit_policy import BrowserVisibility
from gflow_cli.api.video import VideoResult, VideoStatus
from gflow_cli.config import Settings
from gflow_cli.data.store import DataStore
from tests.mcp.test_tools_wired import _FakeFlowApiClient, _FakeImage

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def temp_db(tmp_path: Path) -> Iterator[DataStore]:
    store = DataStore.open(tmp_path / "gflow_test.db")
    store.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('default', '/profiles/default', '2026-11-28T00:00:00Z')"
    )
    store.conn.commit()
    try:
        yield store
    finally:
        store.close()


@pytest.fixture()
def wired(
    temp_db: DataStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[MagicMock]:
    """The MCP tools run through the real worker; only the browser client is faked."""
    # Both spellings: Linux env names are case-sensitive, and conftest sets the lowercase ones.
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    for var in ("GFLOW_CLI_TRANSPORT", "GFLOW_CLI_BROWSER_VISIBILITY", "GFLOW_CLI_MAX_CREDITS"):
        monkeypatch.delenv(var, raising=False)
    worker_settings = Settings(output_dir=tmp_path / "out", flow_host="auto")
    client_cls = MagicMock(return_value=_FakeFlowApiClient())
    with (
        patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
        patch("gflow_cli.worker.daemon.FlowApiClient", client_cls),
        patch(
            "gflow_cli.mcp.tools.get_settings",
            return_value=MagicMock(
                resolved_db_path=lambda: temp_db.path,
                profile_subdir=lambda _: tmp_path / "profile_default",
                output_dir=tmp_path / "out",
            ),
        ),
        patch("gflow_cli.worker.daemon.get_settings", return_value=worker_settings),
        patch(
            "gflow_cli.api.transports.direct_http._transport.refuse_empty_balance",
            new=AsyncMock(return_value=None),
        ),
    ):
        yield client_cls


def _client_kwargs(client_cls: MagicMock) -> dict[str, Any]:
    return client_cls.call_args.kwargs


# ---------------------------------------------------------------------------
# Row 31 — the parameter and its queued round trip
# ---------------------------------------------------------------------------


async def test_unknown_browser_visibility_is_the_bad_parameter_envelope() -> None:
    """Rejected before any job is queued, with the same 400 envelope as ``ui_mode``."""
    from gflow_cli.mcp.tools import (
        gflow_generate_image,
        gflow_generate_video,
        gflow_upscale_video,
    )
    from gflow_cli.mcp.video_extend_tools import gflow_extend_video

    with patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"):
        results = [
            await gflow_generate_image(prompt="p", browser_visibility="visible"),
            await gflow_generate_video(prompt="p", browser_visibility="visible"),
            await gflow_extend_video(
                prompt="p",
                source="7c9e6679-7425-40de-944b-e07fc1f90ae7",
                project="proj-1",
                browser_visibility="visible",
            ),
            await gflow_upscale_video(
                media_id="7c9e6679-7425-40de-944b-e07fc1f90ae7", browser_visibility="visible"
            ),
        ]
    for result in results:
        assert result["status"] == "error"
        assert result["error"]["status"] == 400
        assert result["error"]["title"] == "Invalid browser_visibility"
        assert result["error"]["charged"] == "no"


async def test_browser_visibility_reaches_the_worker_client_case_insensitively(
    wired: MagicMock,
) -> None:
    """``HEADED`` on the tool is ``headed`` on the client the queued job launches."""
    from gflow_cli.mcp.tools import gflow_generate_image

    result = await gflow_generate_image(prompt="a lighthouse", browser_visibility="HEADED")

    assert result["status"] == "completed"
    kwargs = _client_kwargs(wired)
    assert kwargs["visibility"] is BrowserVisibility.HEADED
    # The retry loop reads its relaunch visibility from the client's settings.
    assert kwargs["settings"].browser_visibility == "headed"


async def test_completed_result_reports_route_visibility_retried(wired: MagicMock) -> None:
    """The tool reply says which path ran, as the CLI's ``--json`` does (Task 17)."""
    from gflow_cli.mcp.tools import gflow_generate_image

    wired.return_value.generate_image = AsyncMock(
        return_value=_FakeImage(
            media_name="media-direct-1",
            transport="direct_http",
            visibility="headless",
            retried=True,
        )
    )
    with patch.object(_FakeImage, "route", "direct", create=True):
        result = await gflow_generate_image(prompt="a lighthouse")

    assert result["status"] == "completed"
    assert result["route"] == "direct"
    assert result["visibility"] == "headless"
    assert result["retried"] is True


# ---------------------------------------------------------------------------
# Row 33 — MCP max_credits routes a capped video to the UI
# ---------------------------------------------------------------------------

_MEASURED_T2V: dict[str, Any] = {
    "model": "omni_1_2_flash",
    "duration": 4,
    "resolution": "360p",
    "aspect": "16:9",
}


def _video_result() -> VideoResult:
    return VideoResult(
        status=VideoStatus(media_id="media-vid-1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=None,
        project_id="proj-abc",
        flow_operation_id="op-1",
    )


async def test_mcp_max_credits_video_routes_ui(wired: MagicMock) -> None:
    """A measured shape goes direct and headless; the same shape with a cap goes to the UI,
    headed, because only the UI reads Flow's quote (SCENARIO row 11)."""
    from gflow_cli.mcp.tools import gflow_generate_video

    wired.return_value.generate_video = AsyncMock(return_value=_video_result())

    uncapped = await gflow_generate_video(prompt="a river", **_MEASURED_T2V)
    assert uncapped["status"] == "completed"
    assert _client_kwargs(wired)["visibility"] is BrowserVisibility.HEADLESS

    capped = await gflow_generate_video(prompt="a river", max_credits=5, **_MEASURED_T2V)
    assert capped["status"] == "completed"
    assert _client_kwargs(wired)["visibility"] is BrowserVisibility.HEADED
    sent = wired.return_value.generate_video.await_args.kwargs["req"]
    assert sent.max_credits == 5


# ---------------------------------------------------------------------------
# Row 32 — the envelopes an agent receives when the retry loop stops
# ---------------------------------------------------------------------------


async def test_mcp_final_refusal_envelope_not_retryable(wired: MagicMock) -> None:
    """A second refusal is final: not charged, not retryable, nothing for the agent to do."""
    from gflow_cli.api.refusal import waf_refusal_final
    from gflow_cli.mcp.tools import gflow_generate_video

    wired.return_value.generate_video = AsyncMock(side_effect=waf_refusal_final())

    result = await gflow_generate_video(prompt="a river", **_MEASURED_T2V)

    assert result["status"] == "failed"
    error = result["error"]
    assert error["retryable"] is False
    assert error["charged"] == "no"
    assert error["next_action"] == "none"


async def test_mcp_relaunch_failed_envelope_not_charged(wired: MagicMock) -> None:
    from gflow_cli.api._flow_session import RelaunchFailed
    from gflow_cli.mcp.tools import gflow_generate_video

    wired.return_value.generate_video = AsyncMock(side_effect=RelaunchFailed("relaunch failed"))

    result = await gflow_generate_video(prompt="a river", **_MEASURED_T2V)

    assert result["status"] == "failed"
    assert result["error"]["charged"] == "no"


async def test_mcp_reauth_stop_envelope_not_charged(wired: MagicMock) -> None:
    from gflow_cli.errors import AuthExpiredError
    from gflow_cli.mcp.tools import gflow_generate_video

    stop = AuthExpiredError(detail="signed out; nothing was sent.", charged="no")
    wired.return_value.generate_video = AsyncMock(side_effect=stop)

    result = await gflow_generate_video(prompt="a river", **_MEASURED_T2V)

    assert result["status"] == "failed"
    assert result["error"]["charged"] == "no"
    assert result["error"]["next_action"] == "reauth"


# ---------------------------------------------------------------------------
# Upscale runs in this process (no queue): the override reaches the client directly
# ---------------------------------------------------------------------------


async def test_upscale_video_takes_browser_visibility_and_reports_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.generation_route import Route
    from gflow_cli.config import reset_settings
    from gflow_cli.mcp.tools import gflow_upscale_video

    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("GFLOW_CLI_BROWSER_VISIBILITY", raising=False)
    reset_settings()
    out_file = tmp_path / "up.mp4"
    out_file.write_bytes(b"\x00" * 16)
    seen: dict[str, Any] = {}

    class _Client:
        upscale_route = Route.UI  # a 720p export drives the download menu

        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)
            self.visibility = kwargs["visibility"]
            self.upsample_video = AsyncMock(return_value=out_file)

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr("gflow_cli.mcp.tools._resolve_and_validate_profile", lambda _p: "default")
    monkeypatch.setattr("gflow_cli.mcp.tools.FlowApiClient", _Client)
    try:
        res = await gflow_upscale_video(
            media_id="7c9e6679-7425-40de-944b-e07fc1f90ae7",
            scale="720p",
            project="proj-1",
            browser_visibility="Headless",
        )
    finally:
        reset_settings()

    assert res["status"] == "ok"
    assert seen["visibility"] is BrowserVisibility.HEADLESS
    assert seen["settings"].browser_visibility == "headless"
    assert res["route"] == "ui"  # only a catalogued 1080p source goes direct
    assert res["visibility"] == "headless"


async def test_upscale_video_reports_the_route_taken_not_the_planned_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A planned direct upscale that ended NotSent ran the download menu: the reply says
    ``ui``, as the image and video replies report the route their result took."""
    from gflow_cli.api.generation_route import Route
    from gflow_cli.config import reset_settings
    from gflow_cli.mcp.tools import gflow_upscale_video

    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path))
    reset_settings()
    out_file = tmp_path / "up.mp4"
    out_file.write_bytes(b"\x00" * 16)

    class _Client:
        upscale_route = Route.DIRECT  # set by upsample_video below

        def __init__(self, **kwargs: Any) -> None:
            self.visibility = kwargs["visibility"]

        async def upsample_video(self, **_: Any) -> Path:
            self.upscale_route = Route.UI  # the direct attempt fell back to the menu
            return out_file

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr("gflow_cli.mcp.tools._resolve_and_validate_profile", lambda _p: "default")
    monkeypatch.setattr("gflow_cli.mcp.tools._upscale_route", lambda *_: Route.DIRECT)
    monkeypatch.setattr("gflow_cli.mcp.tools.FlowApiClient", _Client)
    try:
        res = await gflow_upscale_video(
            media_id="7c9e6679-7425-40de-944b-e07fc1f90ae7", scale="1080p", project="proj-1"
        )
    finally:
        reset_settings()

    assert res["status"] == "ok"
    assert res["route"] == "ui"
