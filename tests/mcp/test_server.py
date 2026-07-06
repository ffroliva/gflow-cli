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
# Tool listing
# ---------------------------------------------------------------------------


class TestMcpToolListing:
    """Verify the server exposes the expected tools."""

    def test_server_has_expected_tools(self, mcp_server: Any) -> None:
        """The server should expose at least 5 core tools."""
        tools = mcp_server._tool_manager._tools
        tool_names = set(tools.keys())
        expected = {
            "gflow_generate_image",
            "gflow_generate_video",
            "gflow_list_projects",
            "gflow_list_characters",
            "gflow_list_tools",
        }
        assert expected.issubset(tool_names), (
            f"Missing tools: {expected - tool_names}. Found: {tool_names}"
        )

    def test_generate_image_tool_has_required_params(self, mcp_server: Any) -> None:
        """gflow_generate_image should accept prompt + model/aspect/count/seed/tools/profile."""
        tool = mcp_server._tool_manager._tools["gflow_generate_image"]
        schema = tool.parameters
        required_fields = {"prompt"}
        assert required_fields.issubset(set(schema.get("required", []))), (
            f"Missing required fields: {required_fields}"
        )
        # CLI/MCP symmetry (AGENTS.md): the CLI --tool option mirrors to a `tools` param.
        assert "tools" in schema.get("properties", {}), (
            "MCP image tool missing 'tools' (CLI parity)"
        )

    def test_generate_video_tool_has_required_params(self, mcp_server: Any) -> None:
        """gflow_generate_video should accept prompt, mode, aspect, image_path, tools, profile."""
        tool = mcp_server._tool_manager._tools["gflow_generate_video"]
        schema = tool.parameters
        required_fields = {"prompt"}
        assert required_fields.issubset(set(schema.get("required", []))), (
            f"Missing required fields: {required_fields}"
        )
        # CLI/MCP symmetry (AGENTS.md): the CLI --tool option mirrors to a `tools` param.
        assert "tools" in schema.get("properties", {}), (
            "MCP video tool missing 'tools' (CLI parity)"
        )


# ---------------------------------------------------------------------------
# gflow_list_tools
# ---------------------------------------------------------------------------


class TestListTools:
    def test_list_tools_registered(self, mcp_server: Any) -> None:
        assert "gflow_list_tools" in mcp_server._tool_manager._tools

    @pytest.mark.asyncio
    async def test_list_tools_payload_shape(self) -> None:
        from gflow_cli.mcp.tools import gflow_list_tools

        payload = await gflow_list_tools()
        names = {t["name"] for t in payload["tools"]}
        assert "creative-director" in names
        cd = next(t for t in payload["tools"] if t["name"] == "creative-director")
        assert {"name", "title", "description", "category"} <= cd.keys()


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

    def test_utf8_pipes_configured(self) -> None:
        """UTF-8 encoding should be configured for stdin/stdout on Windows."""
        import sys
        from unittest.mock import MagicMock

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

    @pytest.mark.asyncio
    async def test_rate_limiter_concurrent_access(self) -> None:
        """Rate limiter should handle concurrent acquisitions safely."""
        from gflow_cli.mcp.tools import _TokenBucket

        bucket = _TokenBucket(capacity=4, refill_rate=0.0)

        async def acquire_token() -> bool:
            return await bucket.acquire()

        results = await asyncio.gather(*[acquire_token() for _ in range(6)])
        assert sum(results) == 4


# ---------------------------------------------------------------------------
# Tool execution (mocked)
# ---------------------------------------------------------------------------


