"""CLI-boundary handlers shared across cli.py / cli_image.py / cli_video.py.

Top-level file (NOT a ``cli/`` package) to avoid file/package collision with
the existing ``cli.py``. Per Phase 4 modular-monolith rule (spec C8).

Three exports:

* :func:`_handle_gflow_error` -- catches :class:`gflow_cli.errors.GFlowError`
  (and subclasses), emits a structured ``error_raised`` event with the
  RFC 9457 Problem Details payload, prints a Rich-formatted user-facing
  message + remediation hint, and returns the exit code mapped via
  :data:`gflow_cli.errors.EXIT_CODE_MAP`.
* :func:`_handle_unhandled_error` -- catches everything else. Privacy-safe:
  hashes the exception message + stack with SHA-256 and emits an
  ``error_unhandled`` event. Never logs the raw message or full stack.
* :func:`run_with_handlers` -- wraps an ``asyncio.run(coro)`` call in the two
  handlers above plus a ``KeyboardInterrupt`` / :class:`click.Abort` handler
  that exits with 130 (the conventional SIGINT exit code).

Observability bootstrap (T5) is not yet shipped. Both handlers therefore
import ``gflow_cli.observability`` lazily inside the function body; when the
import fails they fall back to building the structured event in-line via
the stdlib ``structlog`` logger. Tests exercise the fallback path until
T5 lands.
"""

from __future__ import annotations

import hashlib
import sys
import traceback
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import click
import structlog
from rich.console import Console

from gflow_cli.errors import (
    EXIT_CODE_MAP,
    ContentPolicyError,
    GFlowError,
    WireFormatError,
)

_logger = structlog.get_logger(__name__)
_console = Console()


def _exit_code_for(exc: GFlowError) -> int:
    """Return the exit code for *exc* via isinstance-walk on ``EXIT_CODE_MAP``.

    Iteration order matters: subclasses inherit the parent class's code only
    if they don't have their own entry, and the dict is ordered
    most-specific-first (see ``errors.py``).
    """
    for cls, code in EXIT_CODE_MAP.items():
        if isinstance(exc, cls):
            return code
    return 1


def _emit_error_raised_fallback(exc: GFlowError, *, cli_command: str) -> None:
    """In-line fallback emitter used until T5 ships ``observability.py``.

    Builds the same payload the future ``emit_error_event`` helper will
    build: ``error_class``, RFC 9457 ``problem`` dict, ``cli_command``,
    ``correlation_id``, plus class-specific extensions (``upstream_status``
    for :class:`ContentPolicyError`, ``discovery`` for
    :class:`WireFormatError`).
    """
    correlation_id = exc.instance or f"gflow:error:{uuid.uuid4()}"
    extras: dict[str, Any] = {}
    if isinstance(exc, ContentPolicyError):
        # Flow returns HTTP 200 for content rejections; RFC 9457 forbids 2xx
        # on the Problem Details status field, so we surface the raw upstream
        # status only as an event extension (see ContentPolicyError docstring).
        extras["upstream_status"] = 200
    if isinstance(exc, WireFormatError):
        extras["discovery"] = exc.discovery
    _logger.error(
        "error_raised",
        error_class=type(exc).__name__,
        problem=exc.to_problem_details(),
        cli_command=cli_command,
        correlation_id=correlation_id,
        **extras,
    )


def _handle_gflow_error(exc: GFlowError, *, cli_command: str) -> int:
    """Print user-facing message + remediation, emit ``error_raised`` event,
    return exit code.

    Lazy-imports :mod:`gflow_cli.observability` so this module is usable even
    before T5 lands. On ``ImportError`` we fall back to the in-line emitter.
    """
    try:
        from gflow_cli.observability import emit_error_event  # type: ignore[import-not-found]

        emit_error_event(_logger, exc, cli_command=cli_command)
    except ImportError:
        _emit_error_raised_fallback(exc, cli_command=cli_command)
    _console.print(f"[red]{exc.title}:[/red] {exc.detail or ''}")
    if exc.remediation_hint:
        _console.print(f"[yellow]-> {exc.remediation_hint}[/yellow]")
    return _exit_code_for(exc)


def _handle_unhandled_error(exc: BaseException, *, cli_command: str) -> int:
    """Catch-all for non-:class:`GFlowError`. Privacy-safe: hashes message + stack,
    never logs raw payload. Always returns exit code 1.
    """
    try:
        from gflow_cli.observability import emit_unhandled_event  # type: ignore[import-not-found]

        emit_unhandled_event(_logger, exc, cli_command=cli_command)
    except ImportError:
        message_hash = hashlib.sha256(str(exc).encode("utf-8", "replace")).hexdigest()
        stack_hash = hashlib.sha256(
            "".join(traceback.format_tb(exc.__traceback__)).encode("utf-8", "replace")
        ).hexdigest()
        _logger.error(
            "error_unhandled",
            exception_class=type(exc).__name__,
            message_hash=message_hash,
            stack_hash=stack_hash,
            cli_command=cli_command,
        )
    _console.print(
        "[red]Unexpected error.[/red] Re-run with --verbose to capture details. "
        "If this persists, file a bug at https://github.com/ffroliva/gflow-cli/issues."
    )
    return 1


def run_with_handlers(
    coro_factory: Callable[[], Coroutine[Any, Any, Any]],
    *,
    cli_command: str,
) -> None:
    """Wrap an :func:`asyncio.run` call in the GFlowError + unhandled handlers.

    The factory pattern (``lambda: _run_t2i(...)``) is intentional: it defers
    coroutine creation until we're inside the try/except so any exception
    raised at construction time (e.g. by an eager validator) also hits the
    handler.

    Usage in a Click command body::

        run_with_handlers(
            lambda: _run_t2i(prompt, ...),
            cli_command="image t2i",
        )
    """
    import asyncio

    try:
        asyncio.run(coro_factory())
    except GFlowError as e:
        sys.exit(_handle_gflow_error(e, cli_command=cli_command))
    except (KeyboardInterrupt, click.Abort):
        sys.exit(130)
    except BaseException as e:  # noqa: BLE001 — intentional catch-all at the CLI boundary
        sys.exit(_handle_unhandled_error(e, cli_command=cli_command))
