"""Local weekly usage counters and the kill switches that gate them (#1164).

Best-effort by contract: ``record`` never raises, prints or noticeably slows a
command. Counters hold only command/flag names, exit codes and coarse duration
buckets, in ``<home>/telemetry.db``. The home comes from ``_resolve_home()``,
never ``get_settings()``, so a broken configuration cannot silence or break this.
No connection is cached: a long-lived MCP server would otherwise pin the WAL.
"""

from __future__ import annotations

import os
import platform
import secrets
import sqlite3
import sys
import time
import traceback
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import click
import structlog
from dotenv import dotenv_values

from gflow_cli import __version__
from gflow_cli.config import _env_files, _resolve_home  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from gflow_cli.tools.spec import ToolSpec

log = structlog.get_logger(__name__)


def _debug(event: str, **kw: str) -> None:
    # Unconfigured structlog prints to STDOUT (PrintLogger); observability.configure_logging
    # routes to stderr. Log only once configured, so stdout (MCP stdio, --json) stays clean.
    if structlog.is_configured():
        log.debug(event, **kw)


_RETAIN_WEEKS = 53
_TRANSITION_WINDOW_S = 30 * 60
SCHEMA = 1
NOTICE_VERSION = 1
NOTICE_TEXT = (
    "gflow sends weekly usage totals to improve it, and we share overall figures publicly and "
    "with\n"
    "sponsors. Commands, flag names, models, error codes, and your country as a total. No ID, "
    "prompts,\n"
    "paths or account details. Each report is added to totals and discarded.\n"
    "Turn it off: gflow telemetry off, or set GFLOW_CLI_TELEMETRY=0\n"
    "See exactly what is sent: gflow telemetry show. Full notice: gflow docs TELEMETRY"
)
_NOTICE_SHOWN_THIS_RUN = False


@dataclass(frozen=True)
class Counter:
    """One weekly counter row. ``kind`` is command, flag, exit, duration,
    transition, client, crash or tool.
    """

    week: str
    surface: str
    kind: str
    key: str
    n: int


def iso_week(now: datetime | None = None) -> str:
    year, week, _ = (now or datetime.now(UTC)).isocalendar()
    return f"{year}-W{week:02d}"


def _env_sources() -> list[dict[str, str | None]]:
    sources: list[dict[str, str | None]] = [dict(os.environ)]
    try:  # unreadable or non-UTF-8 .env (also inside _resolve_home): cannot switch anything
        for path in _env_files():
            sources.append(dict(dotenv_values(path)))
    except (OSError, ValueError):
        pass
    return sources


def _lookup(name: str) -> list[str]:
    """Every value of ``name`` across env and dotenv files (name matched case-insensitively)."""
    return [
        v for src in _env_sources() for k, v in src.items() if k.upper() == name and v is not None
    ]


@cache
def killed() -> str | None:
    """The kill switch in force, if any. Any source can only turn telemetry OFF."""
    if any(
        v.strip().lower() not in ("1", "true", "on", "") for v in _lookup("GFLOW_CLI_TELEMETRY")
    ):
        return "GFLOW_CLI_TELEMETRY"
    if any(v.strip().lower() not in ("", "0", "false") for v in _lookup("DO_NOT_TRACK")):
        return "DO_NOT_TRACK"
    return None


def opted_out() -> bool:
    """Not cached: a running MCP server must notice ``gflow telemetry off``.

    Fails closed: when the state cannot be read we cannot tell, so nothing is counted.
    """
    try:
        return (_resolve_home() / "telemetry.off").exists()
    except Exception:  # noqa: BLE001
        return True


def set_opted_out(value: bool) -> None:
    """Persist the user's choice independently of the best-effort counter store."""
    from gflow_cli.errors import ConfigurationError

    path = _resolve_home() / "telemetry.off"
    try:
        if value:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        raise ConfigurationError(
            detail=f"Could not write telemetry preference at {path}: {exc}"
        ) from exc


def _meta() -> dict[str, str]:
    home = _resolve_home()
    db_path = home / "telemetry.db"
    if not db_path.exists():
        return {}
    with closing(sqlite3.connect(db_path, timeout=0.2)) as con:
        return dict(con.execute("SELECT key, value FROM meta"))


