"""Once-a-day PyPI update notice (#479).

Best-effort by contract: every failure path — unreadable cache, offline host,
broken settings, PyPI shape change — resolves to "no notice", never an
exception, and nothing here ever blocks the command. The notice is always
served from the on-disk cache; a stale cache stamps ``checked_at``
SYNCHRONOUSLY (atomic tmp+replace, so the once-a-day cap holds even when the
fetch thread dies with a fast command) and then refreshes ``latest`` on a
daemon thread whose result feeds the NEXT invocation.

Skipped entirely when: ``GFLOW_CLI_UPDATE_CHECK=0``, a CI environment is
detected (``CI`` set to anything but ``0``/``false``), or gflow-cli is not an
index-installed wheel (PEP 610 ``direct_url.json`` present — editable, local
source, VCS, and direct-URL installs must not get index "upgrade" advice).
"""

from __future__ import annotations

import contextlib
import csv
import gc
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from threading import Thread
from typing import cast

import structlog

from gflow_cli import __version__
from gflow_cli.config import get_settings

log = structlog.get_logger(__name__)

_CHECK_INTERVAL_SECONDS = 86_400.0
_PYPI_JSON_URL = "https://pypi.org/pypi/gflow-cli/json"
_RELEASE_URL_BASE = "https://github.com/ffroliva/gflow-cli/releases/tag/"
_VERSION_CHARS = re.compile(r"[0-9A-Za-z.!+-]+")
_FETCH_TIMEOUT_SECONDS = 3.0  # background notice poll — must never be felt
_COMMAND_FETCH_TIMEOUT_SECONDS = 10.0  # explicit `gflow update` — the user is waiting for it


def _installed_from_index() -> bool:
    """True only for an index-installed wheel — the case where "upgrade"
    advice applies. PEP 610: index installs write no ``direct_url.json``;
    its mere presence (editable, local source, VCS, direct wheel URL) means
    ``pip install -U`` would silently replace a deliberate install. A pure
    source run has no distribution at all."""
    try:
        return distribution("gflow-cli").read_text("direct_url.json") is None
    except PackageNotFoundError:
        return False


def _in_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() not in ("", "0", "false")


def _is_newer(latest: str, installed: str) -> bool:
    # ponytail: base-version compare only — each dot part contributes its
    # leading digits and parsing stops at the first non-numeric part, so
    # "0.56.0rc1" -> (0, 56, 0) and "0.55.1.post1" -> (0, 55, 1). Ceiling: a
    # post/rc release of the SAME base version is not reported as newer.
    # Upgrade path: packaging.version.Version (new runtime dep) if that bites.
    def parse(version: str) -> tuple[int, ...] | None:
        parts: list[int] = []
        for part in version.split("."):
            match = re.match(r"\d+", part)
            if match is None:
                break
            parts.append(int(match.group()))
        return tuple(parts) or None

    latest_parts, installed_parts = parse(latest), parse(installed)
    if latest_parts is None or installed_parts is None:
        return False
    return latest_parts > installed_parts


def _read_cache(cache_path: Path) -> dict[str, object]:
    try:
        parsed: object = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return cast("dict[str, object]", parsed)


