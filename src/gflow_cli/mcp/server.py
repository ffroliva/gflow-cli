# SPDX-License-Identifier: MIT
"""MCP server core — MCPServer instance, stdout redirection, and transport boot.

Stdout isolation is critical: the stdio transport uses stdout for JSON-RPC
messages. Any stray print() or log write to stdout corrupts the channel.
We redirect sys.stdout → sys.stderr on server boot and configure structlog
to always target stderr.
"""

from __future__ import annotations

import asyncio
import hmac
import io
import os
import sys
from typing import Literal

import structlog
from mcp.server import MCPServer
from mcp.server.caching import CacheableMethod, CacheHint
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from gflow_cli import __version__
from gflow_cli.config import get_settings, reset_settings
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


#: How long a tool call waits for a profile another process holds (#862). A human at the
#: CLI can retry a fail-fast `ProfileLockedError`; an MCP client has nobody to do that.
MCP_LEASE_WAIT_SECONDS = "180"


def _apply_mcp_lease_wait_default() -> None:
    """Wait out cross-process profile contention unless the operator chose otherwise.

    Must reset the settings cache: `gflow`'s root command has already loaded settings
    before `mcp run` / `serve` reach here, so an environment default alone is read too
    late (#862 shipped exactly that). A value from the environment OR a `.env` file is
    the operator's choice and wins — including an explicit fail-fast `0`.

    Same-process contention never waits (see `profile_lease`); calls inside this server
    are serialized per profile in `mcp.tools` instead.
    """
    if "lease_wait_seconds" in get_settings().model_fields_set:
        return
    os.environ["GFLOW_CLI_LEASE_WAIT_SECONDS"] = MCP_LEASE_WAIT_SECONDS
    reset_settings()


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


# ---------------------------------------------------------------------------
# HTTP transports — app construction + request auth
# ---------------------------------------------------------------------------


class _BearerAuthMiddleware:
    """Require ``Authorization: Bearer <token>`` on every HTTP request.

    ``Settings.daemon_token`` was checked only at startup (``cli.py`` refuses a
    non-loopback bind without one) and never handed to the transport, so the
    token gated *binding* and not a single request. This is the enforcement.

    Pure ASGI rather than ``BaseHTTPMiddleware``: the streamable-HTTP and SSE
    endpoints are long-lived streams, and ``BaseHTTPMiddleware`` buffers them.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # ``lifespan`` (and any future websocket scope) is not a request and
        # carries no headers — passing it through is what keeps the session
        # manager's startup/shutdown running.
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        scheme, _, presented = Headers(scope=scope).get("authorization", "").partition(" ")
        # RFC 9110: the auth scheme is case-insensitive. ``compare_digest`` is
        # reached through the module so the comparison stays constant-time and
        # the call site stays observable.
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            presented.encode("utf-8"), self._expected
        ):
            response = PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="gflow"'},
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


def build_app(
    *,
    transport: Literal["http", "sse"],
    host: str = "127.0.0.1",
    token: str | None = None,
) -> Starlette:
    """Build the ASGI app both HTTP entry points serve.

    Never cache the result: ``streamable_http_app()`` mints a fresh
    ``StreamableHTTPSessionManager`` whose ``.run()`` may be entered once, so a
    second serve of a memoised app raises at lifespan.

    ``host`` is forwarded rather than defaulted away — the SDK auto-enables
    DNS-rebinding protection only when it sees a loopback host, so dropping it
    would hand a ``--host 0.0.0.0`` deployment a loopback allow-list and reject
    every legitimate remote client. ``transport_security`` is deliberately left
    at ``None`` for the SDK to fill in.

    Args:
        transport: ``"http"`` for Streamable HTTP at :data:`HTTP_PATH`, ``"sse"``
            for the deprecated HTTP+SSE surface.
        host: Bind address, forwarded to the SDK's rebinding heuristic.
        token: Daemon token. ``None`` adds no auth layer at all — the local
            no-token posture stays exactly as open as it was.
    """
    app = (
        server.streamable_http_app(streamable_http_path=HTTP_PATH, host=host)
        if transport == "http"
        else server.sse_app(host=host)
    )
    if token is not None:
        # Added LAST on purpose: ``add_middleware`` inserts at index 0, so the
        # last layer added is the outermost one. Auth must answer before the
        # transport's rebinding guard, or an unauthenticated caller learns the
        # Host allow-list from a 421.
        app.add_middleware(_BearerAuthMiddleware, token=token)
    return app


def _daemon_token() -> str | None:
    """The configured daemon token, unwrapped from its ``SecretStr``."""
    token = get_settings().daemon_token
    return token.get_secret_value() if token else None


def _log_auth_posture(host: str, token: str | None) -> None:
    if token:
        log.info("mcp.server.auth_enforced", host=host, scheme="bearer")
    else:
        log.warning(
            "mcp.server.auth_disabled",
            host=host,
            detail=(
                "requests are unauthenticated; set GFLOW_CLI_DAEMON_TOKEN to require "
                "an Authorization: Bearer header on every request"
            ),
        )


async def _serve(app: Starlette, *, host: str, port: int) -> None:
    """Serve ``app`` with uvicorn, mirroring the SDK's own ``run_*_async``."""
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level=server.settings.log_level.lower())
    await uvicorn.Server(config).serve()


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

    _apply_mcp_lease_wait_default()
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
    _apply_mcp_lease_wait_default()
    _configure_utf8_pipes()

    token = _daemon_token()

    log.info(
        "mcp.server.starting",
        transport="streamable-http",
        host=host,
        port=port,
        path=HTTP_PATH,
        name=_SERVER_NAME,
    )
    _log_auth_posture(host, token)

    _register_surfaces()

    await _serve(
        build_app(transport="http", host=host, token=token),
        host=host,
        port=port,
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
    _apply_mcp_lease_wait_default()
    _configure_utf8_pipes()

    token = _daemon_token()

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
    _log_auth_posture(host, token)

    _register_surfaces()

    await _serve(
        build_app(transport="sse", host=host, token=token),
        host=host,
        port=port,
    )


def main_stdio() -> None:
    """Synchronous wrapper for ``run_stdio`` — called by Click."""
    asyncio.run(run_stdio())


def main_http(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Synchronous wrapper for ``run_http`` — called by Click."""
    asyncio.run(run_http(host=host, port=port))


def main_sse(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Synchronous wrapper for ``run_sse`` — called by Click."""
    asyncio.run(run_sse(host=host, port=port))