def upload_disabled_reason() -> str | None:
    """Return the first reason weekly uploads are disabled, failing closed on read errors."""
    try:
        if switch := killed():
            return f"killed: {switch}"
        if opted_out():
            return "telemetry off"
        from gflow_cli import update_check

        if update_check._in_ci():  # pyright: ignore[reportPrivateUsage]
            return "CI"
        if not update_check._installed_from_index():  # pyright: ignore[reportPrivateUsage]
            return "not installed from a package index"
        meta = _meta()
        if meta.get("upload_disabled") == "1":
            return "server stopped uploads"
        if meta.get("notice_seen_version") != str(NOTICE_VERSION):
            return "notice not shown yet"
        return None
    except Exception:  # noqa: BLE001 - any uncertain upload state stays off
        return "local usage state unavailable"


def uploads_enabled() -> bool:
    """Whether upload conditions are satisfied; any uncertainty disables uploads."""
    return upload_disabled_reason() is None


def notice_seen() -> bool:
    """Whether the current notice version is recorded locally."""
    try:
        return _meta().get("notice_seen_version") == str(NOTICE_VERSION)
    except Exception:  # noqa: BLE001
        return False


def maybe_show_notice() -> bool:
    """Show and persist the one-time notice only in an interactive, opted-in run."""
    global _NOTICE_SHOWN_THIS_RUN
    try:
        if not sys.stderr.isatty() or _NOTICE_SHOWN_THIS_RUN or killed() or opted_out():
            return False
        from gflow_cli import update_check

        if (
            update_check._in_ci()  # pyright: ignore[reportPrivateUsage]
            or _meta().get("notice_seen_version") == str(NOTICE_VERSION)
        ):
            return False
        click.echo(NOTICE_TEXT, err=True)
        _NOTICE_SHOWN_THIS_RUN = True  # pyright: ignore[reportConstantRedefinition]
        try:
            home = _resolve_home()
            home.mkdir(parents=True, exist_ok=True)
            with closing(_connect(home)) as con, con:
                con.executemany(
                    "INSERT OR REPLACE INTO meta VALUES (?, ?)",
                    [
                        ("notice_seen_version", str(NOTICE_VERSION)),
                        ("notice_seen_at", datetime.now(UTC).isoformat()),
                    ],
                )
        except Exception:  # noqa: BLE001 - a failed write only makes the notice repeat next run
            pass
        return True
    except Exception:  # noqa: BLE001 - the notice must never affect a command
        return False


def state() -> tuple[Literal["on", "off"], str | None]:
    """Return the telemetry state and the switch currently disabling it, if any."""
    if switch := killed():
        return "off", switch
    if opted_out():
        return "off", "telemetry.off"
    return "on", None


def _bucket(seconds: float) -> str:
    if seconds < 10:
        return "<10s"
    if seconds < 60:
        return "<1m"
    if seconds < 300:
        return "<5m"
    return "5m+"


def _connect(home: Path) -> sqlite3.Connection:
    con = sqlite3.connect(home / "telemetry.db", timeout=0.2)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            "CREATE TABLE IF NOT EXISTS counters (week TEXT, surface TEXT, kind TEXT, key TEXT,"
            " n INTEGER, PRIMARY KEY (week, surface, kind, key))"
        )
        con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute(
            "CREATE TABLE IF NOT EXISTS reports ("
            "week TEXT PRIMARY KEY, report_id TEXT NOT NULL, due_at TEXT, sent_at TEXT)"
        )
    except BaseException:
        con.close()  # a leaked handle pins the file on Windows
        raise
    return con