def _write_cache(cache_path: Path, payload: dict[str, object]) -> None:
    """Atomic tmp+replace (house pattern — a bare write_text can be torn by
    a daemon thread frozen at interpreter exit or a concurrent process)."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp_path, cache_path)
    except OSError as exc:
        log.debug("update_check.cache_write_failed", error=type(exc).__name__)


def fetch_latest(timeout: float) -> str | None:
    """The newest gflow-cli version on PyPI, or None on any failure."""
    try:
        import httpx  # lazy — keeps CLI startup free of the httpx import chain

        response = httpx.get(_PYPI_JSON_URL, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        latest = str(response.json()["info"]["version"])
    except Exception as exc:  # noqa: BLE001 — best-effort by contract
        log.debug("update_check.fetch_failed", error=type(exc).__name__)
        return None
    # Trust boundary: this string is cached and later rendered as Rich markup
    # and interpolated into a URL. PEP 440 needs nothing outside this set.
    if not _VERSION_CHARS.fullmatch(latest):
        log.debug("update_check.fetch_rejected", reason="not a version string")
        return None
    return latest


def _refresh_cache(cache_path: Path) -> None:
    """Fetch the latest version from PyPI and rewrite the cache. The caller
    already stamped ``checked_at``, so a failed fetch writes nothing and the
    once-a-day cap still holds."""
    latest = fetch_latest(_FETCH_TIMEOUT_SECONDS)
    if latest is not None:
        _write_cache(cache_path, {"checked_at": time.time(), "latest": latest})


@dataclass(frozen=True)
class UpdateNotice:
    """A newer release is on PyPI. ``text`` is the one-line form; the CLI
    renders a panel from the fields when stderr is a terminal."""

    installed: str
    latest: str

    @property
    def release_url(self) -> str:
        return f"{_RELEASE_URL_BASE}v{self.latest}"

    @property
    def text(self) -> str:
        return (
            f"A newer gflow-cli is available: {self.latest} (installed {self.installed}). "
            "Run `gflow update` to upgrade. Set GFLOW_CLI_UPDATE_CHECK=0 to silence."
        )


def maybe_notify_update() -> UpdateNotice | None:
    """The update notice to show the user, or None. Never raises."""
    try:
        settings = get_settings()
        if not settings.update_check or _in_ci():
            return None
        if not _installed_from_index():
            return None
        cache_path = Path(settings.home) / "update_check.json"
        cache = _read_cache(cache_path)
        checked_at = cache.get("checked_at")
        latest = cache.get("latest")
        now = time.time()
        # A future checked_at (clock skew, restored VM/backup) must count as
        # stale, or the cache stays "fresh" until the wall clock catches up.
        fresh = isinstance(checked_at, (int, float)) and 0 <= now - checked_at <= (
            _CHECK_INTERVAL_SECONDS
        )
        if not fresh:
            # Stamp checked_at SYNCHRONOUSLY before spawning the fetch: the
            # daemon thread dies with fast commands, and an unstamped cache
            # would fire a doomed PyPI request on every invocation.
            _write_cache(
                cache_path,
                {"checked_at": now, "latest": latest if isinstance(latest, str) else None},
            )
            Thread(target=_refresh_cache, args=(cache_path,), daemon=True).start()
        if isinstance(latest, str) and _is_newer(latest, __version__):
            return UpdateNotice(installed=__version__, latest=latest)
        return None
    except Exception as exc:  # noqa: BLE001 — the notice must never break a command
        log.debug("update_check.skipped", error=type(exc).__name__)
        return None


# --- `gflow update` — self-update through the installer that put us here ------


@dataclass(frozen=True)
class Installer:
    name: str  # "uv" | "pipx" | "pip"
    command: tuple[str, ...]
    # Chosen before the manager runs: a failed run can leave the extras uninstalled.
    spec: str = "gflow-cli"


@dataclass(frozen=True)
class UpdateReport:
    installed: str
    latest: str | None  # None: PyPI unreachable
    installer: str
    command: tuple[str, ...]
    update_available: bool | None  # None: unknown (latest is None)
    upgraded: bool
    notes: tuple[str, ...] = ()  # post-upgrade caveats: stale launcher, Playwright bump


def _is_user_install() -> bool:
    """True when gflow-cli sits in the user site (``pip install --user``), not a venv.

    Such an install must be upgraded with ``--user``: without it pip writes a second copy
    into the interpreter's own site-packages, which the user site still shadows (#1298)."""
    if sys.prefix != sys.base_prefix:
        return False
    import site

    try:
        here = Path(str(distribution("gflow-cli").locate_file(""))).resolve()
    except PackageNotFoundError:
        return False
    return here == Path(site.getusersitepackages()).resolve()


_EXTRA_MARKER = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")
_REQ_NAME = re.compile(r"[A-Za-z0-9._-]+")


