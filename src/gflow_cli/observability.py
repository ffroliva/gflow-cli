"""structlog bootstrap + structured error event emitters.

Public API:
    * :func:`configure_logging` — bootstrap structlog. TTY auto-detection
      decides between text (human) and JSON (machine-parseable) renderers
      when ``LogFormat.AUTO`` is selected. Mounts an
      :class:`structlog.processors.ExceptionRenderer` configured with
      ``show_locals=False`` so frame-local variables (which can contain
      auth tokens, signed URLs, ...) NEVER reach the wire.
    * :func:`emit_error_event` — structured ``error_raised`` event for any
      caught :class:`gflow_cli.errors.GFlowError`. Stable fields:
      ``error_class``, ``problem`` (RFC 9457 Problem Details), ``cli_command``.
      ``correlation_id`` and ``cli_version`` flow via ``contextvars`` (bound at
      the process boundary in :mod:`gflow_cli.cli`).
    * :func:`emit_unhandled_event` — privacy-safe ``error_unhandled`` event
      for any non-GFlowError. Hashes the message and stack with SHA-256 so
      log aggregators can group recurring errors without storing the raw
      payload (which could include PII or tokens).

This module is part of the Phase 4 modular monolith — keep it a single
top-level file under ``src/gflow_cli/`` per spec C8. Public exports are
imported directly from :mod:`gflow_cli.observability` (no submodules).
"""

from __future__ import annotations

import hashlib
import logging
import sys
import traceback
from typing import Any

import structlog

from gflow_cli.config import LogFormat
from gflow_cli.errors import ContentPolicyError, GFlowError, WireFormatError

__all__ = [
    "configure_logging",
    "emit_error_event",
    "emit_unhandled_event",
]


def configure_logging(log_format: LogFormat = LogFormat.AUTO) -> None:
    """Bootstrap structlog.

    Renders text on a TTY, JSON when stdout is piped (``LogFormat.AUTO``).
    Sets ``show_locals=False`` on the exception renderer so frame locals
    (which can contain auth tokens or signed URLs) are NEVER serialized.

    The implementation uses the explicit
    :class:`structlog.processors.ExceptionRenderer` +
    :class:`structlog.tracebacks.ExceptionDictTransformer` pair rather than
    the simpler ``format_exc_info`` processor. ``format_exc_info`` does NOT
    accept a ``show_locals`` flag; its default behaviour happens to omit
    locals today, but a future maintainer who swaps in
    ``RichTracebackFormatter(show_locals=True)`` could silently leak frame
    locals (auth tokens). The explicit form makes the privacy contract
    visible at the call site.
    """
    if log_format == LogFormat.AUTO:
        is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
        log_format = LogFormat.TEXT if is_tty else LogFormat.JSON

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    exc_renderer = structlog.processors.ExceptionRenderer(
        structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
    )
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        exc_renderer,
    ]
    renderer: Any
    if log_format == LogFormat.TEXT:
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()
    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


def emit_error_event(
    logger: Any,
    exc: GFlowError,
    *,
    cli_command: str,
) -> None:
    """Emit ``error_raised`` for a caught :class:`GFlowError`.

    Stable fields:

    * ``error_class`` — concrete subclass name (``AuthExpiredError``, ...)
    * ``problem`` — RFC 9457 Problem Details dict (see
      :meth:`GFlowError.to_problem_details`)
    * ``cli_command`` — the subcommand path (``"image t2i"``, ``"video i2v"``)

    ``correlation_id`` and ``cli_version`` flow from ``contextvars`` bound
    at the process boundary in :mod:`gflow_cli.cli`.

    Per-class extensions:

    * :class:`ContentPolicyError` → ``upstream_status=200``. Flow returns
      HTTP 200 with empty ``media[]`` for content rejections; RFC 9457
      forbids a 2xx ``status`` on a Problem Details payload, so we surface
      the literal upstream status only as an event extension.
    * :class:`WireFormatError` → ``discovery`` payload (``route_name``,
      ``http_status``, ``content_type``, ``top_level_keys``, ...). Lets
      ``grep error_class=WireFormatError`` reveal what was unexpected.
    """
    payload: dict[str, Any] = {
        "error_class": type(exc).__name__,
        "problem": exc.to_problem_details(),
        "cli_command": cli_command,
    }
    if isinstance(exc, ContentPolicyError):
        payload["upstream_status"] = 200
    if isinstance(exc, WireFormatError):
        payload["discovery"] = exc.discovery
    logger.error("error_raised", **payload)


def emit_unhandled_event(
    logger: Any,
    exc: BaseException,
    *,
    cli_command: str,
) -> None:
    """Emit ``error_unhandled`` for non-:class:`GFlowError`. Privacy-safe.

    NEVER includes the raw exception message or formatted traceback — only
    SHA-256 hashes. Aggregators can group recurring errors by hash without
    storing PII or tokens that may have ended up in ``str(exc)`` or a frame
    local.
    """
    msg_bytes = str(exc).encode("utf-8", errors="replace")
    tb_bytes = "".join(traceback.format_tb(exc.__traceback__)).encode("utf-8", errors="replace")
    logger.error(
        "error_unhandled",
        exception_class=type(exc).__name__,
        message_hash=hashlib.sha256(msg_bytes).hexdigest(),
        stack_hash=hashlib.sha256(tb_bytes).hexdigest(),
        cli_command=cli_command,
    )
