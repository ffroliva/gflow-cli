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

import copy
import functools
import inspect
import json
import re
import sys
import traceback
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import click
import structlog
from click.core import ParameterSource
from rich.console import Console
from rich.markup import escape

from gflow_cli import auth as auth_mod
from gflow_cli import json_output, profile_store
from gflow_cli.config import get_settings
from gflow_cli.errors import (
    GFlowError,
    exit_code_for,
)
from gflow_cli.observability import emit_error_event, emit_unhandled_event
from gflow_cli.tools.runtime import apply_tool

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator

    from gflow_cli.tools.invocation import AppliedTool

_logger = structlog.get_logger(__name__)
_CommandT = TypeVar("_CommandT", bound=click.Command)
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
    "OutputFormat",
    "OutputFormatOption",
    "OutputFormatType",
    "VALID_OUTPUT_FORMATS",
    "UpscaleLegacyShortOption",
    "_FLOW_ID_RE",
    "_handle_gflow_error",
    "_handle_unhandled_error",
    "_make_provider_dir",
    "_resolve_profile",
    "_validate_project_id",
    "apply_tool_option",
    "BridgeCommand",
    "bridge_params",
    "cli_leaves",
    "command_tree",
    "help_json_path",
    "default_project",
    "out_file_option",
    "output_format_option",
    "run_with_handlers",
    "safe_path_text",
    "slugify_project_name",
    "run_bridged",
    "spends_credits",
    "spends_credits_decision",
    "tool_option",
    "upscale_out_dir_option",
]

# Flow project ids are interpolated into navigation URLs and CSS selectors
# (page.goto, `data-tile-id='fe_id_<id>'`) before any other guard runs, so the
# `--project` option on both `image t2i`/`i2i` and `video t2v`/`i2v`/`r2v`
# validates against this allowlist at the CLI boundary. Mirrors
# `routes._PROJECT_ID_RE`. Relocated here (T4b follow-up) so the two call-site
# modules can't drift out of sync.
_FLOW_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,128}$")


class OutputFormat(StrEnum):
    """Supported machine-readable and tabular output formats (#1158)."""

    JSON = "json"
    TABLE = "table"
    ID = "id"


VALID_OUTPUT_FORMATS: frozenset[str] = frozenset({f.value for f in OutputFormat})


class OutputFormatOption(click.Option):
    """Marks an option as selecting the output format (json|table|id) (#1158)."""


class OutputFormatType(click.Choice[str]):
    """ParamType converting -o/--output values to lowercase and enforcing remediation."""

    name = "format"

    def __init__(
        self,
        *,
        allow_id: bool = True,
        has_out_file: bool = False,
        is_upscale_out: bool = False,
        valid_formats: frozenset[str] | None = None,
    ) -> None:
        self.allow_id = allow_id
        self.has_out_file = has_out_file
        self.is_upscale_out = is_upscale_out
        if valid_formats is not None:
            self.valid_formats = valid_formats
        elif allow_id:
            self.valid_formats = VALID_OUTPUT_FORMATS
        else:
            self.valid_formats = frozenset({OutputFormat.JSON.value, OutputFormat.TABLE.value})
        choices_list: list[str] = sorted(self.valid_formats)
        super().__init__(choices=choices_list, case_sensitive=False)

    def convert(self, value: Any, param: click.Parameter | None, ctx: click.Context | None) -> str:
        if not isinstance(value, str):
            return str(value)
        val_lower = value.lower()
        if val_lower in self.valid_formats:
            return val_lower
        if self.has_out_file:
            raise click.UsageError(
                f"--output now selects the format; use --out-file {value}", ctx=ctx
            )
        if self.is_upscale_out:
            raise click.UsageError(f"use --out {value}", ctx=ctx)
        choices = ", ".join(repr(f) for f in sorted(self.valid_formats))
        raise click.UsageError(
            f"Invalid value for '-o' / '--output': '{value}' is not one of {choices}.", ctx=ctx
        )


def _resolve_chosen_format(kwargs: dict[str, Any], dest: str, default: str) -> str:
    json_flag: bool = kwargs.pop("json_alias", False)
    fmt: str | None = kwargs.get(dest)
    if json_flag:
        if fmt is not None and fmt != "json":
            raise click.UsageError("Cannot combine --json with -o/--output")
        return "json"
    return default if fmt is None else fmt