def _installed_extras() -> tuple[str, ...]:
    """The gflow-cli extras whose every requirement is installed.

    pip upgrades only what it is asked for, so `gflow-cli` without its extras leaves an
    extra's own pin where it was (#1322)."""
    try:
        requires = distribution("gflow-cli").requires or []
    except PackageNotFoundError:
        return ()
    by_extra: dict[str, list[str]] = {}
    for req in requires:
        spec, _, marker = req.partition(";")
        extra, name = _EXTRA_MARKER.search(marker), _REQ_NAME.match(spec.strip())
        if extra and name:
            by_extra.setdefault(extra[1], []).append(name[0])

    def present(name: str) -> bool:
        try:
            distribution(name)
        except PackageNotFoundError:
            return False
        return True

    # `dev` is the contributor toolchain, never a user extra: requesting it would pin the
    # user's own ruff/pytest to ours.
    return tuple(
        sorted(e for e, names in by_extra.items() if e != "dev" and all(map(present, names)))
    )


def _package_spec() -> str:
    """``gflow-cli`` with its installed extras, e.g. ``gflow-cli[patchright]``."""
    extras = _installed_extras()
    return f"gflow-cli[{','.join(extras)}]" if extras else "gflow-cli"


def installer() -> Installer | None:
    """Which package manager owns this install, and the command that upgrades
    it. None for anything that is not an index install (editable, local path,
    VCS, bare source run) — there is no safe "upgrade" for those.

    The manager is read off the venv root: ``uv tool`` writes
    ``uv-receipt.toml`` there and pipx writes ``pipx_metadata.json``. Anything
    else is a plain venv, upgraded through its OWN interpreter — never a bare
    ``pip`` from PATH, which may belong to another Python."""
    if not _installed_from_index():
        return None
    prefix = Path(sys.prefix)
    spec = _package_spec()
    if (prefix / "uv-receipt.toml").is_file():
        return Installer("uv", ("uv", "tool", "upgrade", "gflow-cli"), spec)
    if (prefix / "pipx_metadata.json").is_file():
        return Installer("pipx", ("pipx", "upgrade", "gflow-cli"), spec)
    pip_cmd = [sys.executable, "-m", "pip", "install", "--upgrade"]
    if _is_user_install():
        pip_cmd.append("--user")
    pip_cmd.append(spec)
    return Installer("pip", tuple(pip_cmd), spec)


def shown_command(command: tuple[str, ...]) -> str:
    """The command as a user can paste it: a ``[extras]`` spec quoted against shell globbing."""
    return " ".join(f'"{arg}"' if "[" in arg else arg for arg in command)


def _show_output(stdout: str | None, stderr: str | None) -> None:
    """Put the manager's captured output on stderr (nothing when it streamed)."""
    output = "\n".join(filter(None, (stdout, stderr))).strip()
    if output:
        sys.stderr.write(f"{output}\n")


def _run_command(
    command: tuple[str, ...], *, capture_output: bool
) -> subprocess.CompletedProcess[str]:
    """Run the manager subprocess: on the terminal, or captured."""
    if not capture_output:
        return subprocess.run(list(command), text=True, check=False)  # noqa: S603 — fixed argv
    with subprocess.Popen(  # noqa: S603 — fixed argv
        list(command),
        stdin=subprocess.DEVNULL,  # a prompt would be invisible and look like a hang
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        # pip / pipx on Windows otherwise write the console code page; uv is UTF-8 anyway.
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    ) as proc:
        try:
            stdout, stderr = proc.communicate()
        except KeyboardInterrupt:
            proc.kill()
            try:
                _show_output(*proc.communicate(timeout=5))
            except subprocess.TimeoutExpired:
                # A grandchild still holds the pipes, and closing them in __exit__ would
                # wait for it (measured on Windows). Leave them to the dying process.
                proc.stdout = proc.stderr = None
            raise
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def _version_in_venv(dist: str) -> str | None:
    """``dist``'s version as the venv sees it NOW — asked of a fresh
    interpreter, because this process imported the pre-upgrade modules."""
    probe = f"import importlib.metadata as m; print(m.version({dist!r}))"
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


_IMPORT_PROBE = "import gflow_cli.cli"


