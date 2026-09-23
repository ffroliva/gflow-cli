"""FlowApiClient project create/rename: which host does the work (#864).

labs.google's project route answers 404 "Flow RPCs have been deprecated and disabled"
(401 for a session without a labs token) on every profile measured on 2026-09-17. The
client falls back to flow.google.com on THAT observed refusal — never on host membership,
never when the operator pinned labs.google, and never for an unrelated failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import ProjectInfo
from gflow_cli.config import Settings
from gflow_cli.errors import AuthExpiredError, WafRejectionError, WireFormatError

PID = "57c099ee-a5d6-4e2d-83c9-0346f9644b09"
LABS_OK = {
    "result": {
        "data": {"json": {"result": {"projectId": "labs-1", "projectInfo": {"projectTitle": "t"}}}}
    }
}
RETIRED = WireFormatError(detail="Flow RPCs have been deprecated and disabled.", status=404)
NO_TOKEN = AuthExpiredError(detail="UNAUTHORIZED", status=401)


def _client(tmp_path: Path, flow_host: str, labs: Any) -> FlowApiClient:
    c = FlowApiClient(profile_dir=tmp_path / "prof", settings=Settings(flow_host=flow_host))
    c._page = MagicMock()  # noqa: SLF001 - checkout affordance for unentered clients
    c._post_json = (
        AsyncMock(side_effect=labs) if isinstance(labs, Exception) else AsyncMock(return_value=labs)
    )  # type: ignore[method-assign]
    return c


@pytest.fixture
def migrated() -> Any:
    with (
        patch(
            "gflow_cli.api.transports.migrated_composer.create_project",
            new=AsyncMock(return_value=ProjectInfo(project_id=PID, title="t")),
        ) as create,
        patch(
            "gflow_cli.api.transports.migrated_composer.rename_project", new=AsyncMock()
        ) as rename,
    ):
        yield create, rename


async def test_pinned_flow_google_com_never_calls_labs(tmp_path: Path, migrated: Any) -> None:
    c = _client(tmp_path, "flow.google.com", LABS_OK)

    info = await c.create_project(title="t")
    await c.rename_project(PID, "t2")

    assert info.project_id == PID
    c._post_json.assert_not_awaited()  # type: ignore[attr-defined]
    migrated[1].assert_awaited_once()


async def test_auto_keeps_a_working_labs_route(tmp_path: Path, migrated: Any) -> None:
    c = _client(tmp_path, "auto", LABS_OK)

    info = await c.create_project(title="t")

    assert info.project_id == "labs-1"
    migrated[0].assert_not_awaited()


@pytest.mark.parametrize("refusal", [RETIRED, NO_TOKEN], ids=["404-retired", "401-no-token"])
async def test_auto_falls_back_on_the_observed_refusal(
    tmp_path: Path, migrated: Any, refusal: Exception
) -> None:
    c = _client(tmp_path, "auto", refusal)

    info = await c.create_project(title="t")
    await c.rename_project(PID, "t2")

    assert info.project_id == PID
    migrated[0].assert_awaited_once()
    migrated[1].assert_awaited_once()


async def test_pinned_labs_never_falls_back(tmp_path: Path, migrated: Any) -> None:
    c = _client(tmp_path, "labs.google", RETIRED)

    with pytest.raises(WireFormatError):
        await c.create_project(title="t")
    migrated[0].assert_not_awaited()


@pytest.mark.parametrize(
    "other",
    [
        WafRejectionError(detail="waf", status=403),
        WireFormatError(detail="bad request", status=400),
    ],
    ids=["403", "400"],
)
async def test_other_failures_are_not_a_reason_to_switch_host(
    tmp_path: Path, migrated: Any, other: Exception
) -> None:
    c = _client(tmp_path, "auto", other)

    with pytest.raises(type(other)):
        await c.create_project(title="t")
    migrated[0].assert_not_awaited()


async def test_migrated_create_gets_the_default_title_when_none_given(
    tmp_path: Path, migrated: Any
) -> None:
    c = _client(tmp_path, "flow.google.com", LABS_OK)

    await c.create_project()

    title = migrated[0].await_args.args[1]
    assert isinstance(title, str) and title


# --- video without a project -----------------------------------------------------------


class _VideoTransport:
    def __init__(self) -> None:
        self.project_ids: list[str | None] = []

    async def generate_video(self, *, request: Any, project_id: str | None, **_: Any) -> Any:
        self.project_ids.append(project_id)
        return MagicMock(project_id=project_id)


def _video_client(tmp_path: Path, flow_host: str) -> tuple[FlowApiClient, _VideoTransport]:
    from gflow_cli.api.transports.base import VideoCapableTransport

    c = _client(tmp_path, flow_host, RETIRED)
    t = _VideoTransport()
    c.transport = t  # type: ignore[assignment]
    assert isinstance(t, VideoCapableTransport)
    return c, t


async def test_video_without_a_project_gets_one_created_first(
    tmp_path: Path, migrated: Any
) -> None:
    """The labs gallery used to create it inside the transport — a path that cannot reach
    flow.google.com's projects page. Creating at the client boundary, exactly as
    generate_image already does, hands every video route an existing project."""
    from gflow_cli.api.video import GenerateVideoRequest, Mode

    c, t = _video_client(tmp_path, "auto")

    await c.generate_video(req=GenerateVideoRequest(prompt="a crane", mode=Mode.T2V))

    assert t.project_ids == [PID]
    migrated[0].assert_awaited_once()


async def test_video_with_a_project_creates_nothing(tmp_path: Path, migrated: Any) -> None:
    from gflow_cli.api.video import GenerateVideoRequest, Mode

    c, t = _video_client(tmp_path, "auto")

    await c.generate_video(
        req=GenerateVideoRequest(prompt="a crane", mode=Mode.T2V), project_id="p1"
    )

    assert t.project_ids == ["p1"]
    c._post_json.assert_not_awaited()  # type: ignore[attr-defined]
    migrated[0].assert_not_awaited()