def _sync_format_args(
    kwargs: dict[str, Any],
    chosen_fmt: str,
    dest: str,
    takes_dest: bool,
    takes_as_json: bool,
) -> None:
    if takes_dest:
        kwargs[dest] = chosen_fmt
    elif dest in kwargs:
        kwargs.pop(dest)

    if takes_as_json and "as_json" not in kwargs:
        kwargs["as_json"] = chosen_fmt == "json"

    current_ctx = click.get_current_context(silent=True)
    if current_ctx is not None:
        current_ctx.params[dest] = chosen_fmt
        current_ctx.params["as_json"] = chosen_fmt == "json"


def output_format_option(
    dest: str = "output_format",
    *,
    default: str = "table",
    allow_id: bool = True,
    has_out_file: bool = False,
    is_upscale_out: bool = False,
    help: str | None = None,
) -> Callable[[Any], Any]:
    """Decorator adding ``-o/--output json|table|id`` and ``--json`` alias (#1158)."""
    if help is None:
        help = "Output format (json|table|id)." if allow_id else "Output format (json|table)."
    fmt_type = OutputFormatType(
        allow_id=allow_id,
        has_out_file=has_out_file,
        is_upscale_out=is_upscale_out,
    )

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(f)
        takes_dest = dest in sig.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        takes_as_json = "as_json" in sig.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )

        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            chosen_fmt = _resolve_chosen_format(kwargs, dest, default)
            _sync_format_args(kwargs, chosen_fmt, dest, takes_dest, takes_as_json)
            return f(*args, **kwargs)

        with_json = click.option(
            "--json",
            "json_alias",
            is_flag=True,
            default=False,
            help="Machine-readable JSON (alias for -o json).",
        )(wrapper)
        with_format = click.option(
            "-o",
            "--output",
            dest,
            type=fmt_type,
            default=None,
            help=help,
            cls=OutputFormatOption,
        )(with_json)
        return with_format

    return decorator


def out_file_option(
    dest: str = "output_file",
    *,
    help: str,
) -> Callable[[Any], Any]:
    """Decorator adding ``--out-file PATH`` (#1158)."""
    return click.option(
        "--out-file",
        dest,
        type=click.Path(dir_okay=False, path_type=Path),
        default=None,
        help=help,
    )


class UpscaleLegacyShortOption(click.Option):
    """Rejects legacy `-o DIR` on upscale with remediation (#1158)."""

    def process_value(self, ctx: click.Context, value: Any) -> Any:
        if value is not None:
            raise click.UsageError(f"use --out {value}", ctx=ctx)
        return None


def upscale_out_dir_option(
    dest: str = "out_dir",
    *,
    help: str = "Output directory (defaults to the configured gflow output dir).",
) -> Callable[[Any], Any]:
    """Decorator adding ``--out DIR`` and rejecting deprecated short form ``-o DIR`` (#1158)."""

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        with_legacy = click.option(
            "-o",
            f"{dest}_deprecated",
            type=click.UNPROCESSED,
            default=None,
            expose_value=False,
            hidden=True,
            cls=UpscaleLegacyShortOption,
        )(f)
        return click.option(
            "--out",
            dest,
            type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
            default=None,
            help=help,
        )(with_legacy)

    return decorator