def _import_failure() -> str | None:
    """Why a fresh interpreter can no longer import gflow-cli, or None if it can.

    ``_version_in_venv`` cannot see a half-replaced environment: it reads
    ``*.dist-info`` and never touches the modules the next command imports.
    #848 measured exactly that gap — an interrupted native-dependency
    replacement left every command dying on ``AttributeError: module
    'greenlet' has no attribute 'greenlet'`` while the reported version stayed
    perfectly readable, so `gflow update` called it "still 0.69.0" and sent the
    user to a plain `uv tool upgrade` that could not repair it.

    ``-I`` so a ``gflow_cli`` directory in the user's CWD — or on PYTHONPATH —
    cannot answer on the venv's behalf. A ``--user`` install lives in the user site,
    which ``-I`` hides, so it gets ``-E -P``: no PYTHON* variables, no CWD, user site
    kept. A probe that could not run at all returns None: an environment we failed
    to ask about must never be reported as a broken one.
    """
    isolation = ("-E", "-P") if _is_user_install() else ("-I",)
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv
            [sys.executable, *isolation, "-c", _IMPORT_PROBE],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return None
    lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
    return lines[-1] if lines else f"exit code {result.returncode}"


def _reinstall_hint(inst: Installer, version: str | None) -> str:
    """The command that repairs a half-replaced environment.

    A plain ``upgrade`` does not: it reads the metadata, finds it current and
    exits 0 having changed nothing — measured in #848, where only a forced
    reinstall completed."""
    pin = f"{inst.spec}=={version}" if version else inst.spec
    user_flag = "--user " if "--user" in inst.command else ""
    repair = {
        "uv": f'uv tool install "{pin}" --force --reinstall',
        "pipx": f'pipx install --force "{pin}"',
    }.get(inst.name, f'{sys.executable} -m pip install --force-reinstall {user_flag}"{pin}"')
    return (
        "Close every running gflow process (including any `gflow serve` / MCP server), "
        f"then run `{repair}`."
    )


def _require_runnable(inst: Installer, shown: str) -> None:
    """Raise ConfigurationError (exit 11) when the manager cannot be started here."""
    from gflow_cli.errors import ConfigurationError

    if inst.name != "pip" and shutil.which(inst.command[0]) is None:
        raise ConfigurationError(
            f"`{inst.command[0]}` is not on PATH, so gflow cannot run `{shown}` for you",
            remediation_hint=f"Run `{shown}` from a shell where `{inst.command[0]}` is available.",
        )
    if inst.name == "pip" and importlib.util.find_spec("pip") is None:
        # A `uv venv` ships no pip module: `python -m pip` would fail and the
        # remediation would then recommend the same broken command.
        raise ConfigurationError(
            "This venv has no `pip` module, so gflow cannot upgrade itself here",
            remediation_hint=(
                f'Run `uv pip install --upgrade "{inst.spec}" --python {sys.executable}` '
                "(or reinstall the way you installed it)."
            ),
        )


_WINERROR_IN_USE = 32  # ERROR_SHARING_VIOLATION


def _record_launchers() -> list[Path]:
    """Every ``.exe`` launcher gflow-cli's RECORD lists, present on disk or not.

    Read from RECORD itself: from Python 3.12 ``Distribution.files`` drops entries
    missing on disk, which would hide a launcher an interrupted update left aside."""
    dist = distribution("gflow-cli")
    rows = csv.reader((dist.read_text("RECORD") or "").splitlines())
    return [Path(str(dist.locate_file(r[0]))) for r in rows if r and r[0].lower().endswith(".exe")]


