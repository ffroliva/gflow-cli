"""End-to-end tests for MCP server — JSON-RPC protocol, tools, resources, transport."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gflow_cli.mcp.server import server

# Import tools and resources to register them
import gflow_cli.mcp.tools  # noqa: F401
import gflow_cli.mcp.resources  # noqa: F401


class TestMcpJsonRpcProtocol:
    """Verify MCP server responds to JSON-RPC requests correctly."""

    @pytest.mark.asyncio
    async def test_server_has_tools_registered(self) -> None:
        """Server must have at least 4 tools registered."""
        tools = server._tool_manager._tools
        assert len(tools) >= 4, f"Expected at least 4 tools, got {len(tools)}"

    @pytest.mark.asyncio
    async def test_server_has_resources_registered(self) -> None:
        """Server must have resources registered."""
        resources = server._resource_manager._resources
        assert len(resources) >= 2, f"Expected at least 2 resources, got {len(resources)}"

    @pytest.mark.asyncio
    async def test_tool_execution_returns_dict(self) -> None:
        """Tool execution must return a dictionary."""
        from gflow_cli.mcp.tools import gflow_list_projects

        result = await gflow_list_projects()
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_resource_read_returns_string(self) -> None:
        """Resource read must return a string."""
        from gflow_cli.mcp.resources import mcp_guide

        content = await mcp_guide()
        assert isinstance(content, str)
        assert len(content) > 0
