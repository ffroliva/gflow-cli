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
import sys
import traceback
from typing import TYPE_CHECKING, Any

import structlog

from gflow_cli.config import LogFormat
from gflow_cli.errors import ContentPolicyError, GFlowError, WireFormatError

if TYPE_CHECKING:
    from structlog.types import FilteringBoundLogger

# Numeric stdlib-logging level constants, mirrored here so observability.py and
# cli.py do NOT need to `import logging` solely for one constant each. Source
# of truth: Python's logging module (logging.DEBUG == 10, logging.INFO == 20).
# Single point of definition prevents the half-and-half state the council
# flagged (cli.py inlined 10, observability.py imported logging.INFO).
DEBUG_LEVEL: int = 10
INFO_LEVEL: int = 20

__all__ = [
    "DEBUG_LEVEL",
    "INFO_LEVEL",
    "configure_logging",
    "emit_error_event",
    "emit_unhandled_event",
    "resolves_to_json",
]


def resolves_to_json(fmt: LogFormat) -> bool:
    """True when *fmt* resolves to the JSON renderer.

    ``LogFormat.AUTO`` renders TEXT only on a stderr TTY (logs go to stderr).
    Single point of truth for the AUTO resolution — shared by
    :func:`configure_logging` and ``cli_data._logs_emit_json``.
    """
    if fmt == LogFormat.AUTO:
        return not bool(getattr(sys.stderr, "isatty", lambda: False)())
    return fmt == LogFormat.JSON


def configure_logging(log_format: LogFormat = LogFormat.AUTO) -> None:
    """Bootstrap structlog.

    Logs are written to **stderr** (stdout is reserved for command output such
    as ``--json`` payloads, so a consumer can ``json.loads(stdout)`` cleanly).
    Renders text on a TTY, JSON when stderr is piped (``LogFormat.AUTO``).
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
    log_format = LogFormat.JSON if resolves_to_json(log_format) else LogFormat.TEXT

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    exc_renderer = structlog.processors.ExceptionRenderer(
        structlog.tracebacks.ExceptionDictTransformer(show_locals=False),
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
        wrapper_class=structlog.make_filtering_bound_logger(INFO_LEVEL),
        # Route to stderr: the default PrintLoggerFactory writes to stdout,
        # which corrupts `--json` output (logs interleave with the payload).
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )


def emit_error_event(
    logger: FilteringBoundLogger,
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

    * :class:`ContentPolicyError` → ``upstream_status``. Flow returns
      HTTP 200 with empty ``media[]`` for content rejections, or HTTP 400
      with a content-safety reason (``PUBLIC_ERROR_UNSAFE_GENERATION`` et
      al.). RFC 9457 forbids a 2xx ``status`` on a Problem Details payload,
      so we surface the literal upstream status only as an event extension.
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
        payload["upstream_status"] = exc.status if exc.status is not None else 200
    if isinstance(exc, WireFormatError):
        payload["discovery"] = exc.discovery
    logger.error("error_raised", **payload)


def emit_unhandled_event(
    logger: FilteringBoundLogger,
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
    tb_bytes = "".join(traceback.format_tb(exc.__traceback__)).encode("utf-8", errors="replace")
    logger.error(
        "error_unhandled",
        exception_class=type(exc).__name__,
        message_hash=exception_message_hash(exc),
        stack_hash=hashlib.sha256(tb_bytes).hexdigest(),
        cli_command=cli_command,
    )


def exception_message_hash(exc: BaseException) -> str:
    """SHA-256 hex digest of ``str(exc)`` — the privacy-safe stand-in for a
    message that may carry PII or tokens. Shared by ``emit_unhandled_event``
    and the data layer's FAILED-operation recorder (#341) so log events and
    catalog rows hash identically and can be correlated.
    """
    return hashlib.sha256(str(exc).encode("utf-8", errors="replace")).hexdigest()