def _restore_launchers(moved: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """Put back every launcher pip did not recreate, returning any still missing."""
    unrestored: list[tuple[Path, Path]] = []
    for orig, aside in moved:
        if not orig.exists() and aside.exists():
            try:
                os.replace(aside, orig)
            except OSError as exc:
                log.warning(
                    "update.launcher_restore_failed",
                    launcher=str(orig),
                    aside=str(aside),
                    error=str(exc),
                )
        if not orig.exists():
            unrestored.append((orig, aside))
    return unrestored


def _move_aside(launchers: list[Path], moved: list[tuple[Path, Path]]) -> None:
    """Rename each launcher to ``<name>.gflow-update-<pid>.old``, appending to ``moved``.

    A launcher in use (WinError 32: another gflow process holds it) or one that vanished
    mid-rename (another ``gflow update`` moved it) refuses the update before pip runs,
    with everything restored. Any other error (access denied on an all-users Python)
    means pip cannot remove the launcher either and falls back as it did before #1298,
    so the update goes ahead without the guard."""
    from gflow_cli.errors import ConfigurationError

    pid = os.getpid()
    for p in launchers:
        aside = p.with_name(f"{p.name}.gflow-update-{pid}.old")
        try:
            os.replace(p, aside)
        except OSError as exc:
            _restore_launchers(moved)
            moved.clear()
            if isinstance(exc, FileNotFoundError):
                raise ConfigurationError(
                    f"`{p}` disappeared during the update, so `gflow update` changed nothing",
                    remediation_hint=(
                        "Another `gflow update` may be running. Wait for it to finish, then "
                        "run `gflow update` again."
                    ),
                ) from exc
            if getattr(exc, "winerror", None) != _WINERROR_IN_USE:
                log.warning("update.launcher_not_set_aside", launcher=str(p), error=str(exc))
                return
            raise ConfigurationError(
                f"`{p}` is in use by another program, so `gflow update` changed nothing",
                remediation_hint=(
                    "Close every program that may be running gflow (an MCP server, "
                    "`gflow serve`, another terminal), then run `gflow update` again."
                ),
            ) from exc
        moved.append((p, aside))


@contextlib.contextmanager
def _launchers_set_aside(inst: Installer) -> Generator[None, None, None]:
    """Set the launchers aside while pip runs, on a Windows pip install (#1298).

    pip uninstalls before it installs. A launcher held open fails that uninstall
    halfway, after ``gflow_cli`` is gone, and pip does not roll back. Every gflow
    process holds its own launcher through importlib.metadata's cached zip handle on
    ``sys.path[0]``. This process drops that hold and renames its launchers aside, so
    pip writes fresh ones; another process's hold refuses the update before pip runs.
    No metadata call may happen between the release and the end of pip."""
    if inst.name != "pip" or sys.platform != "win32":
        yield
        return
    from gflow_cli.errors import ConfigurationError

    launchers = _record_launchers()
    missing = next((p for p in launchers if not p.is_file()), None)
    if missing is not None:
        raise ConfigurationError(
            f"Launcher `{missing}` is missing (an earlier update may have been interrupted)",
            remediation_hint=_reinstall_hint(inst, __version__),
        )
    importlib.invalidate_caches()
    gc.collect()
    moved: list[tuple[Path, Path]] = []
    pip_returned = False
    try:  # one finally for the renames and pip: Ctrl-C or any error anywhere restores
        _move_aside(launchers, moved)
        yield
        pip_returned = True
    finally:
        unrestored = _restore_launchers(moved)
    if pip_returned and unrestored:  # never masks an exception already on its way out
        details = ", ".join(f"`{orig}` (saved at `{aside}`)" for orig, aside in unrestored)
        raise ConfigurationError(
            f"Could not restore launcher(s) {details} after `gflow update`",
            # Unpinned: pip may already have installed a newer release, and pinning this
            # process's version would tell the user to downgrade.
            remediation_hint=_reinstall_hint(inst, None),
        )


def _sweep_set_aside() -> None:
    """Delete ``.old`` launchers an earlier update left, on Windows (#1298).

    Only where the launcher itself exists: a missing one means an update was
    interrupted, and its ``.old`` is the evidence. A ``.old`` still in use (its
    process has not exited) is skipped; the next run removes it. Runs on
    ``--check`` too: it changes nothing but leftovers of gflow's own."""
    if sys.platform != "win32":
        return
    try:
        launchers = _record_launchers()
    except PackageNotFoundError:
        return
    for p in launchers:
        if p.is_file():
            for old in p.parent.glob(f"{p.name}.gflow-update-*.old"):
                with contextlib.suppress(OSError):
                    os.remove(old)


def run_update(
    *,
    check: bool,
    stream_output: bool = False,
    status_callback: Callable[[str], None] | None = None,
) -> UpdateReport:
    """Upgrade gflow-cli in place, or with ``check`` only report.

    ``stream_output`` runs the manager on the terminal; otherwise its output is
    captured and shown only when the manager or the update fails.

    Raises :class:`~gflow_cli.errors.ConfigurationError` (exit 11) for a
    non-index install, a manager binary missing from PATH, or a manager that
    exits non-zero. A failed PyPI pre-check is NOT fatal: the manager is the
    authority on what is installable, so it still runs."""
    from gflow_cli.errors import ConfigurationError

    inst = installer()
    if inst is None:
        raise ConfigurationError(
            "This gflow-cli is not a PyPI install (editable, local-path, VCS or source run)",
            remediation_hint=(
                "Update it the way it was installed — `git pull` / reinstall from the "
                "checkout. `gflow update` only manages index installs (uv tool / pipx / pip)."
            ),
        )
    _sweep_set_aside()
    log.info("update.installer_detected", installer=inst.name)
    latest = fetch_latest(_COMMAND_FETCH_TIMEOUT_SECONDS)
    if latest is not None:
        _write_cache(
            Path(get_settings().home) / "update_check.json",
            {"checked_at": time.time(), "latest": latest},
        )
    available = _is_newer(latest, __version__) if latest is not None else None
    report = UpdateReport(
        installed=__version__,
        latest=latest,
        installer=inst.name,
        command=inst.command,
        update_available=available,
        upgraded=False,
    )
    if check or available is False:
        return report

    shown = shown_command(inst.command)
    _require_runnable(inst, shown)
    playwright_before = _version_in_venv("playwright")
    if status_callback is not None:
        target = f"{__version__} -> {latest}" if latest else __version__
        status_callback(f"Updating gflow-cli {target} via {inst.name}...")
    with _launchers_set_aside(inst):
        res = _run_command(inst.command, capture_output=not stream_output)
    returncode = res.returncode

    # The venv is the truth, not the manager's exit code: on Windows `uv tool
    # upgrade` installs the new wheel into site-packages and then fails copying
    # `gflow.exe` into its bin dir over the launcher running this very command
    # (os error 32). The stale launcher keeps working: it only points at the
    # venv's python.
    after = _version_in_venv("gflow-cli")
    log.info("update.command_finished", installer=inst.name, returncode=returncode, after=after)
    # Before any version arithmetic: is the environment still usable at all?
    # The manager replaces files in place, so an interrupted run leaves a venv
    # whose metadata reads fine and whose imports do not (#848). That state is
    # worse than not upgrading, and the version-based branches below cannot
    # see it — they would report "still <version>" for an install that no
    # longer starts, and recommend a command that cannot repair it.
    broken = _import_failure()
    if broken is not None:
        log.warning("update.environment_broken", installer=inst.name, returncode=returncode)
        _show_output(res.stdout, res.stderr)
        raise ConfigurationError(
            f"`{shown}` left this gflow-cli install unusable: a fresh interpreter can no "
            f"longer import it ({broken})",
            remediation_hint=_reinstall_hint(inst, latest or after),
        )
    if after is None:
        _show_output(res.stdout, res.stderr)
        raise ConfigurationError(
            f"`{shown}` exited {returncode}, and the installed version could not be re-read "
            "afterwards",
            remediation_hint="Run `gflow --version` to see what is installed now.",
        )
    if after == __version__:
        if returncode == 0 and available is None:
            return report  # PyPI was unreachable and the manager found nothing newer
        _show_output(res.stdout, res.stderr)
        raise ConfigurationError(
            f"`{shown}` exited {returncode} and gflow-cli is still {__version__}",
            remediation_hint=f"Read its output above, then run `{shown}` yourself.",
        )
    notes: list[str] = []
    if returncode != 0:
        _show_output(res.stdout, res.stderr)
        notes.append(
            f"Note: `{shown}` exited {returncode} AFTER installing {after}. On Windows this is "
            "usually the installer failing to copy over the launcher running this command "
            "(uv tool does this); that launcher keeps working."
        )
    playwright_after = _version_in_venv("playwright")
    if playwright_before != playwright_after:
        notes.append(
            f"Playwright changed ({playwright_before} -> {playwright_after}); its browser "
            f"build must match: run `{sys.executable} -m playwright install chromium`."
        )
    return replace(report, latest=after, upgraded=True, notes=tuple(notes))
