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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
DB_FILE = "telemetry.db"
OFF_FILE = "telemetry.off"
CONSENT_FILE = "telemetry.consent"
SCHEMA = 1
NOTICE_VERSION = 2
NOTICE_TEXT = (
    "gflow sends weekly usage totals to improve it, and we share overall figures publicly and "
    "with\n"
    "sponsors. Commands, flag names, models, error codes, and your country as a total. No ID, "
    "prompts,\n"
    "paths or account details. Each report is added to totals and discarded.\n"
    "Turn it off: gflow telemetry off, or set GFLOW_CLI_TELEMETRY=0\n"
    "See exactly what is sent: gflow telemetry show. Full notice: gflow docs TELEMETRY"
)
NOTICE_TEXT_CONSENT = (
    "gflow can send weekly usage totals to help improve it; overall figures are shared publicly "
    "and "
    "with sponsors. Commands, flag names, models, error codes, and your country as a total. No ID, "
    "prompts, paths or account details. Each report is added to totals and discarded.\n"
    "In your region nothing is sent unless you agree: gflow telemetry on (undo: gflow telemetry "
    "off)\n"
    "See exactly what would be sent: gflow telemetry show. Full notice: gflow docs TELEMETRY"
)
_NOTICE_SHOWN_THIS_RUN = False

_EU_EEA_TIMEZONES = frozenset(
    {
        "Africa/Ceuta",
        "America/Cayenne",
        "America/Guadeloupe",
        "America/Martinique",
        "America/Marigot",
        "America/St_Barthelemy",
        "Arctic/Longyearbyen",
        "Asia/Famagusta",
        "Asia/Nicosia",
        "Atlantic/Azores",
        "Atlantic/Canary",
        "Atlantic/Madeira",
        "Atlantic/Reykjavik",
        "Europe/Amsterdam",
        "Europe/Athens",
        "Europe/Berlin",
        "Europe/Bratislava",
        "Europe/Brussels",
        "Europe/Bucharest",
        "Europe/Budapest",
        "Europe/Busingen",
        "Europe/Copenhagen",
        "Europe/Dublin",
        "Europe/Helsinki",
        "Europe/Lisbon",
        "Europe/Ljubljana",
        "Europe/Luxembourg",
        "Europe/Madrid",
        "Europe/Malta",
        "Europe/Mariehamn",
        "Europe/Oslo",
        "Europe/Paris",
        "Europe/Prague",
        "Europe/Riga",
        "Europe/Rome",
        "Europe/Sofia",
        "Europe/Stockholm",
        "Europe/Tallinn",
        "Europe/Vaduz",
        "Europe/Vienna",
        "Europe/Vilnius",
        "Europe/Warsaw",
        "Europe/Zagreb",
        "Indian/Reunion",
        "Indian/Mayotte",
        "CET",
        "EET",
        "MET",
        "WET",
        "Poland",
        "Portugal",
        "Eire",
        "Iceland",
    }
)
# Deliberately over-inclusive: Windows zones spanning any EU/EEA country require consent.
_EU_EEA_WINDOWS_TIMEZONES = frozenset(
    {
        "Azores Standard Time",
        "Central Europe Standard Time",
        "Central European Standard Time",
        "E. Europe Standard Time",
        "FLE Standard Time",
        "GMT Standard Time",
        "Greenwich Standard Time",
        "GTB Standard Time",
        "Romance Standard Time",
        "W. Europe Standard Time",
    }
)


