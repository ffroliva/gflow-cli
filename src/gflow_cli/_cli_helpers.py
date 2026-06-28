"""CLI-boundary handlers + profile/provider helpers shared across cli.py /
cli_image.py / cli_video.py.

Top-level file (NOT a ``cli/`` package) to avoid file/package collision with
the existing ``cli.py``. Per Phase 4 modular-monolith rule (spec C8).

Exports:

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
* :func:`_resolve_profile` -- normalize ``--profile`` flag against the profile
  precedence chain (CLI flag > ``GFLOW_CLI_PROFILE`` env > config.toml default
  > single discovered profile). Relocated from cli_image.py / cli_video.py in
  T4b; the negative-import test in ``tests/cli/test_helpers.py`` enforces no
  drift back into the call-site modules.
* :func:`_make_provider_dir` -- return the on-disk Playwright profile dir for
  a given profile name, or exit 2 if the user hasn't run ``gflow auth login``
  yet. Also relocated in T4b.

T5 (this commit) shipped :mod:`gflow_cli.observability`, so the lazy-import
fallback that T4a carried while observability was a forward reference has
been removed. Both error handlers now import
:func:`gflow_cli.observability.emit_error_event` /
:func:`gflow_cli.observability.emit_unhandled_event` at module load time.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import click
import structlog
from rich.console import Console

from gflow_cli import auth as auth_mod
from gflow_cli import json_output, profile_store
from gflow_cli.errors import (
    EXIT_CODE_MAP,
    GFlowError,
)
from gflow_cli.observability import emit_error_event, emit_unhandled_event
from gflow_cli.tools.runtime import apply_tool

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from gflow_cli.tools.invocation import AppliedTool

_logger = structlog.get_logger(__name__)
_console = Console()

# Explicit re-export list. The underscore prefix on ``_resolve_profile`` and
# ``_make_provider_dir`` (and the two error handlers) is historical — they
# were module-private helpers in cli_image.py / cli_video.py before T4b — and
# the negative-import test in ``tests/cli/test_helpers.py`` pins those exact
# names. Listing them in ``__all__`` tells pyright (`reportPrivateUsage`)
# that despite the leading underscore these are part of this module's
# documented API and cross-module imports are sanctioned. Pyright's
# `reportPrivateUsage` rule does NOT honor the PEP 484 `from X import Y as Y`
# re-export idiom for underscore names, so __all__ is the practical fix.
__all__ = [
    "_handle_gflow_error",
    "_handle_unhandled_error",
    "_make_provider_dir",
    "_resolve_profile",
    "apply_tool_option",
    "run_with_handlers",
    "safe_path_text",
]


def _parse_tool_spec(spec: str) -> tuple[str, dict[str, str]]:
    name, _, raw_opts = spec.partition(":")
    options: dict[str, str] = {}
    if raw_opts:
        for pair in raw_opts.split(","):
            k, _, v = pair.partition("=")
            options[k.strip()] = v.strip()
    return name.strip(), options


def apply_tool_option(
    text: str,
    tool_specs: tuple[str, ...],
    *,
    category: Literal["image", "video"],
    quiet: bool,
) -> tuple[str, str | None, AppliedTool | None]:
    """Apply ``--tool name[:k=v]`` specs in sequence. Returns ``(prompt_to_send,
    original_prompt|None, applied_tool|None)``. Unknown tool / wrong category →
    UsageError (pre-network).

    ``applied_tool`` is the provenance snapshot of the LAST tool that rewrote the
    prompt — recorded in ``operations.metadata_json.tool``. It is built from the
    resolved spec + options (not the tool's output), so it stays accurate even
    under a monkeypatched ``apply_tool`` in tests. ``apply_tool`` is imported at
    MODULE scope so tests can monkeypatch it.
    """
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.tools.invocation import applied_tool_from_spec
    from gflow_cli.tools.registry import get_tool

    if not tool_specs:
        return text, None, None
    original = text
    current = text
    changed = False
    applied: AppliedTool | None = None
    for spec_str in tool_specs:
        name, options = _parse_tool_spec(spec_str)
        try:
            spec = get_tool(name)
        except ConfigurationError as exc:
            raise click.UsageError(str(exc)) from exc
        if not spec.supports(category):
            raise click.UsageError(f"tool {name!r} does not support {category} generation.")
        # Validate option keys against the tool's declared schema.
        if options:
            unknown_keys = sorted(set(options) - set(spec.options_schema))
            if unknown_keys:
                valid = sorted(spec.options_schema)
                raise click.UsageError(
                    f"tool {name!r}: unknown option(s) {unknown_keys!r}; valid options: {valid!r}"
                )
        # Validate the style value when provided — must match a declared domain
        # for THIS generation category (an image style is invalid on video, etc).
        style_val = options.get("style")
        if style_val is not None and spec.config.domain(style_val, category) is None:
            valid_styles = sorted(
                d.name for d in spec.config.domains if d.category in (category, "both")
            )
            raise click.UsageError(
                f"tool {name!r}: unknown {category} style {style_val!r}; "
                f"valid {category} styles: {valid_styles!r}"
            )
        result = apply_tool(spec, current, options, category=category)
        if result.was_expanded:
            changed = True
            current = result.expanded
            applied = applied_tool_from_spec(spec, options)
            if not quiet:
                _console.print(f"[cyan]{spec.title} applied:[/cyan] [dim]{current}[/dim]")
        elif not quiet:
            _console.print(f"[yellow]{spec.title} skipped[/yellow] (unavailable).")
    return current, (original if changed else None), applied


def safe_path_text(path: Path) -> str:
    """Format a path for terminal display, avoiding absolute path leaks.

    Tries to:
    1. Make path relative to CWD if it's underneath.
    2. Replace Path.home() with '~' if it's underneath.
    3. Fall back to str(path) but warn in review that this might leak.
    """
    try:
        # 1. CWD relative
        cwd = Path.cwd().resolve()
        p_resolved = path.resolve()
        if p_resolved.is_relative_to(cwd):
            return str(p_resolved.relative_to(cwd))

        # 2. Home relative
        home = Path.home().resolve()
        if p_resolved.is_relative_to(home):
            # Using forward slashes for cross-platform feel in ~ paths
            rel = p_resolved.relative_to(home)
            return "~/" + str(rel).replace("\\", "/")

        # 3. If it's still absolute, and we're on Windows, check if it's in a drive root
        # that isn't the home drive, or just return the resolved path.
        return str(p_resolved)
    except (ValueError, RuntimeError, OSError):
        # Fallback for any resolution error (e.g. invalid chars on Win)
        return str(path)


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


def _handle_gflow_error(exc: GFlowError, *, cli_command: str) -> int:
    """Print user-facing message + remediation, emit ``error_raised`` event,
    return exit code.
    """
    emit_error_event(_logger, exc, cli_command=cli_command)
    _console.print(f"[red]{exc.title}:[/red] {exc.detail or ''}")
    if exc.remediation_hint:
        _console.print(f"[yellow]-> {exc.remediation_hint}[/yellow]")
    return _exit_code_for(exc)


def _handle_unhandled_error(exc: BaseException, *, cli_command: str) -> int:
    """Catch-all for non-:class:`GFlowError`. Privacy-safe: hashes message + stack,
    never logs raw payload. Always returns exit code 1.
    """
    emit_unhandled_event(_logger, exc, cli_command=cli_command)
    _console.print(
        "[red]Unexpected error.[/red] Re-run with --verbose to capture details. "
        "If this persists, file a bug at https://github.com/ffroliva/gflow-cli/issues.",
    )
    return 1


def run_with_handlers(
    coro_factory: Callable[[], Coroutine[Any, Any, Any]],
    *,
    cli_command: str,
    as_json: bool = False,
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

    When ``as_json`` is set the error path emits a machine-readable JSON
    payload on stdout instead of the Rich message (the observability event
    still fires either way) so a programmatic caller gets a parseable failure
    with the same exit code. The success payload is the coroutine's own
    responsibility — it knows the result shape.
    """
    import asyncio

    try:
        asyncio.run(coro_factory())
    except GFlowError as e:
        if as_json:
            emit_error_event(_logger, e, cli_command=cli_command)
            json_output.emit(json_output.error_payload(e))
            sys.exit(_exit_code_for(e))
        sys.exit(_handle_gflow_error(e, cli_command=cli_command))
    except (KeyboardInterrupt, click.Abort):
        sys.exit(130)
    except SystemExit:
        # A coroutine that has already taken responsibility for its own exit
        # (e.g. `video t2v --json` emits the failed `video_result` payload and
        # then raises SystemExit(1) so we don't print a second "Saved:" / Rich
        # error). Propagate the exit code without emitting a SECOND JSON
        # `UnexpectedError` payload through the catch-all below — that would
        # leave stdout with two concatenated documents that no `json.loads`
        # call can parse, defeating the whole point of --json.
        raise
    except BaseException as e:
        if as_json:
            emit_unhandled_event(_logger, e, cli_command=cli_command)
            json_output.emit(json_output.unexpected_payload())
            sys.exit(1)
        sys.exit(_handle_unhandled_error(e, cli_command=cli_command))


# ---------------------------------------------------------------------------
# Profile / provider helpers (relocated from cli_image.py + cli_video.py in T4b)
# ---------------------------------------------------------------------------


def _resolve_profile(profile: str | None) -> str:
    """Return the active profile name or exit with a friendly message.

    Precedence (delegated to :func:`profile_store.resolve_profile`):

    1. *profile* argument (the CLI ``--profile`` flag) if truthy
    2. ``GFLOW_CLI_PROFILE`` env var
    3. ``config.toml`` default under ``$GFLOW_CLI_HOME``
    4. single discovered ``profile_*`` directory under ``$GFLOW_CLI_HOME``
    5. raise — surfaced to the user as a yellow Rich message and exit 2
    """
    if profile:
        return profile
    try:
        return profile_store.resolve_profile(None)
    except profile_store.NoProfilesError as exc:
        _console.print(f"[yellow]{exc}[/yellow]")
        sys.exit(2)
    except profile_store.NoDefaultProfileError as exc:
        _console.print(f"[yellow]{exc}[/yellow]")
        sys.exit(2)


def _make_provider_dir(profile_name: str) -> Path:
    """Return the Playwright profile dir for *profile_name*, or exit if absent.

    Does NOT create the directory — that's :func:`gflow_cli.auth.login`'s
    responsibility. If the dir is missing the user is prompted to run
    ``gflow auth login`` and the process exits with code 2.
    """
    pdir = auth_mod.profile_dir(profile_name)
    if not pdir.exists():
        _console.print(
            f"[red]No session for profile '{profile_name}'.[/red] "
            "Run [bold]gflow auth login[/bold] first.",
        )
        sys.exit(2)
    return pdir
