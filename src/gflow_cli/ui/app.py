from __future__ import annotations

import asyncio
import contextlib
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

    # Write daemon lockfile
    import os

    profile_dir = settings.profile_subdir(profile_name)
    profile_dir.mkdir(parents=True, exist_ok=True)
    daemon_lock = profile_dir / "profile.lock"
    daemon_lock.write_text(str(os.getpid()))

    # 2. Start FlowWorker loop in background
    worker = FlowWorker(profile_name, str(db_path))
    worker_task = asyncio.create_task(worker.start())
    app.state.worker = worker
    app.state.worker_task = worker_task

    yield

    # Shutdown
    logger.info("Shutting down gflow-daemon lifespan")
    daemon_lock = settings.profile_subdir(profile_name) / "profile.lock"
    if daemon_lock.exists():
        with contextlib.suppress(FileNotFoundError):
            daemon_lock.unlink()

    worker.stop()
    worker_task.cancel()
    # Await the worker's completion. gather(..., return_exceptions=True) captures
    # the worker task's own CancelledError as a result instead of swallowing a
    # genuine cancellation of this lifespan task, which is re-raised.
    await asyncio.gather(worker_task, return_exceptions=True)
    worker.close()
    logger.info("gflow-daemon worker stopped cleanly")


app = FastAPI(title="gflow-daemon", lifespan=lifespan)

mcp_sse_app = server.sse_app(mount_path="/mcp")


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


@app.post("/mcp/message")
async def post_mcp_message_singular(request: Request) -> Response:
    """Route singular message requests to the mounted Starlette app messages path."""
    scope = dict(request.scope)
    scope["path"] = "/messages"
    scope["raw_path"] = b"/messages"
    receive = request._receive  # type: ignore[reportPrivateUsage]
    send = request._send  # type: ignore[reportPrivateUsage]
    await mcp_sse_app(scope, receive, send)
    return AlreadySentResponse()


# Mount Starlette SSE application after defining more specific routes
app.mount("/mcp", mcp_sse_app)
