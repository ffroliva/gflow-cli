from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from gflow_cli.config import get_settings
from gflow_cli.data.redaction import redact_metadata
from gflow_cli.data.store import DataStore
from gflow_cli.mcp.server import server
from gflow_cli.worker.daemon import FlowWorker
from gflow_cli.worker.queue import QueueRepository, recover_processing

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings = get_settings()
    profile_name = settings.profile or "default"
    db_path = settings.resolved_db_path()

    logger.info(
        "Initializing gflow-daemon lifespan", profile_name=profile_name, db_path=str(db_path)
    )

    # 1. Startup crash recovery: classify each hung 'processing' row by its
    #    checkpoint instead of blanket-failing. A task whose submit may have spent
    #    a credit becomes 'indeterminate' (never silently failed, never resubmit);
    #    a pre-submit task becomes 'failed'. Never calls generation.
    with DataStore.open(db_path) as store:
        repo = QueueRepository(store)
        recovered = recover_processing(repo, profile_name)
        if recovered["failed"] or recovered["indeterminate"]:
            logger.info(
                "Recovered hung processing tasks on startup",
                failed=recovered["failed"],
                indeterminate=recovered["indeterminate"],
            )

    # No daemon-lifetime profile lock (D3): the daemon does NOT own the profile
    # while idle. Each browser-owning task acquires the cross-process
    # ProfileLease inside FlowApiClient's launch path for exactly its own
    # lifetime, so an overwriteable daemon-lifetime lock file would be both
    # redundant and unsafe (any process could clobber it).

    # 2. Start FlowWorker loop in background
    worker = FlowWorker(profile_name, str(db_path))
    worker_task = asyncio.create_task(worker.start())
    app.state.worker = worker
    app.state.worker_task = worker_task

    # try/finally (D4): shutdown MUST run even if the lifespan body is cancelled
    # (Ctrl-C / ASGI server shutdown) — otherwise the worker task and its DB
    # store leak. Teardown order: (1) stop accepting work -> (2) cancel + await
    # the worker -> (5) close the store this component owns. The worker releases
    # any per-task browser lease itself (D3/D4) as its own client tears down.
    try:
        yield
    finally:
        logger.info("Shutting down gflow-daemon lifespan")
        worker.stop()  # (1) stop accepting new work
        worker_task.cancel()  # (2) cancel...
        # ...and await completion. gather(..., return_exceptions=True) captures
        # the worker's own CancelledError as a result instead of swallowing a
        # genuine cancellation of this lifespan task, which propagates out.
        await asyncio.gather(worker_task, return_exceptions=True)
        worker.close()  # (5) close the DB store owned here
        logger.info("gflow-daemon worker stopped cleanly")


app = FastAPI(title="gflow-daemon", lifespan=lifespan)

# mcp>=2 dropped FastMCP's `mount_path=` shim. In 1.x that shim split two
# values apart: the SSE handshake ADVERTISED "/mcp/messages/" while the POST
# route stayed mounted at "/messages/" inside the sub-app. mcp>=2's `sse_app`
# derives both from a single `message_path`, so we can no longer have them
# differ — and we need them to, because this app is mounted under "/mcp"
# (Starlette strips that prefix before dispatching inward).
#
# So we keep the SDK default ("/messages/"), which keeps the inner route — and
# the SDK's auto-enabled DNS-rebinding protection — correct, and add an explicit
# "/messages/" alias below for the endpoint the handshake advertises. Without
# that alias the failure is silent: the stream opens, then every client POST
# 404s.
#
# NOTE: this surface is still HTTP+SSE, which the MCP 2026-07-28 spec
# deprecates. `gflow serve` already defaults to Streamable HTTP; migrating this
# FastAPI daemon too is deliberately left as follow-up because
# `streamable_http_app()` requires `server.session_manager.run()` to be driven
# from the app lifespan, and the /mcp request-logging middleware and the
# singular /mcp/message alias below are both SSE-shaped. That is a behavioural
# change to a separate, currently unwired surface (`ui.server.run_server` has no
# caller), not a mechanical port.
mcp_sse_app = server.sse_app()


class LogMcpRequestsMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.startswith("/mcp"):
            if path in ("/mcp/message", "/mcp/messages"):
                if await self._forward_logged_mcp_message(scope, receive, send, path):
                    return
            else:
                logger.info("Incoming MCP request", path=path)

        await self.app(scope, receive, send)

    def _log_mcp_payload(self, path: str, body_bytes: bytes) -> None:
        """Log the decoded MCP request payload (redacted), or raw bytes if not JSON."""
        try:
            body_json = json.loads(body_bytes)
            redacted = redact_metadata(body_json)
            logger.info("Incoming MCP request payload", path=path, payload=redacted)
        except Exception:
            logger.info("Incoming MCP request raw payload", path=path, size=len(body_bytes))

    async def _forward_logged_mcp_message(
        self, scope: Scope, receive: Receive, send: Send, path: str
    ) -> bool:
        """Read+log the body of an /mcp/message(s) POST, then forward it; True if forwarded."""
        try:
            # Read all body chunks safely
            body_bytes = b""
            more_body = True
            chunks: list[bytes] = []
            while more_body:
                message = await receive()
                chunks.append(message.get("body", b""))
                more_body = message.get("more_body", False)
            body_bytes = b"".join(chunks)

            if body_bytes:
                self._log_mcp_payload(path, body_bytes)

            # Reconstruct receive so downstream handlers can read it
            async def wrapped_receive() -> dict[str, Any]:
                return {"type": "http.request", "body": body_bytes, "more_body": False}

            await self.app(scope, wrapped_receive, send)
            return True
        except Exception as exc:
            logger.warning("Failed to parse request body in middleware", exc_info=exc)
            return False


app.add_middleware(LogMcpRequestsMiddleware)


class AlreadySentResponse(Response):
    """Response that does nothing; used when response was already written to ASGI send."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Intentional no-op: the response was already written directly to the ASGI
        # `send` upstream, so this placeholder must not emit anything further.
        return


async def _dispatch_to_sse_app(request: Request, inner_path: str) -> Response:
    """Forward a request into the mounted SSE sub-app at ``inner_path``.

    The sub-app is mounted at "/mcp", so paths must be rewritten to be
    mount-relative before dispatch.
    """
    scope = dict(request.scope)
    scope["path"] = inner_path
    scope["raw_path"] = inner_path.encode()
    receive = request._receive  # type: ignore[reportPrivateUsage]
    send = request._send  # type: ignore[reportPrivateUsage]
    await mcp_sse_app(scope, receive, send)
    return AlreadySentResponse()


@app.post("/mcp/message")
async def post_mcp_message_singular(request: Request) -> Response:
    """Route singular message requests to the mounted Starlette app messages path."""
    return await _dispatch_to_sse_app(request, "/messages")


@app.post("/messages/")
async def post_mcp_message_advertised(request: Request) -> Response:
    """Serve the endpoint the SSE handshake actually advertises.

    mcp>=2 advertises the sub-app-relative "/messages/" (see the `sse_app()`
    note above), but the sub-app is mounted under "/mcp" — so without this alias
    every client POST after a successful handshake would 404 at the app root.
    """
    return await _dispatch_to_sse_app(request, "/messages/")


# Mount Starlette SSE application after defining more specific routes
app.mount("/mcp", mcp_sse_app)