def _read_windows_timezone_key_name() -> str | None:
    """Read the configured Windows timezone key, if available."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "TimeZoneKeyName")
        return value if isinstance(value, str) else None
    except (ImportError, OSError):
        return None


def _posix_timezone_name() -> str | None:
    """Read an IANA timezone from the POSIX localtime link or timezone file."""
    try:
        target = os.readlink("/etc/localtime")
        parts = target.split("/zoneinfo/", maxsplit=1)
        if len(parts) == 2 and parts[1]:
            return parts[1].removeprefix("posix/").removeprefix("right/")
        return Path("/etc/timezone").read_text(encoding="utf-8").strip() or None
    except (OSError, RuntimeError):
        return None


def _valid_iana_name(tz: str) -> bool:
    return (
        not tz.startswith("/")
        and ".." not in tz.split("/")
        and all(part.replace("_", "").replace("-", "").isalnum() for part in tz.split("/"))
    )


def _env_timezone_name(tz: str) -> str | None:
    if not _valid_iana_name(tz):
        return None
    try:
        ZoneInfo(tz)
        return tz
    except (ZoneInfoNotFoundError, ValueError):
        if sys.platform == "win32" and ("/" in tz or tz in {"UTC", "GMT"}):
            return tz
        return None


def _timezone_name() -> str | None:
    """Resolve a timezone name without network or locale information."""
    if tz := os.environ.get("TZ", "").strip():
        if _valid_iana_name(tz):
            return _env_timezone_name(tz)
    return _posix_timezone_name() if sys.platform != "win32" else _read_windows_timezone_key_name()


def consent_region() -> bool:
    """Whether weekly uploads require explicit consent based on local timezone only."""
    zone = _timezone_name()
    return zone is None or zone in _EU_EEA_TIMEZONES or zone in _EU_EEA_WINDOWS_TIMEZONES


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
    """Not cached: a running MCP server must notice the ``<home>/telemetry.off`` sentinel.

    Fails closed: when the state cannot be read we cannot tell, so nothing is counted.
    """
    try:
        return (_resolve_home() / OFF_FILE).exists()
    except Exception:  # noqa: BLE001
        return True


def set_opted_out(value: bool) -> None:
    """Persist the user's choice independently of the best-effort counter store."""
    from gflow_cli.errors import ConfigurationError

    home = _resolve_home()
    path = home / OFF_FILE
    consent_path = home / CONSENT_FILE
    preference_path = path
    try:
        if value:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
            preference_path = consent_path
            consent_path.unlink(missing_ok=True)
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        raise ConfigurationError(
            detail=f"Could not write telemetry preference at {preference_path}: {exc}"
        ) from exc


def set_consent() -> None:
    """Persist explicit upload consent when this machine is in a consent region."""
    if not consent_region():
        return
    path = _resolve_home() / CONSENT_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    except OSError as exc:
        from gflow_cli.errors import ConfigurationError

        raise ConfigurationError(
            detail=f"Could not write telemetry preference at {path}: {exc}"
        ) from exc


def _meta() -> dict[str, str]:
    home = _resolve_home()
    db_path = home / DB_FILE
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
        if consent_region() and not (_resolve_home() / CONSENT_FILE).exists():
            return "consent needed in your region: run gflow telemetry on"
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
        click.echo(NOTICE_TEXT_CONSENT if consent_region() else NOTICE_TEXT, err=True)
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
        return "off", OFF_FILE
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
    con = sqlite3.connect(home / DB_FILE, timeout=0.2)
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


def _safe_frame_tokens(exc: BaseException) -> tuple[list[str], int]:
    tokens: list[str] = []
    third_party: str | None = None
    ours = 0
    frames = list(traceback.walk_tb(exc.__traceback__))
    for frame, _ in reversed(frames):
        module = _safe_frame_module(frame.f_globals.get("__name__"))
        if module is None:
            continue
        if module != "gflow_cli" and not module.startswith("gflow_cli."):
            third_party = third_party or module.partition(".")[0]
            continue
        if third_party:
            tokens.append(f"<{third_party}>")
            third_party = None
        tokens.append(f"{_frame_name(module)}:{frame.f_code.co_qualname}")
        ours += 1
        if ours == 3:
            break
    if third_party:
        tokens.append(f"<{third_party}>")
    return tokens, ours


def _frame_name(module: str) -> str:
    return module.removeprefix("gflow_cli.") if module != "gflow_cli" else "gflow_cli"


def _safe_frame_module(module: object) -> str | None:
    if not isinstance(module, str) or module.startswith("<") or module == "__main__":
        return None
    if module.partition(".")[0] in sys.stdlib_module_names:
        return None
    return module


def crash_key(exc: BaseException) -> str:
    """Fingerprint exception type and safe gflow_cli frame names only."""
    cause = exc.__cause__ or (exc.__context__ if not exc.__suppress_context__ else None)
    head = type(exc).__name__ + (f" from {type(cause).__name__}" if cause else "")
    tokens, ours = _safe_frame_tokens(exc)
    return (f"{head} <- {' <- '.join(tokens)}" if ours else f"{head} at other")[:200]


def _option_for_arg(group: click.Group, arg: str) -> click.Option | None:
    return next(
        (
            param
            for param in group.params
            if isinstance(param, click.Option) and arg.split("=", 1)[0] in param.opts
        ),
        None,
    )


def _option_argv_width(group: click.Group, arg: str) -> int:
    option = _option_for_arg(group, arg)
    if option and option.nargs and "=" not in arg:
        return option.nargs + 1
    return 1


def _command_path(root: click.Group, argv: Sequence[str]) -> tuple[str, click.Command]:
    group: click.Group = root
    leaf: click.Command = root
    path: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--":
            break
        if arg.startswith("-"):
            index += _option_argv_width(group, arg)
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
        return command_name, root
    return " ".join(path), leaf


