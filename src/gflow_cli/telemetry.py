"""Local weekly usage counters and the kill switches that gate them (#1164).

Best-effort by contract: ``record`` never raises, prints or noticeably slows a
command. Counters hold only command/flag names, exit codes and coarse duration
buckets, in ``<home>/telemetry.db``. The home comes from ``_resolve_home()``,
never ``get_settings()``, so a broken configuration cannot silence or break this.
No connection is cached: a long-lived MCP server would otherwise pin the WAL.
"""

from __future__ import annotations

import os
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
from typing import Literal

import click
import structlog
from dotenv import dotenv_values

from gflow_cli.config import _env_files, _resolve_home  # pyright: ignore[reportPrivateUsage]

log = structlog.get_logger(__name__)


def _debug(event: str, **kw: str) -> None:
    # Unconfigured structlog prints to STDOUT (PrintLogger); observability.configure_logging
    # routes to stderr. Log only once configured, so stdout (MCP stdio, --json) stays clean.
    if structlog.is_configured():
        log.debug(event, **kw)


_RETAIN_WEEKS = 53
_TRANSITION_WINDOW_S = 30 * 60


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
            if prev and now.timestamp() - float(meta.get("prev_time", 0)) <= _TRANSITION_WINDOW_S:
                keys.append(("transition", f"{prev} -> {command}"))
            con.executemany(
                "INSERT INTO counters VALUES (?, ?, ?, ?, 1)"
                " ON CONFLICT(week, surface, kind, key) DO UPDATE SET n = n + 1",
                [(week, surface, kind, key) for kind, key in keys],
            )
            con.executemany(
                "INSERT OR REPLACE INTO meta VALUES (?, ?)",
                [("prev_command", command), ("prev_time", str(time.time()))],
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