class TestToolExecution:
    """Verify tool handlers return structured responses."""

    @pytest.mark.asyncio
    async def test_generate_image_returns_structured_response(self) -> None:
        """gflow_generate_image should return a dict with status and params when wired."""
        from unittest.mock import AsyncMock, patch

        from gflow_cli.mcp.tools import gflow_generate_image

        with (
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.mcp.tools._run_generation_task",
                new=AsyncMock(
                    return_value={
                        "status": "completed",
                        "task_id": "task-abc",
                        "flow_media_id": "media-123",
                        "files": ["/tmp/out/img.png"],
                    }
                ),
            ),
        ):
            result = await gflow_generate_image(prompt="test sunset", model="nano2")

        assert isinstance(result, dict)
        assert result["status"] == "completed"
        assert "params" in result
        assert result["params"]["prompt"] == "test sunset"

    @pytest.mark.asyncio
    async def test_generate_video_returns_structured_response(self) -> None:
        """gflow_generate_video should return a dict with status and params when wired."""
        from unittest.mock import AsyncMock, patch

        from gflow_cli.mcp.tools import gflow_generate_video

        with (
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.mcp.tools._run_generation_task",
                new=AsyncMock(
                    return_value={
                        "status": "completed",
                        "task_id": "task-xyz",
                        "flow_media_id": "media-vid-456",
                        "files": ["/tmp/out/vid.mp4"],
                    }
                ),
            ),
        ):
            result = await gflow_generate_video(prompt="cinematic drone shot")

        assert isinstance(result, dict)
        assert result["status"] == "completed"
        assert "params" in result
        assert result["params"]["mode"] == "t2v"

    @pytest.mark.asyncio
    async def test_generate_image_adapts_tools_to_specs(self) -> None:
        """A valid MCP `tools` array is adapted to CLI --tool specs in params."""
        from unittest.mock import AsyncMock, patch

        from gflow_cli.mcp.tools import gflow_generate_image

        with (
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.mcp.tools._run_generation_task",
                new=AsyncMock(
                    return_value={
                        "status": "completed",
                        "task_id": "task-tools",
                        "flow_media_id": "media-t",
                        "files": [],
                    }
                ),
            ),
        ):
            result = await gflow_generate_image(
                prompt="a cat",
                tools=[{"name": "creative-director", "options": {"style": "cinema"}}],
            )
        assert result["status"] == "completed"
        assert result["params"]["tool_specs"] == ["creative-director:style=cinema"]

    @pytest.mark.asyncio
    async def test_generate_image_rejects_malformed_tools(self) -> None:
        """A malformed `tools` item returns a clean invalid_tools error."""
        from unittest.mock import patch

        from gflow_cli.mcp.tools import gflow_generate_image

        # Profile resolution must succeed first so tools validation is reached.
        with patch(
            "gflow_cli.mcp.tools._resolve_and_validate_profile",
            return_value="default",
        ):
            result = await gflow_generate_image(prompt="a cat", tools=[{"options": {"style": "x"}}])
        assert result["status"] == "invalid_tools"
        assert "tools" in result["error"]

    @pytest.mark.asyncio
    async def test_generate_video_adapts_tools_to_specs(self) -> None:
        from unittest.mock import AsyncMock, patch

        from gflow_cli.mcp.tools import gflow_generate_video

        with (
            patch(
                "gflow_cli.mcp.tools._resolve_and_validate_profile",
                return_value="default",
            ),
            patch(
                "gflow_cli.mcp.tools._run_generation_task",
                new=AsyncMock(
                    return_value={
                        "status": "completed",
                        "task_id": "task-vt",
                        "flow_media_id": "media-vt",
                        "files": [],
                    }
                ),
            ),
        ):
            result = await gflow_generate_video(
                prompt="a dog",
                tools=[{"name": "creative-director"}],
            )
        assert result["status"] == "completed"
        assert result["params"]["tool_specs"] == ["creative-director"]

    @pytest.mark.asyncio
    async def test_list_projects_returns_empty_list(self) -> None:
        """gflow_list_projects should return an empty list when no data."""
        from unittest.mock import patch

        from gflow_cli.mcp.tools import gflow_list_projects

        with patch("gflow_cli.mcp.tools.list_projects", return_value=[]):
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

    @pytest.mark.asyncio
    async def test_server_has_resources_registered(self, mcp_server: Any) -> None:
        """Server must have resources registered."""
        resources = mcp_server._resource_manager._resources
        assert len(resources) >= 2, f"Expected at least 2 resources, got {len(resources)}"

    @pytest.mark.asyncio
    async def test_known_issues_resource_returns_content(self) -> None:
        """gflow://docs/known-issues must return KNOWN_ISSUES.md content."""
        from gflow_cli.mcp.resources import known_issues

        content = await known_issues()
        assert isinstance(content, str)
        assert len(content) > 0
        assert "##" in content


# ---------------------------------------------------------------------------
# CLI/MCP parameter symmetry
# ---------------------------------------------------------------------------


class TestCliMcpParameterSymmetry:
    """Verify that CLI command parameters match MCP tool signatures.

    This is a CI gate — any new CLI option must have a corresponding
    MCP tool parameter. See AGENTS.md: 'MCP & CLI Schema Symmetry'.
    """

    def test_image_t2i_params_mirrored(self, mcp_server: Any) -> None:
        """Key parameters of `gflow image t2i` must appear in gflow_generate_image."""
        tool = mcp_server._tool_manager._tools["gflow_generate_image"]
        schema_props = set(tool.parameters.get("properties", {}).keys())
        # Core params that must be mirrored
        required_in_both = {"prompt", "model", "aspect", "count", "seed", "profile"}
        assert required_in_both.issubset(schema_props), (
            f"MCP tool missing CLI params: {required_in_both - schema_props}"
        )

    def test_video_t2v_params_mirrored(self, mcp_server: Any) -> None:
        """Key parameters of `gflow video t2v` must appear in gflow_generate_video."""
        tool = mcp_server._tool_manager._tools["gflow_generate_video"]
        schema_props = set(tool.parameters.get("properties", {}).keys())
        required_in_both = {"prompt", "mode", "aspect", "profile", "model", "duration", "count"}
        assert required_in_both.issubset(schema_props), (
            f"MCP tool missing CLI params: {required_in_both - schema_props}"
        )


# ---------------------------------------------------------------------------
# MCP prompts
# ---------------------------------------------------------------------------