def _option_aliases(leaf: click.Command) -> dict[str, str]:
    options = [p for p in leaf.params if isinstance(p, click.Option)]
    return {
        name: next((o for o in opt.opts if o.startswith("--")), opt.opts[0])
        for opt in options
        for name in opt.opts + opt.secondary_opts
    }


def _used_flag_names(argv: Sequence[str], aliases: dict[str, str]) -> list[str]:
    flags: list[str] = []
    for arg in argv:
        if arg == "--":
            break
        name = arg.split("=", 1)[0]
        if name in aliases and aliases[name] not in flags:
            flags.append(aliases[name])
    return flags


def leaf_and_flags(root: click.Group, argv: Sequence[str]) -> tuple[str, list[str]]:
    """Resolve Click's command path and retain only registered leaf option names."""
    command, leaf = _command_path(root, argv)
    return command, _used_flag_names(argv, _option_aliases(leaf))


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
        except BaseException as exc:
            outcome = _invocation_outcome(exc)
            raise
        finally:
            _record_cli_invocation(self, ctx, argv, command, flags, outcome, started)


def _invocation_outcome(exc: BaseException) -> str:
    if isinstance(exc, SystemExit):
        if isinstance(exc.code, int):
            return str(exc.code)
        return "0" if exc.code is None else "1"
    if isinstance(exc, (click.exceptions.Exit, click.ClickException)):
        return str(exc.exit_code)
    if isinstance(exc, click.Abort):
        return "1"
    if isinstance(exc, KeyboardInterrupt):
        return "130"
    if isinstance(exc, Exception):
        try:
            record_crash("cli", exc)
        except Exception:
            pass
    return "1"


def _record_cli_invocation(
    root: click.Group,
    ctx: click.Context,
    argv: Sequence[str],
    command: str,
    flags: list[str],
    outcome: str,
    started: float,
) -> None:
    try:
        help_opts = set(ctx.help_option_names)
        current: click.Command = root
        for arg in argv:
            if arg == "--":
                break
            if isinstance(current, click.Group):
                current = current.commands.get(arg, current)
                help_opts.update(current.context_settings.get("help_option_names", ()))
        before_separator = argv[: argv.index("--")] if "--" in argv else argv
        if any(arg in help_opts for arg in before_separator):
            return
        record("cli", command, flags, outcome, time.monotonic() - started)
    except Exception:
        pass


def read_counters(weeks: int | None = None) -> list[Counter]:
    """All stored counters (or those of the last ``weeks`` ISO weeks); [] on any failure."""
    try:
        home = _resolve_home()
        if not (home / DB_FILE).exists():
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
        if not (home / DB_FILE).exists():
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
        if not (home / DB_FILE).exists():
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


def _report_counter_targets() -> dict[str, Any]:
    flags: dict[str, dict[str, dict[str, int]]] = {}
    exits: dict[str, dict[str, dict[str, int]]] = {}
    durations: dict[str, dict[str, dict[str, int]]] = {}
    tools: dict[str, dict[str, int]] = {}
    return {
        "commands": {},
        "flags": flags,
        "exits": exits,
        "durations": durations,
        "transitions": {},
        "crashes": {},
        "mcp_clients": {},
        "tools": tools,
        "nested": {"flag": flags, "exit": exits, "duration": durations, "tool": tools},
    }


def _add_nested(target: dict[str, Any], path: tuple[str, ...], count: int) -> None:
    cursor = target
    for key_part in path[:-1]:
        cursor = cursor.setdefault(key_part, {})
    leaf = path[-1]
    cursor[leaf] = cursor.get(leaf, 0) + count


def _add_report_counter(
    targets: dict[str, Any], surface: str, kind: str, key: str, count: int
) -> None:
    if "|" in key or "|" in surface:
        return
    nested = targets["nested"]
    if kind == "command":
        _add_nested(targets["commands"], (surface, key), count)
    elif kind in nested and " " in key:
        _add_nested_counter(nested, surface, kind, key, count)
    else:
        _add_scalar_counter(targets, surface, kind, key, count)


def _add_nested_counter(
    nested: dict[str, Any], surface: str, kind: str, key: str, count: int
) -> None:
    name, value = key.rsplit(" ", 1)
    if "|" in name or "|" in value:
        return
    path = (name, value) if kind == "tool" else (surface, name, value)
    _add_nested(nested[kind], path, count)


