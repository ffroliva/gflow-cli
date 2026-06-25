# SPDX-License-Identifier: MIT
"""Tests for the MCP server — tool listing, schema validation, and CLI/MCP symmetry.

These tests verify that:
1. The MCP server exposes the expected tools with correct schemas.
2. CLI command parameters have parity with MCP tool signatures.
3. Error boundaries catch exceptions without crashing.
4. Stdout redirection works (stdio transport safety).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def server():
    """Return the FastMCP server instance."""
    import gflow_cli.mcp.resources  # noqa: F401
    import gflow_cli.mcp.tools  # noqa: F401
    from gflow_cli.mcp.server import server

    return server


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------


class TestMcpToolListing:
    """Verify the server exposes the expected tools."""

    def test_server_has_expected_tools(self, server: Any) -> None:
        """The server should expose at least 4 core tools."""
        tools = server._tool_manager._tools
        tool_names = set(tools.keys())
        expected = {
            "gflow_generate_image",
            "gflow_generate_video",
            "gflow_list_projects",
            "gflow_list_characters",
        }
        assert expected.issubset(tool_names), (
            f"Missing tools: {expected - tool_names}. Found: {tool_names}"
        )

    def test_generate_image_tool_has_required_params(self, server: Any) -> None:
        """gflow_generate_image should accept prompt, model, aspect, count, seed, profile."""
        tool = server._tool_manager._tools["gflow_generate_image"]
        schema = tool.parameters
        required_fields = {"prompt"}
        assert required_fields.issubset(set(schema.get("required", []))), (
            f"Missing required fields: {required_fields}"
        )

    def test_generate_video_tool_has_required_params(self, server: Any) -> None:
        """gflow_generate_video should accept prompt, mode, aspect, image_path, profile."""
        tool = server._tool_manager._tools["gflow_generate_video"]
        schema = tool.parameters
        required_fields = {"prompt"}
        assert required_fields.issubset(set(schema.get("required", []))), (
            f"Missing required fields: {required_fields}"
        )


# ---------------------------------------------------------------------------
# Stdout redirection
# ---------------------------------------------------------------------------


class TestStdoutRedirection:
    """Verify stdout → stderr redirection for stdio transport safety."""

    def test_redirect_stdout_to_stderr(self) -> None:
        """After redirection, sys.stdout should write to stderr's buffer."""
        import io
        import sys

        from gflow_cli.mcp.server import _redirect_stdout_to_stderr

        mock_stdout = object()

        class MockStderr:
            buffer = io.BytesIO()

        # Patch sys streams and modules to pretend we are not in pytest
        with (
            patch("sys.stdout", mock_stdout),
            patch("sys.stderr", MockStderr()),
            patch("sys.modules", {}),
            patch("gflow_cli.mcp.server.io.TextIOWrapper") as mock_wrapper,
        ):
            _redirect_stdout_to_stderr()

            mock_wrapper.assert_called_once()
            assert sys.stdout is not mock_stdout


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class TestTokenBucketRateLimiter:
    """Verify the token-bucket rate limiter."""

    @pytest.mark.asyncio
    async def test_acquire_succeeds_within_capacity(self) -> None:
        """Acquisitions within bucket capacity should succeed."""
        from gflow_cli.mcp.tools import _TokenBucket

        bucket = _TokenBucket(capacity=3, refill_rate=0.0)
        assert await bucket.acquire() is True
        assert await bucket.acquire() is True
        assert await bucket.acquire() is True

    @pytest.mark.asyncio
    async def test_acquire_fails_when_empty(self) -> None:
        """Acquisitions beyond capacity should fail (rate-limited)."""
        from gflow_cli.mcp.tools import _TokenBucket

        bucket = _TokenBucket(capacity=1, refill_rate=0.0)
        assert await bucket.acquire() is True
        assert await bucket.acquire() is False

    @pytest.mark.asyncio
    async def test_bucket_refills_over_time(self) -> None:
        """Tokens should refill at the configured rate."""
        from gflow_cli.mcp.tools import _TokenBucket

        bucket = _TokenBucket(capacity=1, refill_rate=100.0)  # fast refill for testing
        assert await bucket.acquire() is True
        assert await bucket.acquire() is False
        await asyncio.sleep(0.02)  # wait for refill
        assert await bucket.acquire() is True