def _validate_project_id(
    _ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    """Reject a --project id that isn't alphanumeric/hyphen (1-128 chars)."""
    if value is not None and not _FLOW_ID_RE.fullmatch(value):
        msg = "project id must be 1-128 chars of letters, digits, or hyphens."
        raise click.BadParameter(msg, param=param)
    return value


def default_project(project_id: str | None, project_name: str | None, profile: str) -> str | None:
    """The project a generation runs in: ``--project``, else the ``GFLOW_CLI_PROJECT``
    pin unless ``--project-name`` asked for a new project (#1039). ``None`` = create one.

    ``GFLOW_CLI_PROJECT_NAME`` names the pinned project, so a ``--project-name`` that came
    from that variable (not the command line) does not ask for a new one. Project ids
    are account-scoped and the pin is not, so a pin the catalog records only under other
    profiles is refused before any browser starts.
    """
    settings = get_settings()
    ctx = click.get_current_context(silent=True)
    if ctx is not None and ctx.get_parameter_source("project_name") == ParameterSource.ENVIRONMENT:
        project_name = None
    pinned = settings.project
    if project_id is not None or project_name is not None or pinned is None:
        return project_id
    from gflow_cli.data.repository import DataRepository
    from gflow_cli.data.store import DataStore
    from gflow_cli.errors import DataStoreError

    owner = None
    try:
        with DataStore.open(settings.resolved_db_path()) as store:
            repo = DataRepository(store)
            if repo.get_project(profile, pinned) is None:
                owner = repo.get_project(None, pinned)
    except DataStoreError:
        pass  # an unreadable catalog never blocks generation
    if owner is not None:
        msg = (
            f"GFLOW_CLI_PROJECT={pinned} is a project of profile {owner.profile_name!r}, "
            f"not {profile!r}. Pass --project or --project-name, or change the setting."
        )
        raise click.UsageError(msg)
    click.echo(f"Using the default project {pinned} (GFLOW_CLI_PROJECT).", err=True)
    return pinned


def slugify_project_name(prompt: str | None, prefix: str = "gflow") -> str:
    """Derive a smart, URL-safe project title slug from prompt text.

    Strips special characters, lowercases, replaces spaces/hyphens with single dashes,
    caps length at 40 characters (removing trailing dashes), and defaults to
    ``f"{prefix}-project"`` if prompt is empty, whitespace, or contains no
    alphanumeric characters.
    """
    if not prompt or not prompt.strip():
        return f"{prefix}-project"

    text = prompt.lower()
    # Strip special characters, keeping letters, digits, spaces, and hyphens.
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    # Replace whitespace and repeated hyphens with a single dash.
    slug = re.sub(r"[\s-]+", "-", text).strip("-")
    if not slug:
        return f"{prefix}-project"

    slug = slug[:40].rstrip("-")
    return slug or f"{prefix}-project"


_TOOL_OPTION_HELP = (
    "Apply a prompt tool before generating, e.g. --tool creative-director or "
    "--tool creative-director:style=cinema. Repeatable. Needs an OpenAI-compatible "
    "endpoint via GFLOW_CLI_LLM_API_KEY and/or GFLOW_CLI_LLM_BASE_URL; falls back "
    "to the original prompt if unconfigured or on error."
)


def tool_option(func: Callable[..., Any]) -> Callable[..., Any]:
    """Reusable ``-t/--tool`` Click option (DRY across t2i/i2i/t2v/i2v/r2v/chain).

    Binds to the ``tool_specs: tuple[str, ...]`` parameter. One definition keeps
    the help text and flag spelling identical on every generation command.
    """
    return click.option(
        "-t",
        "--tool",
        "tool_specs",
        multiple=True,
        help=_TOOL_OPTION_HELP,
    )(func)


_SPENDS_CREDITS_ATTR = "gflow_spends_credits"


def spends_credits(value: bool) -> Callable[[_CommandT], _CommandT]:
    """Record whether a leaf command submits a Flow generation (#1203).

    Goes OUTERMOST, above ``@<group>.command(...)``, so it marks the Command object.
    True means a new generation is submitted: a video spends Veo credits, an image spends
    the daily image quota. Every leaf needs a decision; ``tests/cli/test_help_json.py``
    fails on an undecorated one.
    """

    def mark(cmd: _CommandT) -> _CommandT:
        setattr(cmd, _SPENDS_CREDITS_ATTR, value)
        return cmd

    return mark


def spends_credits_decision(cmd: click.Command) -> bool | None:
    """The ``@spends_credits`` value of *cmd*, or None when it was never decided."""
    return getattr(cmd, _SPENDS_CREDITS_ATTR, None)


def cli_leaves(
    group: click.Group, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], click.Command, list[str]]]:
    """Yield ``(path, command, aliases)`` for every runnable command under *group*.

    That is every plain command plus every ``invoke_without_command`` group (bare
    ``gflow auth`` runs). Hidden commands and groups are skipped. A command registered
    under several names in one group yields once, under its first name; the other names
    are its aliases.
    """
    names: dict[int, list[str]] = {}
    for name, cmd in group.commands.items():
        names.setdefault(id(cmd), []).append(name)
    for first, *aliases in names.values():
        cmd = group.commands[first]
        if cmd.hidden:
            continue
        if not isinstance(cmd, click.Group) or cmd.invoke_without_command:
            yield (*path, first), cmd, aliases
        if isinstance(cmd, click.Group):
            yield from cli_leaves(cmd, (*path, first))


