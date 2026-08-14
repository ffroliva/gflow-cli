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
        """The server should expose the core generation + listing tools."""
        tools = mcp_server._tool_manager._tools
        tool_names = set(tools.keys())
        expected = {
            "gflow_generate_image",
            "gflow_generate_video",
            "gflow_list_projects",
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

    def test_list_characters_stub_is_gone(self) -> None:
        """#499: the old stub answered ok+[] — an agent reads that as "you
        have no characters" and acts on the lie. The tool stays absent until
        it can return real data."""
        from gflow_cli.mcp import tools

        assert not hasattr(tools, "gflow_list_characters")


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
    async def test_known_issues_resource_returns_bounded_index(self) -> None:
        """gflow://docs/known-issues returns the #501 bounded index."""
        from gflow_cli.mcp.resources import known_issues

        content = await known_issues()
        assert isinstance(content, str)
        assert len(content) > 0
        assert len(content.encode()) < 16 * 1024
        assert "gflow://docs/known-issues/" in content


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
        required_in_both = {"prompt", "model", "aspect", "count", "seed", "profile", "instructions"}
        assert required_in_both.issubset(schema_props), (
            f"MCP tool missing CLI params: {required_in_both - schema_props}"
        )

    def test_video_t2v_params_mirrored(self, mcp_server: Any) -> None:
        """Key parameters of `gflow video t2v` must appear in gflow_generate_video."""
        tool = mcp_server._tool_manager._tools["gflow_generate_video"]
        schema_props = set(tool.parameters.get("properties", {}).keys())
        required_in_both = {"prompt", "mode", "aspect", "profile"}
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
        not misrouted to stderr) and runs ``server._lowlevel_server`` over it
        (renamed from ``_mcp_server`` in the mcp>=2 SDK).
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
            mock_server._lowlevel_server.run = AsyncMock()
            await run_stdio()
            mock_server._lowlevel_server.run.assert_called_once()
            # The protocol stream must be the captured real stdout, not stderr.
            assert captured["stdout"] == "PROTOCOL_STDOUT"

    @pytest.mark.asyncio
    async def test_run_http_starts_streamable_http_on_mcp_path(self) -> None:
        """run_http must serve Streamable HTTP on /mcp with the given bind.

        ``stateless_http`` must NOT be forced on: gflow's value is a warm daemon
        holding one Chromium profile behind a ProfileLease, so the SDK default
        (False) is the deliberate choice.
        """
        from unittest.mock import AsyncMock, patch

        from gflow_cli.mcp.server import HTTP_PATH, run_http

        with (
            patch("gflow_cli.mcp.server.server") as mock_server,
            patch("gflow_cli.mcp.server._configure_utf8_pipes"),
        ):
            mock_server.run_streamable_http_async = AsyncMock()
            await run_http(host="127.0.0.1", port=9999)

            mock_server.run_streamable_http_async.assert_called_once_with(
                host="127.0.0.1",
                port=9999,
                streamable_http_path=HTTP_PATH,
            )
            kwargs = mock_server.run_streamable_http_async.call_args.kwargs
            assert "stateless_http" not in kwargs

    @pytest.mark.asyncio
    async def test_run_sse_configures_and_starts(self) -> None:
        """run_sse must pass host/port through to the deprecated SSE runner.

        mcp>=2 takes host/port as explicit kwargs; the old ``server.settings``
        mutation is gone.
        """
        from unittest.mock import AsyncMock, patch

        from gflow_cli.mcp.server import run_sse

        with (
            patch("gflow_cli.mcp.server.server") as mock_server,
            patch("gflow_cli.mcp.server._configure_utf8_pipes"),
        ):
            mock_server.run_sse_async = AsyncMock()
            await run_sse(host="127.0.0.1", port=9999)
            mock_server.run_sse_async.assert_called_once_with(host="127.0.0.1", port=9999)


def test_mcp_retryable_matches_cli() -> None:
    """§6.5 (S24): the MCP error envelope's retryable flag must derive from the
    same shared classification the CLI JSON surface uses — no drift lists."""
    from gflow_cli.errors import (
        ContentPolicyError,
        FlowAgentUiError,
        FlowAppError,
        WafRejectionError,
        is_retryable,
    )
    from gflow_cli.mcp.tools import _gflow_error_dict

    for exc in (
        FlowAppError("crash"),
        FlowAgentUiError("agentic"),
        WafRejectionError("blocked"),
        ContentPolicyError("nope"),
    ):
        error = _gflow_error_dict(exc)
        assert error["retryable"] is is_retryable(exc)
        # RFC 9457 fields still carried through unchanged.
        assert error["title"]
        assert error["type"].startswith("https://gflow-cli.dev/errors/")


def test_mcp_error_envelope_omits_local_path() -> None:
    """S21: the MCP envelope reuses to_problem_details() — opaque incident
    {id, capture_status} only, never the absolute local path or artifacts."""
    import json
    from pathlib import Path

    from gflow_cli.diagnostics import IncidentRef
    from gflow_cli.errors import FlowAppError
    from gflow_cli.mcp.tools import _gflow_error_dict

    exc = FlowAppError("crash")
    exc.incident_ref = IncidentRef(
        id="corr-fp",
        capture_status="complete",
        path=Path("/home/CANARYUSER/gflow/incidents/x"),
        artifacts=("ui.json", "sensitive/screenshot.png"),
    )
    error = _gflow_error_dict(exc)
    assert error["incident"] == {"id": "corr-fp", "capture_status": "complete"}
    blob = json.dumps(error)
    assert "CANARYUSER" not in blob
    assert "screenshot" not in blob