def record(
    surface: Literal["cli", "mcp"],
    command: str,
    flags: Iterable[str],
    outcome: str,
    duration_s: float,
) -> None:
    """Count one command invocation. Silent on every failure (lossy under contention)."""
    try:
        if killed() or opted_out():
            return
        home = _resolve_home()
        home.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        week = iso_week(now)
        keys = [("command", command)]
        keys += [("flag", f"{command} {f}") for f in flags]
        keys += [("exit", f"{command} {outcome}"), ("duration", f"{command} {_bucket(duration_s)}")]
        with closing(_connect(home)) as con, con:
            meta = dict(con.execute("SELECT key, value FROM meta"))
            prev = meta.get("prev_command")
            if (
                prev
                and meta.get("prev_surface") == surface
                and now.timestamp() - float(meta.get("prev_time", 0)) <= _TRANSITION_WINDOW_S
            ):
                keys.append(("transition", f"{prev} -> {command}"))
            con.executemany(
                "INSERT INTO counters VALUES (?, ?, ?, ?, 1)"
                " ON CONFLICT(week, surface, kind, key) DO UPDATE SET n = n + 1",
                [(week, surface, kind, key) for kind, key in keys],
            )
            con.executemany(
                "INSERT OR REPLACE INTO meta VALUES (?, ?)",
                [
                    ("prev_command", command),
                    ("prev_surface", surface),
                    ("prev_time", str(time.time())),
                ],
            )
            con.execute(
                "INSERT OR IGNORE INTO meta VALUES ('first_seen', ?)", (now.date().isoformat(),)
            )
            cutoff = iso_week(now - timedelta(weeks=_RETAIN_WEEKS))
            con.execute("DELETE FROM counters WHERE week < ?", (cutoff,))
    except Exception as exc:  # noqa: BLE001 - telemetry must never affect a command
        _debug("telemetry_record_failed", error=type(exc).__name__)


def record_counter(surface: Literal["cli", "mcp"], kind: str, key: str) -> None:
    """Count one extra weekly value (for example an MCP client or crash)."""
    try:
        if killed() or opted_out():
            return
        home = _resolve_home()
        home.mkdir(parents=True, exist_ok=True)
        with closing(_connect(home)) as con, con:
            con.execute(
                "INSERT INTO counters VALUES (?, ?, ?, ?, 1)"
                " ON CONFLICT(week, surface, kind, key) DO UPDATE SET n = n + 1",
                (iso_week(), surface, kind, key),
            )
    except Exception as failure:  # noqa: BLE001
        _debug("telemetry_counter_failed", error=type(failure).__name__)


def record_crash(
    surface: Literal["cli", "mcp"], exc: BaseException, *, fingerprint: str | None = None
) -> None:
    """Count a privacy-safe crash fingerprint without affecting the caller."""
    try:
        record_counter(surface, "crash", fingerprint if fingerprint is not None else crash_key(exc))
    except Exception as failure:  # noqa: BLE001
        _debug("telemetry_crash_failed", error=type(failure).__name__)


def crash_key(exc: BaseException) -> str:
    """Fingerprint exception type and safe gflow_cli frame names only."""
    cause = exc.__cause__ or (exc.__context__ if not exc.__suppress_context__ else None)
    head = type(exc).__name__ + (f" from {type(cause).__name__}" if cause else "")
    tokens: list[str] = []
    third_party: str | None = None
    ours = 0
    frames = list(traceback.walk_tb(exc.__traceback__))
    for frame, _ in reversed(frames):
        module = frame.f_globals.get("__name__")
        if not isinstance(module, str) or module.startswith("<") or module == "__main__":
            continue
        top = module.partition(".")[0]
        if top in sys.stdlib_module_names:
            continue
        if module == "gflow_cli" or module.startswith("gflow_cli."):
            if third_party:
                tokens.append(f"<{third_party}>")
                third_party = None
            name = module.removeprefix("gflow_cli.") if module != "gflow_cli" else "gflow_cli"
            tokens.append(f"{name}:{frame.f_code.co_qualname}")
            ours += 1
            if ours == 3:
                break
        else:
            third_party = third_party or top
    if third_party:
        tokens.append(f"<{third_party}>")
    return (f"{head} <- {' <- '.join(tokens)}" if ours else f"{head} at other")[:200]


def leaf_and_flags(root: click.Group, argv: Sequence[str]) -> tuple[str, list[str]]:
    """Resolve Click's command path and retain only registered leaf option names."""
    group: click.Group = root
    leaf: click.Command = root
    path: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            break
        if arg.startswith("-"):
            option = next(
                (
                    p
                    for p in group.params
                    if isinstance(p, click.Option) and arg.split("=", 1)[0] in p.opts
                ),
                None,
            )
            if option and option.nargs and "=" not in arg:
                index += option.nargs
            index += 1
            continue
        command = group.commands.get(arg)
        if command is None:
            break
        path.append(arg)
        index += 1
        if not isinstance(command, click.Group):
            leaf = command
            break
        group = command
        leaf = group
    else:
        leaf = group
    if not path:
        command_name = "gflow" if root.name in (None, "main") else root.name
        leaf = root
    else:
        command_name = " ".join(path)
    options = [p for p in leaf.params if isinstance(p, click.Option)]
    aliases = {
        name: next((o for o in opt.opts if o.startswith("--")), opt.opts[0])
        for opt in options
        for name in opt.opts + opt.secondary_opts
    }
    flags: list[str] = []
    for arg in argv:
        if arg == "--":
            break
        name = arg.split("=", 1)[0]
        if name in aliases and aliases[name] not in flags:
            flags.append(aliases[name])
    return command_name, flags


