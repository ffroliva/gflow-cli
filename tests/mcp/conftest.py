"""Shared fixtures for MCP end-to-end tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def mcp_server():
    """Return the FastMCP server instance with all tools/resources registered."""
    import gflow_cli.mcp.resources  # noqa: F401
    import gflow_cli.mcp.tools  # noqa: F401
    from gflow_cli.mcp.server import server

    return server


@pytest.fixture()
def mcp_env(tmp_path: Path) -> dict[str, str]:
    """Build an isolated environment for MCP server testing."""
    env: dict[str, str] = {}
    env["PYTHONUTF8"] = "1"
    env["GFLOW_CLI_DB_PATH"] = str(tmp_path / "gflow.db")
    env["GFLOW_CLI_OUTPUT_DIR"] = str(tmp_path / "out")
    return env
