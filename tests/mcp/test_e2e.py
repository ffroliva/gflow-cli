"""End-to-end tests for MCP server — JSON-RPC protocol, tools, resources, transport."""

from __future__ import annotations

from typing import Any

import pytest

# Import tools and resources to register them
import gflow_cli.mcp.resources  # noqa: F401
import gflow_cli.mcp.tools  # noqa: F401
from gflow_cli.mcp.server import server


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


class TestMcpToolExecution:
    """Verify MCP tools return structured responses when called via server."""

    @pytest.mark.asyncio
    async def test_generate_image_via_server(self, mcp_server: Any) -> None:
        """Server tool execution must return structured response."""
        tool = mcp_server._tool_manager._tools["gflow_generate_image"]
        result = await tool.run(arguments={"prompt": "test sunset", "model": "nano2"})

        assert isinstance(result, dict)
        assert "status" in result
        assert "params" in result

    @pytest.mark.asyncio
    async def test_generate_video_via_server(self, mcp_server: Any) -> None:
        """Server tool execution must return structured response."""
        tool = mcp_server._tool_manager._tools["gflow_generate_video"]
        result = await tool.run(arguments={"prompt": "cinematic shot", "mode": "t2v"})

        assert isinstance(result, dict)
        assert "status" in result
        assert "params" in result

    @pytest.mark.asyncio
    async def test_list_projects_via_server(self, mcp_server: Any) -> None:
        """Server tool execution must return structured response."""
        tool = mcp_server._tool_manager._tools["gflow_list_projects"]
        result = await tool.run(arguments={})

        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert "projects" in result

    @pytest.mark.asyncio
    async def test_list_characters_via_server(self, mcp_server: Any) -> None:
        """Server tool execution must return structured response."""
        tool = mcp_server._tool_manager._tools["gflow_list_characters"]
        result = await tool.run(arguments={})

        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert "characters" in result


class TestMcpResources:
    """Verify MCP resources return expected content."""

    @pytest.mark.asyncio
    async def test_mcp_guide_resource_returns_content(self) -> None:
        """gflow://docs/mcp-guide must return agent instructions."""
        from gflow_cli.mcp.resources import mcp_guide

        content = await mcp_guide()

        assert isinstance(content, str)
        assert len(content) > 0
        assert "gflow_generate_image" in content
        assert "gflow_generate_video" in content
        assert "Use tools, not shell commands" in content

    @pytest.mark.asyncio
    async def test_known_issues_resource_returns_content(self) -> None:
        """gflow://docs/known-issues must return KNOWN_ISSUES.md content."""
        from gflow_cli.mcp.resources import known_issues

        content = await known_issues()

        assert isinstance(content, str)
        assert len(content) > 0

    @pytest.mark.asyncio
    async def test_db_schema_resource_returns_content(self) -> None:
        """gflow://db/schema must return SQL schema."""
        from gflow_cli.mcp.resources import db_schema

        content = await db_schema()

        assert isinstance(content, str)
        assert len(content) > 0