class CountingGroup(click.Group):
    """Click root group that records command names and registered flag names."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        ctx.meta["telemetry_argv"] = list(args)
        return super().parse_args(ctx, args)

    def invoke(self, ctx: click.Context) -> object:
        argv = ctx.meta.get("telemetry_argv", [])
        command, flags = leaf_and_flags(self, argv)
        started = time.monotonic()
        outcome = "0"
        try:
            return super().invoke(ctx)
        except SystemExit as exc:
            outcome = (
                str(exc.code) if isinstance(exc.code, int) else "0" if exc.code is None else "1"
            )
            raise
        except click.exceptions.Exit as exc:
            outcome = str(exc.exit_code)
            raise
        except click.ClickException as exc:
            outcome = str(exc.exit_code)
            raise
        except click.Abort:
            outcome = "1"
            raise
        except KeyboardInterrupt:
            outcome = "130"
            raise
        except Exception as exc:
            outcome = "1"
            try:
                record_crash("cli", exc)
            except Exception:
                pass
            raise
        except BaseException:
            outcome = "1"
            raise
        finally:
            try:
                help_opts = set(ctx.help_option_names)
                current: click.Command = self
                for arg in argv:
                    if arg == "--":
                        break
                    if isinstance(current, click.Group):
                        current = current.commands.get(arg, current)
                        help_opts.update(current.context_settings.get("help_option_names", ()))
                if not any(
                    a in help_opts for a in argv[: argv.index("--") if "--" in argv else len(argv)]
                ):
                    record("cli", command, flags, outcome, time.monotonic() - started)
            except Exception:
                pass


def read_counters(weeks: int | None = None) -> list[Counter]:
    """All stored counters (or those of the last ``weeks`` ISO weeks); [] on any failure."""
    try:
        home = _resolve_home()
        if not (home / "telemetry.db").exists():
            return []
        sql = "SELECT week, surface, kind, key, n FROM counters"
        args: tuple[str, ...] = ()
        if weeks is not None:
            sql += " WHERE week > ?"
            args = (iso_week(datetime.now(UTC) - timedelta(weeks=weeks)),)
        with closing(_connect(home)) as con:
            return [Counter(*row) for row in con.execute(sql + " ORDER BY week, kind, key", args)]
    except Exception as exc:  # noqa: BLE001
        _debug("telemetry_read_failed", error=type(exc).__name__)
        return []


def report_id_for(week: str) -> str:
    """Return the stable random report_id for ``week``, creating one if needed."""
    try:
        home = _resolve_home()
        home.mkdir(parents=True, exist_ok=True)
        with closing(_connect(home)) as con, con:
            # Insert-if-absent, then one read: an existing week keeps its id.
            con.execute(
                "INSERT OR IGNORE INTO reports (week, report_id, due_at, sent_at)"
                " VALUES (?, ?, NULL, NULL)",
                (week, secrets.token_urlsafe(16)),
            )
            row = con.execute("SELECT report_id FROM reports WHERE week = ?", (week,)).fetchone()
            return str(row[0])
    except Exception as exc:  # noqa: BLE001
        _debug("telemetry_report_id_failed", error=type(exc).__name__)
        return secrets.token_urlsafe(16)


def stored_report_id(week: str) -> str | None:
    """Return only a report ID known to be persisted, for upload deduplication."""
    try:
        home = _resolve_home()
        if not (home / "telemetry.db").exists():
            return None
        with closing(_connect(home)) as con:
            row = con.execute("SELECT report_id FROM reports WHERE week = ?", (week,)).fetchone()
            return str(row[0]) if row is not None else None
    except Exception as exc:  # noqa: BLE001
        _debug("telemetry_stored_report_id_failed", error=type(exc).__name__)
        return None


def record_tool(spec: ToolSpec, expanded: bool) -> None:
    """Count prompt-tool usage without affecting the caller."""
    try:
        if killed() or opted_out():
            return
        from gflow_cli.tools.loader import load_builtin_tools

        builtins = load_builtin_tools()
        name = spec.name if builtins.get(spec.name) == spec else "custom"
        status = "expanded" if expanded else "unchanged"
        key = f"{name} {status}"
        home = _resolve_home()
        home.mkdir(parents=True, exist_ok=True)
        with closing(_connect(home)) as con, con:
            con.execute(
                "INSERT INTO counters VALUES (?, 'any', 'tool', ?, 1)"
                " ON CONFLICT(week, surface, kind, key) DO UPDATE SET n = n + 1",
                (iso_week(), key),
            )
    except Exception as exc:  # noqa: BLE001
        _debug("telemetry_tool_failed", error=type(exc).__name__)


def _detect_os() -> str:
    sys_name = platform.system()
    if sys_name == "Windows":
        return "windows"
    if sys_name == "Linux":
        return "linux"
    if sys_name == "Darwin":
        return "darwin"
    return "other"


def _detect_arch() -> str:
    mach = platform.machine().lower()
    if mach in ("amd64", "x86_64"):
        return "x86_64"
    if mach in ("arm64", "aarch64"):
        return "arm64"
    return "other"


def _detect_installer() -> str:
    try:
        from gflow_cli.update_check import installer

        inst = installer()
        if inst is not None and inst.name in ("uv", "pipx", "pip"):
            return inst.name
        return "other"
    except Exception:
        return "other"


def _detect_install_age(home: Path) -> str:
    try:
        if not (home / "telemetry.db").exists():
            return "<1w"
        with closing(_connect(home)) as con:
            row = con.execute("SELECT value FROM meta WHERE key = 'first_seen'").fetchone()
            if row and row[0]:
                first_date = datetime.fromisoformat(row[0]).date()
                days = (datetime.now(UTC).date() - first_date).days
                if days < 7:
                    return "<1w"
                if days < 30:
                    return "<1m"
                if days < 180:
                    return "<6m"
                return "6m+"
    except Exception:
        pass
    return "<1w"


def _detect_storage() -> str:
    try:
        from gflow_cli.config import get_settings

        uri = get_settings().storage_uri
        if not uri:
            return "local"
        scheme = urlsplit(uri).scheme.lower()
        if not scheme:
            return "local"
        if scheme == "s3":
            return "s3"
        if scheme in ("gs", "gcs"):
            return "gcs"
        return "other"
    except Exception:
        return "other"


def _catalog_db_path() -> Path:
    if env := os.environ.get("GFLOW_CLI_DB_PATH"):
        return Path(env)
    try:
        from gflow_cli.config import get_settings

        return get_settings().resolved_db_path()
    except Exception:
        from gflow_cli import paths

        return paths.database_path(_resolve_home())


def build_report(week: str) -> dict[str, object]:
    """Assemble the weekly usage report for ``week`` field by field (#1164)."""
    home = _resolve_home()

    commands: dict[str, dict[str, int]] = {}
    flags: dict[str, dict[str, dict[str, int]]] = {}
    exits: dict[str, dict[str, dict[str, int]]] = {}
    durations: dict[str, dict[str, dict[str, int]]] = {}
    transitions: dict[str, int] = {}
    crashes: dict[str, dict[str, int]] = {}
    mcp_clients: dict[str, int] = {}
    tools: dict[str, dict[str, int]] = {}
    nested_targets: dict[str, dict[str, Any]] = {
        "flag": flags,
        "exit": exits,
        "duration": durations,
        "tool": tools,
    }

    def _add(target: dict[str, Any], path: tuple[str, ...], n: int) -> None:
        cursor = target
        for key_part in path[:-1]:
            cursor = cursor.setdefault(key_part, {})
        leaf = path[-1]
        cursor[leaf] = cursor.get(leaf, 0) + n

    try:
        if (home / "telemetry.db").exists():
            with closing(_connect(home)) as con:
                rows = con.execute(
                    "SELECT surface, kind, key, n FROM counters WHERE week = ?", (week,)
                ).fetchall()
                for surface, kind, key, n in rows:
                    if "|" in key or "|" in surface:
                        continue
                    if kind == "command":
                        _add(commands, (surface, key), n)
                    elif kind in nested_targets and " " in key:
                        name, value = key.rsplit(" ", 1)
                        if "|" in name or "|" in value:
                            continue
                        path = (name, value) if kind == "tool" else (surface, name, value)
                        _add(nested_targets[kind], path, n)
                    elif kind == "transition":
                        transitions[key] = transitions.get(key, 0) + n
                    elif kind == "crash":
                        crashes.setdefault(surface, {})[key] = (
                            crashes.setdefault(surface, {}).get(key, 0) + n
                        )
                    elif kind == "client":
                        mcp_clients[key] = mcp_clients.get(key, 0) + n
    except Exception as exc:  # noqa: BLE001
        _debug("telemetry_report_counters_failed", error=type(exc).__name__)

    models: dict[str, int] = {}
    modes: dict[str, int] = {}
    aspects: dict[str, int] = {}
    outcomes: dict[str, int] = {}

    cat_path = _catalog_db_path()
    if cat_path.is_file():
        try:
            from gflow_cli.api.image import (
                Aspect as ImageAspect,
            )
            from gflow_cli.api.image import (
                Model as ImageModel,
            )
            from gflow_cli.api.image import (
                aspect_choices as image_aspect_choices,
            )
            from gflow_cli.api.video import Aspect as VideoAspect
            from gflow_cli.api.video import VideoModel
            from gflow_cli.data.models import OperationKind, OperationStatus
            from gflow_cli.data.store import DataStore

            known_models = {m.value for m in ImageModel} | {m.value for m in VideoModel}
            known_modes = {k.value for k in OperationKind}
            known_outcomes = {s.value for s in OperationStatus}
            known_aspects = (
                {a.value for a in ImageAspect}
                | set(image_aspect_choices().keys())
                | {a.value for a in VideoAspect}
                | {a.wire() for a in VideoAspect}
            )
            year, week_number = (int(part) for part in week.split("-W"))
            week_start = datetime.fromisocalendar(year, week_number, 1).replace(tzinfo=UTC)
            week_end = week_start + timedelta(days=7)

            with DataStore.open_readonly(cat_path) as store:
                op_rows = store.conn.execute(
                    "SELECT model, mode, aspect_ratio, status, started_at FROM operations "
                    "WHERE started_at >= ? AND started_at < ?",
                    (week_start.isoformat(), week_end.isoformat()),
                ).fetchall()
                for op in op_rows:
                    started_at = op["started_at"]
                    if not started_at:
                        continue
                    try:
                        op_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                        if iso_week(op_dt) != week:
                            continue
                    except Exception:
                        continue

                    # mode
                    op_mode = op["mode"]
                    mode_key = op_mode if op_mode in known_modes else "other"
                    if "|" not in mode_key:
                        modes[mode_key] = modes.get(mode_key, 0) + 1

                    # outcome
                    op_status = op["status"]
                    outcome_key = op_status if op_status in known_outcomes else "other"
                    if "|" not in outcome_key:
                        outcomes[outcome_key] = outcomes.get(outcome_key, 0) + 1

                    # model (if set)
                    op_model = op["model"]
                    if op_model:
                        model_key = op_model if op_model in known_models else "other"
                        if "|" not in model_key:
                            models[model_key] = models.get(model_key, 0) + 1

                    # aspect_ratio (if set)
                    op_aspect = op["aspect_ratio"]
                    if op_aspect:
                        aspect_key = op_aspect if op_aspect in known_aspects else "other"
                        if "|" not in aspect_key:
                            aspects[aspect_key] = aspects.get(aspect_key, 0) + 1
        except Exception as exc:  # noqa: BLE001
            _debug("telemetry_report_catalog_failed", error=type(exc).__name__)

    return {
        "schema": SCHEMA,
        "report_id": (
            secrets.token_urlsafe(16) if killed() or opted_out() else report_id_for(week)
        ),
        "week": week,
        "version": __version__,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "os": _detect_os(),
        "arch": _detect_arch(),
        "installer": _detect_installer(),
        "install_age": _detect_install_age(home),
        "storage": _detect_storage(),
        "commands": commands,
        "flags": flags,
        "exits": exits,
        "durations": durations,
        "transitions": transitions,
        "models": models,
        "modes": modes,
        "aspects": aspects,
        "outcomes": outcomes,
        "mcp_clients": mcp_clients,
        "crashes": crashes,
        "tools": tools,
    }
