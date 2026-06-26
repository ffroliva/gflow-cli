"""End-to-end tests for MCP server — JSON-RPC protocol, tools, resources, transport."""

from __future__ import annotations

from typing import Any


class TestMcpToolDiscovery:
    """Verify MCP server exposes expected tools via JSON-RPC."""

    def test_server_exposes_all_core_tools(self, mcp_server: Any) -> None:
        """All four core tools must be registered and discoverable."""
        tools = mcp_server._tool_manager._tools
        expected_tools = {
            "gflow_generate_image",
            "gflow_generate_video",
            "gflow_list_projects",
            "gflow_list_characters",
        }
        actual_tools = set(tools.keys())
        assert expected_tools.issubset(actual_tools), (
            f"Missing tools: {expected_tools - actual_tools}"
        )

    def test_generate_image_tool_has_correct_schema(self, mcp_server: Any) -> None:
        """gflow_generate_image must accept prompt, model, aspect, count, seed, profile."""
        tool = mcp_server._tool_manager._tools["gflow_generate_image"]
        schema = tool.parameters
        properties = set(schema.get("properties", {}).keys())
        required = set(schema.get("required", []))

        assert "prompt" in required, "prompt must be required"
        assert {"prompt", "model", "aspect", "count", "seed", "profile"}.issubset(properties)

    def test_generate_video_tool_has_correct_schema(self, mcp_server: Any) -> None:
        """gflow_generate_video must accept prompt, mode, aspect, image_path, profile."""
        tool = mcp_server._tool_manager._tools["gflow_generate_video"]
        schema = tool.parameters
        properties = set(schema.get("properties", {}).keys())
        required = set(schema.get("required", []))

        assert "prompt" in required, "prompt must be required"
        assert {"prompt", "mode", "aspect", "image_path", "profile"}.issubset(properties)