def _add_scalar_counter(
    targets: dict[str, Any], surface: str, kind: str, key: str, count: int
) -> None:
    if kind == "transition":
        values = targets["transitions"]
        values[key] = values.get(key, 0) + count
    elif kind == "crash":
        values = targets["crashes"].setdefault(surface, {})
        values[key] = values.get(key, 0) + count
    elif kind == "client":
        values = targets["mcp_clients"]
        values[key] = values.get(key, 0) + count


def _report_counters(home: Path, week: str) -> dict[str, Any]:
    targets = _report_counter_targets()
    try:
        if (home / DB_FILE).exists():
            with closing(_connect(home)) as con:
                rows = con.execute(
                    "SELECT surface, kind, key, n FROM counters WHERE week = ?", (week,)
                ).fetchall()
                for surface, kind, key, count in rows:
                    _add_report_counter(targets, surface, kind, key, count)
    except Exception as exc:  # noqa: BLE001
        _debug("telemetry_report_counters_failed", error=type(exc).__name__)
    targets.pop("nested")
    return targets


def _catalog_known_values() -> tuple[set[str], set[str], set[str], set[str]]:
    from gflow_cli.api.image import Aspect as ImageAspect
    from gflow_cli.api.image import Model as ImageModel
    from gflow_cli.api.image import aspect_choices as image_aspect_choices
    from gflow_cli.api.video import Aspect as VideoAspect
    from gflow_cli.api.video import VideoModel
    from gflow_cli.data.models import OperationKind, OperationStatus

    return (
        {model.value for model in ImageModel} | {model.value for model in VideoModel},
        {kind.value for kind in OperationKind},
        {status.value for status in OperationStatus},
        {aspect.value for aspect in ImageAspect}
        | set(image_aspect_choices().keys())
        | {aspect.value for aspect in VideoAspect}
        | {aspect.wire() for aspect in VideoAspect},
    )


def _count_catalog_value(
    target: dict[str, int], value: str | None, known: set[str], *, include_missing: bool = False
) -> None:
    if value or include_missing:
        key = value if value and value in known else "other"
        if "|" not in key:
            target[key] = target.get(key, 0) + 1


def _add_catalog_operation(
    operation: Any,
    week: str,
    known: tuple[set[str], set[str], set[str], set[str]],
    targets: dict[str, dict[str, int]],
) -> None:
    started_at = operation["started_at"]
    if not started_at:
        return
    try:
        if iso_week(datetime.fromisoformat(started_at.replace("Z", "+00:00"))) != week:
            return
    except Exception:
        return
    _count_catalog_value(targets["modes"], operation["mode"], known[1], include_missing=True)
    _count_catalog_value(targets["outcomes"], operation["status"], known[2], include_missing=True)
    _count_catalog_value(targets["models"], operation["model"], known[0])
    _count_catalog_value(targets["aspects"], operation["aspect_ratio"], known[3])


def _catalog_counts(week: str, path: Path) -> dict[str, dict[str, int]]:
    targets: dict[str, dict[str, int]] = {
        name: {} for name in ("models", "modes", "aspects", "outcomes")
    }
    if not path.is_file():
        return targets
    try:
        from gflow_cli.data.store import DataStore

        known = _catalog_known_values()
        year, week_number = (int(part) for part in week.split("-W"))
        week_start = datetime.fromisocalendar(year, week_number, 1).replace(tzinfo=UTC)
        week_end = week_start + timedelta(days=7)
        with DataStore.open_readonly(path) as store:
            rows = store.conn.execute(
                "SELECT model, mode, aspect_ratio, status, started_at FROM operations "
                "WHERE started_at >= ? AND started_at < ?",
                (week_start.isoformat(), week_end.isoformat()),
            ).fetchall()
        for operation in rows:
            _add_catalog_operation(operation, week, known, targets)
    except Exception as exc:  # noqa: BLE001
        _debug("telemetry_report_catalog_failed", error=type(exc).__name__)
    return targets


def build_report(week: str) -> dict[str, object]:
    """Assemble the weekly usage report for one ISO week."""
    home = _resolve_home()
    counters = _report_counters(home, week)
    catalog = _catalog_counts(week, _catalog_db_path())
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
        "commands": counters["commands"],
        "flags": counters["flags"],
        "exits": counters["exits"],
        "durations": counters["durations"],
        "transitions": counters["transitions"],
        "models": catalog["models"],
        "modes": catalog["modes"],
        "aspects": catalog["aspects"],
        "outcomes": catalog["outcomes"],
        "mcp_clients": counters["mcp_clients"],
        "crashes": counters["crashes"],
        "tools": counters["tools"],
    }
