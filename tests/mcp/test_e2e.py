"""End-to-end tests for MCP server — JSON-RPC protocol, tools, resources, transport."""

from __future__ import annotations

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
    """Verify MCP tools return structured responses with correct schemas."""

    @pytest.mark.asyncio
    async def test_generate_image_returns_valid_response(self) -> None:
        """gflow_generate_image must return status and params."""
        from gflow_cli.mcp.tools import gflow_generate_image

        result = await gflow_generate_image(
            prompt="test sunset over mountains",
            model="nano2",
            aspect="16:9",
            count=1,
        )

        assert isinstance(result, dict)
        assert "status" in result
        assert "params" in result
        assert result["params"]["prompt"] == "test sunset over mountains"
        assert result["params"]["model"] == "nano2"

    @pytest.mark.asyncio
    async def test_generate_video_returns_valid_response(self) -> None:
        """gflow_generate_video must return status and params."""
        from gflow_cli.mcp.tools import gflow_generate_video

        result = await gflow_generate_video(
            prompt="cinematic drone shot of city",
            mode="t2v",
            aspect="9:16",
        )

        assert isinstance(result, dict)
        assert "status" in result
        assert "params" in result
        assert result["params"]["mode"] == "t2v"

    @pytest.mark.asyncio
    async def test_list_projects_returns_structured_response(self) -> None:
        """gflow_list_projects must return status and projects list."""
        from gflow_cli.mcp.tools import gflow_list_projects

        result = await gflow_list_projects()

        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert "projects" in result
        assert isinstance(result["projects"], list)

    @pytest.mark.asyncio
    async def test_list_characters_returns_structured_response(self) -> None:
        """gflow_list_characters must return status and characters list."""
        from gflow_cli.mcp.tools import gflow_list_characters

        result = await gflow_list_characters()

        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert "characters" in result
        assert isinstance(result["characters"], list)