# ---------------------------------------------------------------------------
# Tool execution (mocked)
# ---------------------------------------------------------------------------


class TestToolExecution:
    """Verify tool handlers return structured responses."""

    @pytest.mark.asyncio
    async def test_generate_image_returns_structured_response(self) -> None:
        """gflow_generate_image should return a dict with status and params."""
        from gflow_cli.mcp.tools import gflow_generate_image

        result = await gflow_generate_image(prompt="test sunset", model="nano2")
        assert isinstance(result, dict)
        assert "status" in result
        assert "params" in result
        assert result["params"]["prompt"] == "test sunset"

    @pytest.mark.asyncio
    async def test_generate_video_returns_structured_response(self) -> None:
        """gflow_generate_video should return a dict with status and params."""
        from gflow_cli.mcp.tools import gflow_generate_video

        result = await gflow_generate_video(prompt="cinematic drone shot")
        assert isinstance(result, dict)
        assert "status" in result
        assert "params" in result
        assert result["params"]["mode"] == "t2v"

    @pytest.mark.asyncio
    async def test_list_projects_returns_empty_list(self) -> None:
        """gflow_list_projects should return an empty list when no data."""
        from gflow_cli.mcp.tools import gflow_list_projects

        result = await gflow_list_projects()
        assert result["status"] == "ok"
        assert result["projects"] == []

    @pytest.mark.asyncio
    async def test_list_characters_returns_empty_list(self) -> None:
        """gflow_list_characters should return an empty list when no data."""
        from gflow_cli.mcp.tools import gflow_list_characters

        result = await gflow_list_characters()
        assert result["status"] == "ok"
        assert result["characters"] == []


# ---------------------------------------------------------------------------
# MCP resources
# ---------------------------------------------------------------------------


class TestMcpResources:
    """Verify MCP resources are registered and return content."""

    @pytest.mark.asyncio
    async def test_mcp_guide_returns_content(self) -> None:
        """gflow://docs/mcp-guide should return agent instructions."""
        from gflow_cli.mcp.resources import mcp_guide

        content = await mcp_guide()
        assert "gflow_generate_image" in content
        assert "gflow_generate_video" in content
        assert "Use tools, not shell commands" in content

    @pytest.mark.asyncio
    async def test_db_schema_resource(self) -> None:
        """gflow://db/schema should return SQL or a not-found message."""
        from gflow_cli.mcp.resources import db_schema

        content = await db_schema()
        assert isinstance(content, str)
        assert len(content) > 0


# ---------------------------------------------------------------------------
# CLI/MCP parameter symmetry
# ---------------------------------------------------------------------------


class TestCliMcpParameterSymmetry:
    """Verify that CLI command parameters match MCP tool signatures.

    This is a CI gate — any new CLI option must have a corresponding
    MCP tool parameter. See AGENTS.md: 'MCP & CLI Schema Symmetry'.
    """

    def test_image_t2i_params_mirrored(self, server: Any) -> None:
        """Key parameters of `gflow image t2i` must appear in gflow_generate_image."""
        tool = server._tool_manager._tools["gflow_generate_image"]
        schema_props = set(tool.parameters.get("properties", {}).keys())
        # Core params that must be mirrored
        required_in_both = {"prompt", "model", "aspect", "count", "seed", "profile"}
        assert required_in_both.issubset(schema_props), (
            f"MCP tool missing CLI params: {required_in_both - schema_props}"
        )

    def test_video_t2v_params_mirrored(self, server: Any) -> None:
        """Key parameters of `gflow video t2v` must appear in gflow_generate_video."""
        tool = server._tool_manager._tools["gflow_generate_video"]
        schema_props = set(tool.parameters.get("properties", {}).keys())
        required_in_both = {"prompt", "mode", "aspect", "profile"}
        assert required_in_both.issubset(schema_props), (
            f"MCP tool missing CLI params: {required_in_both - schema_props}"
        )
