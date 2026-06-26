# SPDX-License-Identifier: MIT
"""MCP server core — FastMCP instance, stdout redirection, and transport boot.

Stdout isolation is critical: the stdio transport uses stdout for JSON-RPC
messages. Any stray print() or log write to stdout corrupts the channel.
We redirect sys.stdout → sys.stderr on server boot and configure structlog
to always target stderr.
"""

from __future__ import annotations

import asyncio
import io
import sys
from typing import TYPE_CHECKING

import structlog
from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    pass

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

_SERVER_NAME = "gflow-cli"
_SERVER_VERSION = "0.20.1"

server = FastMCP(
    name=_SERVER_NAME,
)

# ---------------------------------------------------------------------------
# Stdout isolation
# ---------------------------------------------------------------------------


def _redirect_stdout_to_stderr() -> None:
    """Redirect sys.stdout to sys.stderr for stdio transport safety.

    This prevents any stray print() call from corrupting the JSON-RPC
    channel. Called once at server boot for stdio transport.
    """
    if "pytest" in sys.modules:
        return
    if sys.stdout is not sys.stderr:
        # Wrap stderr in a TextIOWrapper that matches stdout's interface if possible
        if hasattr(sys.stderr, "buffer") and sys.stderr.buffer is not None:
            sys.stdout = io.TextIOWrapper(
                sys.stderr.buffer,
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
            )
        else:
            sys.stdout = sys.stderr


def _configure_utf8_pipes() -> None:
    """Ensure stdin/stdout use UTF-8 encoding on Windows.

    Windows consoles default to cp1252 or similar, causing mojibake
    on non-ASCII prompt strings in JSON-RPC messages.
    """
    if sys.platform == "win32":
        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(sys, stream_name)
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def run_stdio() -> None:
    """Run the MCP server over stdio transport (Claude Desktop, Cursor, etc.).

    This is the entry point for ``gflow mcp run``.
    """
    _configure_utf8_pipes()
    _redirect_stdout_to_stderr()

    log.info("mcp.server.starting", transport="stdio", name=_SERVER_NAME)

    # Import tools, prompts, resources to register them with the server
    from gflow_cli.mcp import resources as _resources
    from gflow_cli.mcp import tools as _tools

    # Access them to satisfy pyright unused import check
    _ = (_resources, _tools)

    # FastMCP run_stdio_async is a coroutine
    await server.run_stdio_async()  # type: ignore[attr-defined]


async def run_sse(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the MCP server over SSE transport (Gflow Studio, web clients).

    This is the entry point for ``gflow serve`` (SSE mode).

    Args:
        host: Bind address. Defaults to localhost-only for security.
        port: Port number. Defaults to 8000.
    """
    _configure_utf8_pipes()

    log.info(
        "mcp.server.starting",
        transport="sse",
        host=host,
        port=port,
        name=_SERVER_NAME,
    )

    # Import tools, prompts, resources to register them with the server
    from gflow_cli.mcp import resources as _resources
    from gflow_cli.mcp import tools as _tools

    # Access them to satisfy pyright unused import check
    _ = (_resources, _tools)

    # Configure the server settings dynamically before running
    server.settings.host = host  # type: ignore[attr-defined]
    server.settings.port = port  # type: ignore[attr-defined]
    await server.run_sse_async()  # type: ignore[attr-defined]


def main_stdio() -> None:
    """Synchronous wrapper for ``run_stdio`` — called by Click."""
    asyncio.run(run_stdio())


def main_sse(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Synchronous wrapper for ``run_sse`` — called by Click."""
    asyncio.run(run_sse(host=host, port=port))
