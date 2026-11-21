"""Shared fixtures for MCP tests."""

from __future__ import annotations

import pytest

# Register every prompt/resource/tool against the REAL server before any test runs.
# `run_stdio()`/`run_http()` import these modules lazily, and three tests call them
# with `gflow_cli.mcp.server.server` patched to a MagicMock. Whichever import comes
# first is cached, so if it happens under that patch, `@server.prompt()` returns a mock
# and TestMcpPrompts fails for the rest of the process. Single-process runs happened to
# import them earlier; pytest-xdist's scheduling did not (5 failures, measured).
import gflow_cli.mcp.media_trash_tools  # noqa: F401
import gflow_cli.mcp.prompts
import gflow_cli.mcp.resources
import gflow_cli.mcp.tools  # noqa: F401
import gflow_cli.mcp.video_captions_tools  # noqa: F401


@pytest.fixture(autouse=True)
def _fresh_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test its own token bucket.

    The module-level bucket holds 8 tokens per process, so under pytest-xdist whichever
    worker happened to run a 9th generation-tool call got a rate-limited envelope
    (`KeyError: 'params'` in CI, #1017).
    """
    tools = gflow_cli.mcp.tools
    monkeypatch.setattr(tools, "_rate_limiter", tools._TokenBucket())  # pyright: ignore[reportPrivateUsage]


@pytest.fixture()
def mcp_server():
    """Return the MCPServer instance with all tools/resources registered."""
    import gflow_cli.mcp.resources  # noqa: F401
    import gflow_cli.mcp.tools  # noqa: F401
    from gflow_cli.mcp.server import server

    return server