def _jsonable(value: Any) -> Any:
    if callable(value):
        return None
    return json.loads(json.dumps(value, default=str))  # Path, tuple -> JSON-native


def _param_json(param: click.Parameter) -> dict[str, Any]:
    info = param.to_info_dict()
    is_option = isinstance(param, click.Option)
    long = [o for o in param.opts if o.startswith("--")]
    return {
        "name": (long or param.opts)[0] if is_option else param.human_readable_name,
        "opts": [*param.opts, *param.secondary_opts] if is_option else [],
        "type": "flag" if is_option and info["is_flag"] else param.type.name,
        "choices": _jsonable(info["type"].get("choices")),
        "multiple": param.multiple,
        "nargs": param.nargs,
        "default": _jsonable(info["default"]),
        "required": param.required,
        "help": getattr(param, "help", None),
    }


def command_tree(root: click.Group, prefix: tuple[str, ...] = ()) -> dict[str, Any]:
    """The ``--help --json`` payload: every runnable command at or under *prefix* (#1203)."""
    from gflow_cli import __version__

    return {
        "schema": 1,
        "name": "gflow",
        "version": __version__,
        "commands": [
            {
                "path": list(path),
                "summary": cmd.get_short_help_str(limit=300),
                "aliases": aliases,
                "params": [_param_json(p) for p in cmd.params if not getattr(p, "hidden", False)],
                "spends_credits": spends_credits_decision(cmd),
            }
            for path, cmd, aliases in cli_leaves(root)
            if path[: len(prefix)] == prefix
        ],
    }


# Parsed alongside each command's own options so a `--json` token is recognised where
# it is an option and not where another option consumes it as a value.
_HELP_JSON_FLAG = click.Option(["--json", "help_json"], is_flag=True, expose_value=False)


def help_json_path(root: click.Group, args: list[str]) -> tuple[str, ...] | None:
    """The command path a ``--help --json`` request names, or None when there is none.

    Resolution goes through Click's own parser and ``resolve_command``, level by level as
    ``click.shell_completion`` does, so option widths, option values and unknown commands
    are Click's, not a guess from the raw tokens.
    """
    cmd: click.Command = root
    ctx = click.Context(root, info_name="gflow", resilient_parsing=True)
    path: list[str] = []
    while True:
        parser = cmd.make_parser(ctx)
        _HELP_JSON_FLAG.add_to_parser(parser, ctx)
        opts, rest, _ = parser.parse_args(args=list(args))
        help_option = cmd.get_help_option(ctx)
        if help_option and help_option.name in opts and "help_json" in opts:
            return tuple(path)
        if not isinstance(cmd, click.Group) or not rest:
            return None
        name, sub, args = cmd.resolve_command(ctx, rest)
        if sub is None or name is None:
            return None
        cmd = sub
        path.append(sub.name or name)  # an alias resolves to the leaf's own name
        ctx = click.Context(sub, parent=ctx, info_name=name, resilient_parsing=True)


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
        else:
            _warn_tool_skipped(spec.title, quiet=quiet)
    return current, (original if changed else None), applied


#: Latch so a 50-prompt batch emits one notice, not fifty.
_tool_skip_notified = False


