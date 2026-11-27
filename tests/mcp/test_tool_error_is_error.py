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

    @probe.tool(name="failed")
    async def failed() -> dict[str, Any]:
        return {"status": "failed", "task_id": "t-1", "error": {"title": "Flow API error"}}

    @probe.tool(name="invalid_tools")
    async def invalid_tools() -> dict[str, Any]:
        return {
            "status": "invalid_tools",
            "error": {
                "type": "https://gflow-cli.dev/errors/bad-parameter",
                "title": "Invalid Tools",
                "status": 400,
                "detail": "Item 0 in 'tools' is invalid.",
                "charged": "no",
                "next_action": "fix_input",
                "retryable": False,
            },
        }

    @probe.tool(name="ok")
    async def ok() -> dict[str, Any]:
        return {"status": "ok"}

    @probe.tool(name="object_status")
    async def object_status() -> dict[str, Any]:
        return {"status": {"complete": True}}

    @probe.tool(name="completed")
    async def completed() -> dict[str, Any]:
        return {"status": "completed", "task_id": "t-1", "files": []}

    return probe


async def test_exception_caught_by_guarded_is_flagged() -> None:
    async with Client(_probe_server(), mode="2026-07-28") as client:
        result = await client.call_tool("raises", {})

    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["status"] == "error"
    # The masked envelope still never carries the raw exception text.
    assert "boom" not in str(result.structured_content)


@pytest.mark.parametrize("tool", ["rate_limited", "failed", "invalid_tools"])
async def test_every_failure_status_is_flagged(tool: str) -> None:
    """#1464: the statuses telemetry already counts as failures (``outcome_of``)."""
    async with Client(_probe_server(), mode="2026-07-28") as client:
        result = await client.call_tool(tool, {})

    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["status"] == tool


@pytest.mark.parametrize("tool", ["pending", "ok", "completed"])
async def test_other_statuses_are_left_alone(tool: str) -> None:
    async with Client(_probe_server(), mode="2026-07-28") as client:
        result = await client.call_tool(tool, {})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["status"] == tool


async def test_malformed_tools_argument_is_flagged(mcp_server: Any) -> None:
    """The real ``invalid_tools`` envelope, through the generate tool a client calls."""
    from unittest.mock import patch

    with patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"):
        async with Client(mcp_server, mode="2026-07-28") as client:
            result = await client.call_tool(
                "gflow_generate_image",
                {"prompt": "a cat", "tools": [{"options": {"secret_opt_key": "secret-val"}}]},
            )

    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["status"] == "invalid_tools"
    err = result.structured_content["error"]
    assert isinstance(err, dict)
    assert err["type"] == "https://gflow-cli.dev/errors/bad-parameter"
    assert (err["charged"], err["next_action"], err["retryable"]) == ("no", "fix_input", False)
    assert "secret-val" not in str(result.structured_content)
    assert "secret_opt_key" not in str(result.structured_content)
    text = " ".join(getattr(c, "text", "") for c in result.content)
    assert "secret-val" not in text
    assert "secret_opt_key" not in text


async def test_is_error_and_telemetry_share_one_failure_set() -> None:
    """Every status the server flags is a telemetry failure; successes are neither."""
    from gflow_cli.mcp.envelope_status import FAILURE_STATUSES, is_failure
    from gflow_cli.mcp.tools import outcome_of

    for status in FAILURE_STATUSES:
        envelope = {"status": status, "error": {"exit_code": 7}}
        assert is_failure(envelope)
        assert outcome_of(envelope) == "7"
    for status in ("ok", "completed", "pending"):
        assert not is_failure({"status": status})
        assert outcome_of({"status": status}) in {"0", "queued"}


async def test_a_status_that_is_not_a_string_is_left_alone() -> None:
    """A tool's own ``status`` object is not an envelope status; it must not break the call."""
    async with Client(_probe_server(), mode="2026-07-28") as client:
        result = await client.call_tool("object_status", {})

    assert result.is_error is False
    assert result.structured_content == {"status": {"complete": True}}
