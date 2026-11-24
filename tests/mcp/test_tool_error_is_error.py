"""A plain tools/call that returns an error envelope is flagged isError (#1453).

Every tool returns its envelope as a dict, so without the server override the SDK
reported every failure with ``isError: false``. These tests go through a real
client session, the way an MCP client sees the result.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from gflow_cli.mcp.media_trash_tools import gflow_media_trash
from gflow_cli.mcp.server import GflowMCPServer
from gflow_cli.mcp.tools import _guarded  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.asyncio


async def test_envelope_built_outside_guarded_is_flagged(mcp_server: Any) -> None:
    """The confirmation-required envelope is built by hand in media_trash_tools."""
    direct = await gflow_media_trash(media_id="m-1")
    assert direct["status"] == "error"

    async with Client(mcp_server, mode="2026-07-28") as client:
        result = await client.call_tool("gflow_media_trash", {"media_id": "m-1"})

    assert result.is_error is True
    assert result.structured_content == direct


async def test_bad_parameter_envelope_is_flagged(mcp_server: Any) -> None:
    async with Client(mcp_server, mode="2026-07-28") as client:
        result = await client.call_tool("gflow_media_trash", {"media_id": "  "})

    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["status"] == "error"
    assert result.structured_content["error"]["type"].endswith("/bad-parameter")


async def test_successful_call_is_not_flagged(mcp_server: Any) -> None:
    async with Client(mcp_server, mode="2026-07-28") as client:
        result = await client.call_tool("gflow_list_models", {})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content.get("status") != "error"


def _probe_server() -> GflowMCPServer:
    probe = GflowMCPServer(name="probe")

    @probe.tool(name="raises")
    @_guarded
    async def raises() -> dict[str, Any]:
        raise RuntimeError("boom")

    @probe.tool(name="pending")
    async def pending() -> dict[str, Any]:
        return {"status": "pending", "task_id": "t-1"}

    @probe.tool(name="rate_limited")
    async def rate_limited() -> dict[str, Any]:
        return {"status": "rate_limited", "error": {"title": "Rate limited"}}

    return probe


async def test_exception_caught_by_guarded_is_flagged() -> None:
    async with Client(_probe_server(), mode="2026-07-28") as client:
        result = await client.call_tool("raises", {})

    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["status"] == "error"
    # The masked envelope still never carries the raw exception text.
    assert "boom" not in str(result.structured_content)


@pytest.mark.parametrize("tool", ["pending", "rate_limited"])
async def test_other_statuses_are_left_alone(tool: str) -> None:
    async with Client(_probe_server(), mode="2026-07-28") as client:
        result = await client.call_tool(tool, {})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["status"] == tool