def _warn_tool_skipped(title: str, *, quiet: bool) -> None:
    """Tell the user a tool fell back, on stderr, at least once.

    The expander never raises, so a misconfigured endpoint is otherwise
    indistinguishable from success: the run exits 0, bills full credits, and
    silently uses the un-rewritten prompt. The batch and chain paths pass
    ``quiet=True``, which used to suppress this entirely -- so under quiet we
    still emit exactly one notice per process rather than none.
    """
    global _tool_skip_notified
    if quiet and _tool_skip_notified:
        return
    _tool_skip_notified = True
    click.secho(
        f"{title} skipped — the prompt was sent unchanged. Check "
        "GFLOW_CLI_LLM_BASE_URL / GFLOW_CLI_LLM_API_KEY, or run "
        '`gflow tools run <tool> "test" --json` to see why.',
        err=True,
        fg="yellow",
    )


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


def _handle_gflow_error(exc: GFlowError, *, cli_command: str, stderr: bool = False) -> int:
    """Print user-facing message + remediation, emit ``error_raised`` event,
    return exit code.
    """
    emit_error_event(_logger, exc, cli_command=cli_command)
    console = Console(stderr=True) if stderr else _console
    console.print(f"[red]{escape(exc.title)}:[/red] {escape(exc.detail or '')}")
    if exc.remediation_hint:
        console.print(f"[yellow]-> {escape(exc.remediation_hint)}[/yellow]")
    _print_incident_ref(exc.incident_ref, console=console)
    return exit_code_for(exc)


def _print_incident_ref(ref: Any, console: Console | None = None) -> None:
    """One incident sentence for both error paths (S04/S21); the report line
    keys on the ref's artifact tuple — the same source of truth as --json."""
    if ref is None or getattr(ref, "path", None) is None:
        return
    c = console or _console
    c.print(
        f"Incident bundle: {ref.path} — review before sharing; sensitive "
        "artifacts may contain account or media data.",
        markup=False,
    )
    if "report.md" in getattr(ref, "artifacts", ()):
        c.print(f"Pre-filled bug report: {ref.path / 'report.md'}", markup=False)


def _handle_unhandled_error(exc: BaseException, *, cli_command: str, stderr: bool = False) -> int:
    """Catch-all for non-:class:`GFlowError`. Privacy-safe by default: the
    console shows only a generic message (the structured telemetry event is
    always SHA-256-hashed regardless of this setting — see
    ``emit_unhandled_event``). Set GFLOW_CLI_DEBUG_TRACEBACK=1 to print the
    real exception + traceback to the console instead (local debugging only —
    the output may contain tokens/cookies). Always returns exit code 1.
    """
    emit_unhandled_event(_logger, exc, cli_command=cli_command)
    console = Console(stderr=True) if stderr else _console
    if get_settings().debug_traceback:
        console.print(
            "[yellow]GFLOW_CLI_DEBUG_TRACEBACK is set — the output below may "
            "contain tokens/cookies. Do not share it publicly.[/yellow]"
        )
        console.print("".join(traceback.format_exception(exc)), markup=False)
    else:
        console.print(
            "[red]Unexpected error.[/red] Re-run with GFLOW_CLI_DEBUG_TRACEBACK=1 "
            "to see the real error. If this persists, file a bug at "
            "https://github.com/ffroliva/gflow-cli/issues.",
        )
    _print_incident_ref(getattr(exc, "incident_ref", None), console=console)
    return 1


# ------------------------------------------------------------ bridge verbs (#1193)
# `image generate` / `video generate` pick the mode verb from the inputs and then ARE that verb:
# the raw arguments are parsed again by the mode verb itself, so its envvars, callbacks,
# required checks, parameter sources and "No such option" errors are a direct call's.

_BRIDGE_ARGS_META = "gflow.bridge_args"


class BridgeCommand(click.Command):
    """A bridge verb: routes on its raw arguments, then hands them to the resolved verb.

    Only ``--help`` and shell completion parse against the union of the mode verbs'
    options. A run parses loosely (no types, callbacks, envvars or required checks) just
    to read the routing inputs, so every other option is judged by the resolved verb.
    """

    #: Deprecated spellings the mode verbs accept and a bridge refuses (set by bridge_params).
    refused: tuple[click.Option, ...] = ()

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        ahead = args[: args.index("--")] if "--" in args else args
        if ctx.resilient_parsing or any(a in ctx.help_option_names for a in ahead):
            return super().parse_args(ctx, args)
        ctx.meta[_BRIDGE_ARGS_META] = list(args)
        ctx.params = _routing_values(ctx, self, args)
        return []