# ---------------------------------------------------------------------------
# Protocol era negotiation + 2026-07-28 cacheable list results
# ---------------------------------------------------------------------------


class TestProtocolEras:
    """Drive a real client against the real server over both protocol eras.

    These are the only tests that exercise the wire rather than the registry.
    The MCP 2026-07-28 spec removed the handshake, so the SDK serves two eras
    from one binary (`serve_dual_era_loop`) and the client's first request
    decides which. Everything below would still pass on a server that silently
    spoke only one of them if it were asserted against the registry instead.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("mode", "expected_version"),
        [("2026-07-28", "2026-07-28"), ("legacy", "2025-11-25")],
    )
    async def test_server_serves_both_protocol_eras(
        self, mcp_server: Any, mode: str, expected_version: str
    ) -> None:
        """One server binary must answer both the modern and handshake eras."""
        from mcp import Client

        import gflow_cli.mcp.prompts  # noqa: F401

        async with Client(mcp_server, mode=mode) as client:
            assert client.protocol_version == expected_version

            # `server_info` arrives in the handshake's InitializeResult, so it is
            # populated on the legacy era and absent on the modern one until the
            # client asks for it via `server/discover` — itself a consequence of
            # the handshake removal, so assert the era-appropriate shape rather
            # than papering over the difference.
            if mode == "legacy":
                assert client.server_info is not None
                assert client.server_info.name == "gflow-cli"
            else:
                assert client.server_info is None

            listed = await client.list_tools()
            assert len(listed.tools) > 0

    @pytest.mark.asyncio
    async def test_cache_hints_are_advertised_on_the_modern_wire(self, mcp_server: Any) -> None:
        """2026-07-28 list results must carry our ttlMs/cacheScope hints.

        Configuring `cache_hints` on the server is not sufficient evidence that
        clients receive them — the hint is applied during response
        serialization, so only a real round-trip proves it.
        """
        from mcp import Client

        import gflow_cli.mcp.prompts  # noqa: F401
        from gflow_cli.mcp.server import _FIVE_MIN_MS, _HOUR_MS

        async with Client(mcp_server, mode="2026-07-28") as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            prompts = await client.list_prompts()
            read = await client.read_resource("gflow://db/schema")

        # Listing surfaces are fixed at import time by decorators.
        assert tools.ttl_ms == _HOUR_MS
        assert resources.ttl_ms == _HOUR_MS
        assert prompts.ttl_ms == _HOUR_MS

        # resources/read is NOT static: the known-issues resource reads
        # KNOWN_ISSUES.md off disk, so it gets a much shorter TTL.
        assert read.ttl_ms == _FIVE_MIN_MS

        # gflow is a local single-user daemon driving one authenticated browser
        # profile — responses are user-scoped, so shared caching is never
        # authorized.
        assert tools.cache_scope == "private"
        assert resources.cache_scope == "private"

    @pytest.mark.asyncio
    async def test_legacy_wire_does_not_carry_2026_cache_fields(self, mcp_server: Any) -> None:
        """ttlMs/cacheScope are 2026-era vocabulary and must be sieved off legacy.

        Pinned because the failure would be invisible from the server side: the
        hints are configured identically for both eras, and it is the SDK's
        per-version surface that strips them on the way out.
        """
        from mcp import Client

        import gflow_cli.mcp.prompts  # noqa: F401

        async with Client(mcp_server, mode="legacy") as client:
            tools = await client.list_tools()
            resources = await client.list_resources()

        assert tools.ttl_ms == 0
        assert resources.ttl_ms == 0


class TestUiModeParity:
    """#299 PR-A: the CLI --ui-mode option mirrors to a ui_mode param on BOTH
    generate tools (AGENTS.md §61); video rejects 'agentic' at the tool edge
    (no agentic video driver exists)."""

    def test_both_generate_tools_expose_ui_mode(self, mcp_server: Any) -> None:
        for tool_name in ("gflow_generate_image", "gflow_generate_video"):
            tool = mcp_server._tool_manager._tools[tool_name]
            assert "ui_mode" in tool.parameters.get("properties", {}), (
                f"{tool_name} missing 'ui_mode' (CLI parity, refs #299)"
            )

    @pytest.mark.asyncio
    async def test_video_tool_rejects_agentic_ui_mode(self) -> None:
        from gflow_cli.mcp.tools import gflow_generate_video

        result = await gflow_generate_video(prompt="x", ui_mode="agentic")
        assert result["status"] == "error"
        assert "agentic" in str(result).lower()

    @pytest.mark.asyncio
    async def test_video_tool_rejects_unknown_ui_mode(self) -> None:
        from gflow_cli.mcp.tools import gflow_generate_video

        result = await gflow_generate_video(prompt="x", ui_mode="bogus")
        assert result["status"] == "error"
        assert "ui_mode" in str(result).lower()


class TestUiModeEnvelopeShape:
    """#299 code-review findings: ui_mode 400s use the RFC 9457 dict envelope
    (never a flat string), and the param is case-insensitive like the CLI."""

    @pytest.mark.asyncio
    async def test_invalid_ui_mode_returns_dict_envelope(self) -> None:
        from gflow_cli.mcp.tools import gflow_generate_video

        result = await gflow_generate_video(prompt="x", ui_mode="bogus")
        assert result["status"] == "error"
        assert result["error"]["title"] == "Invalid ui_mode"

    @pytest.mark.asyncio
    async def test_ui_mode_is_case_insensitive(self) -> None:
        from gflow_cli.mcp.tools import gflow_generate_video

        result = await gflow_generate_video(prompt="x", ui_mode="AGENTIC")
        # Normalized first, THEN rejected as agentic — not as an unknown value.
        assert result["error"]["title"] == "Unsupported ui_mode for video"
