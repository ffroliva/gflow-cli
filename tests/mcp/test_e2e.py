"""End-to-end tests for MCP server — JSON-RPC protocol, tools, resources, transport."""

from __future__ import annotations

import asyncio
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
        assert "##" in content

    @pytest.mark.asyncio
    async def test_db_schema_resource_returns_content(self) -> None:
        """gflow://db/schema must return SQL schema."""
        from gflow_cli.mcp.resources import db_schema

        content = await db_schema()

        assert isinstance(content, str)
        assert len(content) > 0
        assert "CREATE TABLE" in content


class TestMcpStdoutIsolation:
    """Verify stdout is redirected to stderr for JSON-RPC transport safety."""

    def test_redirect_stdout_to_stderr(self) -> None:
        """After redirection, sys.stdout should write to stderr's buffer."""
        import io
        from unittest.mock import patch

        from gflow_cli.mcp.server import _redirect_stdout_to_stderr

        mock_stdout = object()

        class MockStderr:
            buffer = io.BytesIO()

        with (
            patch("sys.stdout", mock_stdout),
            patch("sys.stderr", MockStderr()),
            patch("sys.modules", {}),
            patch("gflow_cli.mcp.server.io.TextIOWrapper") as mock_wrapper,
        ):
            _redirect_stdout_to_stderr()
            mock_wrapper.assert_called_once()

    def test_utf8_pipes_configured(self) -> None:
        """UTF-8 encoding should be configured for stdin/stdout on Windows."""
        import sys
        from unittest.mock import MagicMock, patch

        from gflow_cli.mcp.server import _configure_utf8_pipes

        mock_stream = MagicMock()
        mock_stream.reconfigure = MagicMock()

        with (
            patch.object(sys, "platform", "win32"),
            patch.object(sys, "stdin", mock_stream),
            patch.object(sys, "stdout", mock_stream),
            patch.object(sys, "stderr", mock_stream),
        ):
            _configure_utf8_pipes()
            assert mock_stream.reconfigure.call_count == 3


class TestMcpRateLimiter:
    """Verify token-bucket rate limiter behavior under controlled conditions."""

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_burst_within_capacity(self) -> None:
        """Should allow up to 8 consecutive calls (bucket capacity)."""
        from gflow_cli.mcp.tools import _TokenBucket

        bucket = _TokenBucket(capacity=8, refill_rate=0.0)

        for _ in range(8):
            assert await bucket.acquire() is True

    @pytest.mark.asyncio
    async def test_rate_limiter_blocks_when_exhausted(self) -> None:
        """Should block after capacity is exhausted."""
        from gflow_cli.mcp.tools import _TokenBucket

        bucket = _TokenBucket(capacity=2, refill_rate=0.0)

        assert await bucket.acquire() is True
        assert await bucket.acquire() is True
        assert await bucket.acquire() is False

    @pytest.mark.asyncio
    async def test_rate_limiter_refills_over_time(self) -> None:
        """Tokens should refill at the configured rate."""
        from gflow_cli.mcp.tools import _TokenBucket

        bucket = _TokenBucket(capacity=1, refill_rate=100.0)

        assert await bucket.acquire() is True
        assert await bucket.acquire() is False
        await asyncio.sleep(0.02)
        assert await bucket.acquire() is True

    @pytest.mark.asyncio
    async def test_rate_limiter_concurrent_access(self) -> None:
        """Rate limiter should handle concurrent acquisitions safely."""
        from gflow_cli.mcp.tools import _TokenBucket

        bucket = _TokenBucket(capacity=4, refill_rate=0.0)

        async def acquire_token() -> bool:
            return await bucket.acquire()

        results = await asyncio.gather(*[acquire_token() for _ in range(6)])
        assert sum(results) == 4