def _routing_values(ctx: click.Context, cmd: BridgeCommand, args: list[str]) -> dict[str, Any]:
    """The bridge's arguments read loosely: ``positionals`` plus each option's raw value."""
    options = (p for p in (*cmd.params, *cmd.refused) if isinstance(p, click.Option))
    router = click.Command(
        cmd.name,
        params=[click.Argument(["positionals"], nargs=-1), *map(_loose_copy, options)],
        context_settings={"ignore_unknown_options": True, "help_option_names": []},
    )
    values = router.make_context(ctx.info_name, list(args), parent=ctx.parent).params
    for param in cmd.refused:
        if values.pop(param.name or "", None):
            typed = next((a for a in args if a.split("=", 1)[0] in param.opts), param.opts[0])
            raise click.NoSuchOption(typed, ctx=ctx)
    return values


def _loose_copy(param: click.Option) -> click.Option:
    """*param* reduced to its spelling and arity, so reading it can never fail or prompt."""
    loose = copy.copy(param)
    loose.required = False
    loose.callback = None
    loose.envvar = None
    loose.allow_from_autoenv = False
    loose.prompt = None
    loose.is_eager = False
    if not (param.is_flag or param.count):
        loose.type = click.STRING
        loose.default = None
    return loose


def bridge_params(
    cmd: BridgeCommand,
    *modes: click.Command,
    exclude: tuple[str, ...] = (),
    help_for: dict[str, str] | None = None,
) -> None:
    """Give *cmd* its own arguments plus every mode verb's options, for ``--help``.

    An option a mode requires (``--ref``) is relaxed, and ``help_for`` rewrites help that
    only reads right on one mode verb, each on a copy so the mode verb is untouched. A
    deprecated spelling (``-o/--output PATH``, #1158, and *exclude*) is refused instead: a
    new verb starts on the current flags.
    """
    overrides = help_for or {}
    params = list(cmd.params)
    refused: dict[str, click.Option] = {}
    seen = {p.name for p in params}
    for param in (p for mode in modes for p in mode.params):
        if isinstance(param, click.Option) and param.name in exclude:
            refused.setdefault(param.name or "", param)
        elif not isinstance(param, click.Argument) and param.name not in seen:
            seen.add(param.name)
            params.append(_bridge_copy(param, overrides))
    cmd.params = params
    cmd.refused = tuple(refused.values())


def _bridge_copy(param: click.Parameter, overrides: dict[str, str]) -> click.Parameter:
    """*param* as the bridge shows it: optional, and with its help rewritten if asked."""
    if not param.required and param.name not in overrides:
        return param
    param = copy.copy(param)
    param.required = False
    if isinstance(param, click.Option) and param.name in overrides:
        param.help = overrides[param.name]
    return param


def run_bridged(ctx: click.Context, mode: click.Command, why: str) -> None:
    """Run *mode* on the bridge's raw arguments, exactly as if it had been called.

    A usage error the mode raises names the verb the bridge resolved to and why.
    """
    group = ctx.parent.info_name if ctx.parent is not None else ""
    raw: list[str] | None = ctx.meta.get(_BRIDGE_ARGS_META)
    if raw is None:
        msg = (
            f"`gflow {group} {ctx.info_name}` routes on its command-line arguments; run it "
            "from the command line, not through ctx.invoke()."
        )
        raise click.UsageError(msg, ctx=ctx)
    ctx.meta[json_output.BRIDGE_MODE_META] = mode.name
    try:
        sub = mode.make_context(mode.name, list(raw), parent=ctx.parent)
        with sub:
            mode.invoke(sub)
    except click.UsageError as exc:
        msg = (
            f"{exc.format_message()}\n`gflow {group} {ctx.info_name}` resolved to "
            f"`gflow {group} {mode.name}` ({why})."
        )
        raise click.UsageError(msg, ctx=exc.ctx) from exc