class TestMcpPrompts:
    """Verify MCP prompts return expected content."""

    @pytest.mark.asyncio
    async def test_expand_prompt_returns_formula(self) -> None:
        """expand_prompt must return a structured prompt formula."""
        from gflow_cli.mcp.prompts import expand_prompt

        result = expand_prompt(subject="sunset over mountains")
        assert isinstance(result, str)
        assert "Subject: sunset over mountains" in result
        assert "Creative Director" in result

    @pytest.mark.asyncio
    async def test_expand_prompt_is_marked_deprecated(self) -> None:
        """The client-visible description (docstring) must flag deprecation and
        point to the creative-director tool, so MCP clients steer to the
        maintained surface. Functionality is retained for backward compatibility."""
        from gflow_cli.mcp.prompts import expand_prompt

        doc = expand_prompt.__doc__ or ""
        first_line = doc.lstrip().splitlines()[0]
        assert "DEPRECATED" in first_line
        assert "creative-director" in doc

    @pytest.mark.asyncio
    async def test_expand_prompt_with_all_params(self) -> None:
        """expand_prompt must include all provided parameters."""
        from gflow_cli.mcp.prompts import expand_prompt

        result = expand_prompt(
            subject="cat",
            action="sleeping",
            setting="window sill",
            camera="close-up",
            lighting="warm sunset",
        )
        assert "Subject: cat" in result
        assert "Action/Movement: sleeping" in result
        assert "Setting/Location: window sill" in result
        assert "Camera/Framing: close-up" in result
        assert "Lighting/Atmosphere: warm sunset" in result

    @pytest.mark.asyncio
    async def test_create_character_returns_profile(self) -> None:
        """create_character must return a character profile prompt."""
        from gflow_cli.mcp.prompts import create_character

        result = create_character(name="Alice")
        assert isinstance(result, str)
        assert "Alice" in result
        assert "character" in result.lower()

    @pytest.mark.asyncio
    async def test_create_character_with_all_params(self) -> None:
        """create_character must include all provided parameters."""
        from gflow_cli.mcp.prompts import create_character

        result = create_character(
            name="Bob",
            gender="male",
            appearance="tall, brown hair",
            clothing="suit",
        )
        assert "Bob" in result
        assert "male" in result
        assert "brown hair" in result
        assert "suit" in result


# ---------------------------------------------------------------------------
# MCP server entry points
# ---------------------------------------------------------------------------


class TestMcpServerEntryPoints:
    """Verify MCP server entry point functions exist and are callable."""

    def test_run_stdio_is_coroutine_function(self) -> None:
        """run_stdio must be an async function."""
        import inspect

        from gflow_cli.mcp.server import run_stdio

        assert inspect.iscoroutinefunction(run_stdio)

    def test_run_sse_is_coroutine_function(self) -> None:
        """run_sse must be an async function."""
        import inspect

        from gflow_cli.mcp.server import run_sse

        assert inspect.iscoroutinefunction(run_sse)

    def test_main_stdio_is_callable(self) -> None:
        """main_stdio must be a callable function."""
        from gflow_cli.mcp.server import main_stdio

        assert callable(main_stdio)

    def test_main_sse_is_callable(self) -> None:
        """main_sse must be a callable function."""
        from gflow_cli.mcp.server import main_sse

        assert callable(main_sse)

    @pytest.mark.asyncio
    async def test_run_stdio_invokes_server(self) -> None:
        """run_stdio must configure pipes and drive the low-level MCP server.

        It captures the real stdout for the protocol channel (so responses are
        not misrouted to stderr) and runs ``server._mcp_server`` over it.
        """
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, MagicMock, patch

        from gflow_cli.mcp.server import run_stdio

        captured = {}

        @asynccontextmanager
        async def fake_stdio_server(stdout=None):
            captured["stdout"] = stdout
            yield (MagicMock(), MagicMock())

        with (
            patch("gflow_cli.mcp.server.server") as mock_server,
            patch("gflow_cli.mcp.server._configure_utf8_pipes"),
            patch("gflow_cli.mcp.server._redirect_stdout_to_stderr"),
            patch("gflow_cli.mcp.server.sys.stdout", MagicMock()),
            patch("gflow_cli.mcp.server.io.TextIOWrapper"),
            patch("anyio.wrap_file", return_value="PROTOCOL_STDOUT"),
            patch("mcp.server.stdio.stdio_server", fake_stdio_server),
        ):
            mock_server._mcp_server.run = AsyncMock()
            await run_stdio()
            mock_server._mcp_server.run.assert_called_once()
            # The protocol stream must be the captured real stdout, not stderr.
            assert captured["stdout"] == "PROTOCOL_STDOUT"

    @pytest.mark.asyncio
    async def test_run_sse_configures_and_starts(self) -> None:
        """run_sse must configure host/port and call server.run_sse_async."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from gflow_cli.mcp.server import run_sse

        with (
            patch("gflow_cli.mcp.server.server") as mock_server,
            patch("gflow_cli.mcp.server._configure_utf8_pipes"),
        ):
            mock_server.run_sse_async = AsyncMock()
            mock_server.settings = MagicMock()
            await run_sse(host="127.0.0.1", port=9999)
            mock_server.run_sse_async.assert_called_once()
            assert mock_server.settings.host == "127.0.0.1"
            assert mock_server.settings.port == 9999
