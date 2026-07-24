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

import structlog
from mcp.server.fastmcp import FastMCP

from gflow_cli import __version__

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

_SERVER_NAME = "gflow-cli"
_SERVER_VERSION = __version__

# Handed to clients at initialize; the first orientation an agent reads.
_SERVER_INSTRUCTIONS = """\
gflow-cli drives the user's own Google Flow session: Veo video and Imagen \
image generation.

- To verify the connection, call gflow_list_projects (fast, local, credits-free).
- Image generation is credits-free; gflow_generate_video SPENDS the user's Veo \
credits — confirm with the user before calling it.
- gflow_list_tools lists optional prompt-rewriting presets ("prompt tools") for \
the generation tools' `tools` parameter — it is NOT this server's tool catalog.
- If a tool returns an auth error, tell the user to run \
`gflow auth login --browser chrome`.
"""

server = FastMCP(
    name=_SERVER_NAME,
    instructions=_SERVER_INSTRUCTIONS,
)
# FastMCP does not expose a public API to configure the server version.
# As a workaround, we assign it directly to the underlying Server instance.
server._mcp_server.version = _SERVER_VERSION  # pyright: ignore[reportPrivateUsage]

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
    import anyio
    from mcp.server.stdio import stdio_server

    _configure_utf8_pipes()

    # Capture the REAL stdout for the JSON-RPC channel BEFORE redirecting
    # sys.stdout to stderr. FastMCP.run_stdio_async() binds sys.stdout at call
    # time, so redirecting first routes every protocol message to stderr and a
    # real MCP client (which reads stdout) sees nothing. We wrap the original
    # stdout here, then redirect sys.stdout so stray print() calls still can't
    # corrupt the channel.
    protocol_stdout = anyio.wrap_file(io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))
    _redirect_stdout_to_stderr()

    log.info("mcp.server.starting", transport="stdio", name=_SERVER_NAME)

    # Import tools, prompts, resources to register them with the server
    from gflow_cli.mcp import prompts as _prompts
    from gflow_cli.mcp import resources as _resources
    from gflow_cli.mcp import tools as _tools

    # Access them to satisfy pyright unused import check
    _ = (_prompts, _resources, _tools)

    # Drive the low-level server directly so the protocol writes to the real
    # stdout we captured above (FastMCP.run_stdio_async exposes no stdout param).
    mcp_server = server._mcp_server  # type: ignore[attr-defined]
    async with stdio_server(stdout=protocol_stdout) as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options(),
        )


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
    from gflow_cli.mcp import prompts as _prompts
    from gflow_cli.mcp import resources as _resources
    from gflow_cli.mcp import tools as _tools

    # Access them to satisfy pyright unused import check
    _ = (_prompts, _resources, _tools)

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
