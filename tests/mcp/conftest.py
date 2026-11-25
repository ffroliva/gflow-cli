"""Shared fixtures for MCP tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import gflow_cli.mcp.audio_tools  # noqa: F401

# The parity suites run on their own in /gflow:check step 1b, so every tool module is imported here.
import gflow_cli.mcp.character_tools  # noqa: F401

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
from tests.mcp.openai_fakes import PNG, FakeTool


@pytest.fixture(autouse=True)
def _fresh_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test its own token bucket.

    The module-level bucket holds 8 tokens per process, so under pytest-xdist whichever
    worker happened to run a 9th generation-tool call got a rate-limited envelope
    (`KeyError: 'params'` in CI, #1017).
    """
    tools = gflow_cli.mcp.tools
    monkeypatch.setattr(tools, "_rate_limiter", tools._TokenBucket())  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def png_files(tmp_path: Path) -> list[str]:
    """Two distinct PNG files, as a finished image generation leaves them."""
    paths = []
    for i in range(2):
        p = tmp_path / f"img{i}.png"
        p.write_bytes(PNG + bytes([i]))
        paths.append(str(p))
    return paths


@pytest.fixture
def set_image_tool(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], FakeTool]:
    """Replace ``gflow_generate_image`` with a :class:`FakeTool` answering a given result.

    Also clears an operator's ``GFLOW_MCP_NO_SPEND``, which would turn every route test
    into a 403.
    """
    monkeypatch.delenv("GFLOW_MCP_NO_SPEND", raising=False)

    def _set(result: dict[str, Any]) -> FakeTool:
        fake = FakeTool(result)
        monkeypatch.setattr(gflow_cli.mcp.tools, "gflow_generate_image", fake)
        return fake

    return _set


@pytest.fixture()
def mcp_server():
    """Return the MCPServer instance with every tool module registered, as at startup."""
    from gflow_cli.mcp.server import _register_surfaces, server  # noqa: PLC2701

    _register_surfaces()
    return server
