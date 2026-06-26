"""Shared fixtures for MCP tests."""

from __future__ import annotations

import pytest


@pytest.fixture()
def mcp_server():
    """Return the FastMCP server instance with all tools/resources registered."""
    import gflow_cli.mcp.resources  # noqa: F401
    import gflow_cli.mcp.tools  # noqa: F401
    from gflow_cli.mcp.server import server

    return server
