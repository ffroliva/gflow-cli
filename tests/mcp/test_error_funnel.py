"""Centralized MCP tool error funnel (issue #473).

Raw exception text must never reach an MCP client: an unexpected exception
gets a masked RFC-9457 envelope (exception class name only — messages can
embed filesystem paths, profile names, or token text) while the real message
goes to the server-side log. GFlowErrors keep their structured
problem-details envelope. Every registered tool routes through the funnel.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

CANARY = r"SECRET-C:\Users\victim token=abc123"


def test_every_registered_tool_is_guarded(mcp_server: Any) -> None:
    """The funnel only works if EVERY tool routes through it — a new tool
    registered without the guard reverts to the framework's raw str(exc)."""
    tools = mcp_server._tool_manager._tools
    unguarded = [
        name for name, tool in tools.items() if not getattr(tool.fn, "__gflow_guarded__", False)
    ]
    assert unguarded == [], f"tools bypassing the error funnel: {unguarded}"


@pytest.mark.asyncio
async def test_unexpected_exception_is_masked(mcp_server: Any) -> None:
    tool = mcp_server._tool_manager._tools["gflow_list_tools"]
    with patch("gflow_cli.tools.registry.iter_tools", side_effect=RuntimeError(CANARY)):
        result = await tool.fn()
    assert result["status"] == "error"
    dumped = json.dumps(result)
    assert CANARY not in dumped
    assert "victim" not in dumped
    # The class name IS safe and useful for a bug report.
    assert "RuntimeError" in result["error"]["detail"]
    assert result["error"]["status"] == 500
    assert result["error"]["retryable"] is False
    # Same key set as the GFlowError envelope — clients see ONE schema.
    assert result["error"]["message"] == result["error"]["detail"]


@pytest.mark.asyncio
async def test_gflow_error_keeps_structured_envelope(mcp_server: Any) -> None:
    from gflow_cli.errors import ConfigurationError

    tool = mcp_server._tool_manager._tools["gflow_list_tools"]
    with patch(
        "gflow_cli.tools.registry.iter_tools",
        side_effect=ConfigurationError("bad setting"),
    ):
        result = await tool.fn()
    assert result["status"] == "error"
    assert result["error"]["title"] == ConfigurationError.title
    assert "retryable" in result["error"]
    assert "ConfigurationError" in result["error"]["message"]


@pytest.mark.asyncio
async def test_list_projects_gflow_error_reaches_the_funnel(mcp_server: Any, tmp_path: Any) -> None:
    """The tool's old inner `except Exception` swallowed GFlowErrors into a
    masked string — a DataStoreError must keep its structured envelope."""
    from gflow_cli.errors import DataStoreError

    tool = mcp_server._tool_manager._tools["gflow_list_projects"]
    with (
        patch("gflow_cli.mcp.tools.list_projects", side_effect=DataStoreError("schema drift")),
        patch(
            "gflow_cli.mcp.tools.get_settings",
            return_value=MagicMock(resolved_db_path=lambda: tmp_path / "gflow.db"),
        ),
    ):
        result = await tool.fn()
    assert result["status"] == "error"
    assert result["error"]["title"] == DataStoreError.title
    assert "retryable" in result["error"]