def run_with_handlers(
    coro_factory: Callable[[], Coroutine[Any, Any, Any]],
    *,
    cli_command: str,
    as_json: bool = False,
    output_format: str = "table",
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

    # Bind the command name so downstream fixed-field consumers (the incident
    # manifest's `command`, log correlation) can read it from contextvars —
    # the CLI boundary is the one place that knows it.
    structlog.contextvars.bind_contextvars(cli_command=cli_command)
    # Long-lived processes (MCP server, `gflow serve`, the worker, a test
    # session) run many commands. Without this, a finished `video extend`
    # leaves its resume id behind and an unrelated Ctrl+C later prints it.
    clear_interrupt_context()
    is_id = output_format == OutputFormat.ID.value
    try:
        asyncio.run(coro_factory())
    except GFlowError as e:
        if as_json:
            emit_error_event(_logger, e, cli_command=cli_command)
            json_output.emit(json_output.error_payload(e))
            sys.exit(exit_code_for(e))
        sys.exit(_handle_gflow_error(e, cli_command=cli_command, stderr=is_id))
    except (KeyboardInterrupt, click.Abort):
        _report_interrupt()
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
            debug_exc = e if get_settings().debug_traceback else None
            json_output.emit(
                json_output.unexpected_payload(
                    debug=debug_exc, incident_ref=getattr(e, "incident_ref", None)
                )
            )
            sys.exit(1)
        sys.exit(_handle_unhandled_error(e, cli_command=cli_command, stderr=is_id))
    finally:
        # Same reasoning as `clear_interrupt_context()` above, applied to the binding
        # this function makes itself: a long-lived process runs many commands through
        # here without passing back through `cli.main()`, which is the only place that
        # clears contextvars. Left bound, a finished command labels the NEXT one's logs
        # with its own name -- and a wrong `cli_command` is worse than a missing one,
        # because it sends whoever is reading to the wrong command. Measured in the
        # 2026-09-07 e2e sweep: six captures labelled `project rename` whose request
        # bodies were `project.createProject`. `sys.exit` raises SystemExit, so this
        # runs on every path out, including the error ones.
        structlog.contextvars.unbind_contextvars("cli_command")


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
        _console.print(f"[yellow]{escape(str(exc))}[/yellow]")
        sys.exit(2)
    except profile_store.NoDefaultProfileError as exc:
        _console.print(f"[yellow]{escape(str(exc))}[/yellow]")
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


# ---------------------------------------------------------------------------
# Interrupt context
# ---------------------------------------------------------------------------
# A long, billed run (chained extends, `video chain`, `movie run`) can be Ctrl+C'd
# mid-flight. Exiting 130 silently leaves the user unable to tell whether anything
# was charged or how to resume, which for a paid run is a data-loss-shaped bug.
# Commands publish their progress here; the boundary handler reads it on the way
# out. Module-level rather than threaded through every signature because the
# handler is shared and the alternative is changing every command's plumbing.
_INTERRUPT_CONTEXT: dict[str, object] = {}


def set_interrupt_context(
    *, credits_spent: int | None = None, resume_id: str | None = None, segments_done: int = 0
) -> None:
    """Publish what an interrupt would be abandoning. Call it as progress is made."""
    if credits_spent is not None:
        _INTERRUPT_CONTEXT["credits_spent"] = credits_spent
    if resume_id is not None:
        _INTERRUPT_CONTEXT["resume_id"] = resume_id
    _INTERRUPT_CONTEXT["segments_done"] = segments_done


def clear_interrupt_context() -> None:
    _INTERRUPT_CONTEXT.clear()


def _report_interrupt() -> None:
    """Print what was spent and how to resume. Silent when nothing was spent, so
    one-shot commands do not grow a spurious banner."""
    if not _INTERRUPT_CONTEXT:
        return
    spent = _INTERRUPT_CONTEXT.get("credits_spent")
    resume_id = _INTERRUPT_CONTEXT.get("resume_id")
    done = _INTERRUPT_CONTEXT.get("segments_done") or 0
    if not spent and not resume_id:
        return
    _console.print("")
    _console.print("[yellow]Interrupted.[/yellow]")
    if done:
        _console.print(f"  completed   : {done} segment(s), already generated and billed")
    if spent:
        _console.print(f"  credits spent: {spent}")
    if resume_id:
        _console.print(f"  resume with : --resume-from {resume_id}")
