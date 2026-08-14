# SPDX-License-Identifier: MIT
"""MCP server core — MCPServer instance, stdout redirection, and transport boot.

Stdout isolation is critical: the stdio transport uses stdout for JSON-RPC
messages. Any stray print() or log write to stdout corrupts the channel.
We redirect sys.stdout → sys.stderr on server boot and configure structlog
to always target stderr.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys

import structlog
from mcp.server import MCPServer
from mcp.server.caching import CacheableMethod, CacheHint

from gflow_cli import __version__
from gflow_cli.mcp.tasks_extension import TasksExtension

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

_SERVER_NAME = "gflow-cli"
_SERVER_VERSION = __version__

#: Streamable-HTTP mount path. ``/mcp`` is the SDK and ecosystem default.
HTTP_PATH = "/mcp"

_HOUR_MS = 60 * 60 * 1000
_FIVE_MIN_MS = 5 * 60 * 1000

# 2026-07-28 cacheable list results (``ttlMs`` / ``cacheScope``). Our listing
# surfaces are decided at import time by decorators, so they are constant for a
# process lifetime — an hour is comfortably conservative against that.
#
# ``resources/read`` gets a much shorter TTL because it is NOT static: the
# known-issues resource reads KNOWN_ISSUES.md off disk, so its content can
# change under a running daemon (e.g. an editable install being edited).
#
# Scope stays ``private`` throughout. gflow is a local, single-user daemon
# driving one user's authenticated browser profile; ``public`` would authorize
# shared/proxy caching we have no use for and would be the wrong default to set
# for a server whose responses are user-scoped by construction.
_CACHE_HINTS: dict[CacheableMethod, CacheHint] = {
    "tools/list": CacheHint(ttl_ms=_HOUR_MS, scope="private"),
    "prompts/list": CacheHint(ttl_ms=_HOUR_MS, scope="private"),
    "resources/list": CacheHint(ttl_ms=_HOUR_MS, scope="private"),
    "resources/templates/list": CacheHint(ttl_ms=_HOUR_MS, scope="private"),
    "resources/read": CacheHint(ttl_ms=_FIVE_MIN_MS, scope="private"),
}

tasks_extension = TasksExtension()

# ``MCPServer`` is the mcp>=2 successor to ``FastMCP`` (which 2.0.0 deleted).
# The decorator API is unchanged; ``version`` is now a first-class constructor
# argument, so the old ``server._mcp_server.version = ...`` private-API poke is
# gone.
server = MCPServer(
    name=_SERVER_NAME,
    version=_SERVER_VERSION,
    cache_hints=_CACHE_HINTS,
    extensions=[tasks_extension],
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


# Credit-spending tools gated by --no-spend (#496). BOTH generate tools:
# image generation is only *empirically* free ("~0 credits observed") and
# no-spend must be a hard guarantee, so anything not contractually free is in.
_SPEND_TOOLS = ("gflow_generate_image", "gflow_generate_video")


def no_spend_active() -> bool:
    """True when no-spend mode is requested (#496).

    ``gflow mcp run --no-spend`` sets ``GFLOW_MCP_NO_SPEND=1``; the env var
    alone also works (and covers ``gflow serve``). The falsy set matches
    Click's boolean vocabulary so 'off'/'no'/'n'/'f' cannot mean False to the
    CLI flag and True to this policy (post-merge review of #496).
    """
    value = os.environ.get("GFLOW_MCP_NO_SPEND", "").strip().lower()
    return value not in ("", "0", "false", "off", "no", "n", "f")


def _apply_no_spend_policy() -> None:
    """Registration-policy seam (#496): under no-spend the credit-spending
    generate tools are removed from the registry entirely, so ``tools/list``
    never shows them. Invisible beats refused — no wasted calls, no refusal
    path for prompt injection to probe, no reliance on the model honoring an
    error. Tools bind via import-time decorators, so the policy runs as a
    post-registration removal rather than an ``if`` around each decorator.
    """
    if not no_spend_active():
        return
    # Idempotent: remove_tool raises ToolError on a missing name, and this
    # runs once per transport boot — a second _register_surfaces() call (or a
    # test fixture that already stripped the tools) must not crash the server.
    from mcp.server.mcpserver.exceptions import ToolError

    removed: list[str] = []
    for name in _SPEND_TOOLS:
        try:
            server.remove_tool(name)
        except ToolError:
            continue
        removed.append(name)
    if removed:
        log.info("mcp.no_spend_active", removed=removed)


def _register_surfaces() -> None:
    """Import tools/prompts/resources so their decorators register them.

    Registration is an import side effect, so these imports are deliberate and
    must not be pruned as "unused".
    """
    from gflow_cli.mcp import prompts as _prompts
    from gflow_cli.mcp import resources as _resources
    from gflow_cli.mcp import tools as _tools

    # Access them to satisfy pyright unused import check
    _ = (_prompts, _resources, _tools)
    _apply_no_spend_policy()


async def run_stdio() -> None:
    """Run the MCP server over stdio transport (Claude Desktop, Cursor, etc.).

    This is the entry point for ``gflow mcp run``.

    Protocol era is negotiated by the SDK, not by us: the low-level
    ``Server.run`` drives ``serve_dual_era_loop``, which serves BOTH the legacy
    handshake era (2024-11-05 … 2025-11-25) and the stateless 2026-07-28 era.
    The client's first request decides which — so one binary speaks to both old
    and new clients with no protocol code on our side.
    """
    import anyio
    from mcp.server.stdio import stdio_server

    _configure_utf8_pipes()

    # Capture the REAL stdout for the JSON-RPC channel BEFORE redirecting
    # sys.stdout to stderr. MCPServer.run_stdio_async() binds sys.stdout at call
    # time, so redirecting first routes every protocol message to stderr and a
    # real MCP client (which reads stdout) sees nothing. We wrap the original
    # stdout here, then redirect sys.stdout so stray print() calls still can't
    # corrupt the channel.
    protocol_stdout = anyio.wrap_file(io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))
    _redirect_stdout_to_stderr()

    log.info("mcp.server.starting", transport="stdio", name=_SERVER_NAME)

    _register_surfaces()

    # Drive the low-level server directly so the protocol writes to the real
    # stdout we captured above (MCPServer.run_stdio_async exposes no stdout
    # param). ``Server.run`` is the dual-era driver — see the docstring.
    mcp_server = server._lowlevel_server  # type: ignore[attr-defined]
    async with stdio_server(stdout=protocol_stdout) as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options(),
        )


async def run_http(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the MCP server over Streamable HTTP (the current spec transport).

    This is the default entry point for ``gflow serve``. Streamable HTTP
    replaces HTTP+SSE, which the 2026-07-28 spec formally deprecated.

    ``stateless_http`` is deliberately left at its ``False`` default. The
    2026-07-28 stateless core exists so servers can scale out across
    interchangeable instances — the opposite of what gflow is. Our value is a
    warm daemon holding one live Chromium profile, serialized by a cross-process
    ``ProfileLease``; spreading requests over stateless workers would buy
    nothing and fight that lease. The *protocol* is stateless either way (the
    2026-07-28 handshake removal is handled by the SDK); this flag only governs
    whether the transport keeps per-connection bookkeeping, and we want it.

    Args:
        host: Bind address. Defaults to localhost-only for security.
        port: Port number. Defaults to 8000.
    """
    _configure_utf8_pipes()

    log.info(
        "mcp.server.starting",
        transport="streamable-http",
        host=host,
        port=port,
        path=HTTP_PATH,
        name=_SERVER_NAME,
    )

    _register_surfaces()

    await server.run_streamable_http_async(
        host=host,
        port=port,
        streamable_http_path=HTTP_PATH,
    )


async def run_sse(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the MCP server over the DEPRECATED HTTP+SSE transport.

    Kept for one deprecation cycle so existing ``gflow serve`` clients pinned to
    ``/sse`` keep working. The 2026-07-28 spec reclassified HTTP+SSE as
    deprecated; prefer :func:`run_http`. Callers reach this via
    ``gflow serve --transport sse``.

    Args:
        host: Bind address. Defaults to localhost-only for security.
        port: Port number. Defaults to 8000.
    """
    _configure_utf8_pipes()

    log.warning(
        "mcp.server.starting",
        transport="sse",
        host=host,
        port=port,
        name=_SERVER_NAME,
        deprecated=(
            "HTTP+SSE is deprecated by the MCP 2026-07-28 spec; "
            "migrate to --transport http (Streamable HTTP at /mcp)."
        ),
    )

    _register_surfaces()

    await server.run_sse_async(host=host, port=port)


def main_stdio() -> None:
    """Synchronous wrapper for ``run_stdio`` — called by Click."""
    asyncio.run(run_stdio())


def main_http(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Synchronous wrapper for ``run_http`` — called by Click."""
    asyncio.run(run_http(host=host, port=port))


def main_sse(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Synchronous wrapper for ``run_sse`` — called by Click."""
    asyncio.run(run_sse(host=host, port=port))
