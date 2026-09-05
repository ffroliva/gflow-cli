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

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
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


@dataclass(frozen=True)
class UpdateReport:
    installed: str
    latest: str | None  # None: PyPI unreachable
    installer: str
    command: tuple[str, ...]
    update_available: bool | None  # None: unknown (latest is None)
    upgraded: bool
    notes: tuple[str, ...] = ()  # post-upgrade caveats: stale launcher, Playwright bump


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
    if (prefix / "uv-receipt.toml").is_file():
        return Installer("uv", ("uv", "tool", "upgrade", "gflow-cli"))
    if (prefix / "pipx_metadata.json").is_file():
        return Installer("pipx", ("pipx", "upgrade", "gflow-cli"))
    return Installer("pip", (sys.executable, "-m", "pip", "install", "--upgrade", "gflow-cli"))


def _run_command(command: tuple[str, ...]) -> int:
    """Run the manager with inherited stdio so the user sees its own output."""
    return subprocess.run(list(command), check=False).returncode  # noqa: S603 — fixed argv


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


def run_update(*, check: bool) -> UpdateReport:
    """Upgrade gflow-cli in place, or with ``check`` only report.

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
    shown = " ".join(inst.command)
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
                f"Run `uv pip install --upgrade gflow-cli --python {sys.executable}` "
                "(or reinstall the way you installed it)."
            ),
        )
    playwright_before = _version_in_venv("playwright")
    returncode = _run_command(inst.command)
    # The venv is the truth, not the manager's exit code: on Windows `uv tool
    # upgrade` installs the new wheel and THEN fails copying `gflow.exe` over
    # the launcher running this very command (os error 32 — the trampoline
    # holds its own file open, so it cannot be renamed aside either). The
    # stale launcher keeps working: it only points at the venv's python.
    after = _version_in_venv("gflow-cli")
    log.info("update.command_finished", installer=inst.name, returncode=returncode, after=after)
    if after is None:
        raise ConfigurationError(
            f"`{shown}` exited {returncode}, and the installed version could not be re-read "
            "afterwards",
            remediation_hint="Run `gflow --version` to see what is installed now.",
        )
    if after == __version__:
        if returncode == 0 and available is None:
            return report  # PyPI was unreachable and the manager found nothing newer
        raise ConfigurationError(
            f"`{shown}` exited {returncode} and gflow-cli is still {__version__}",
            remediation_hint=f"Read its output above, then run `{shown}` yourself.",
        )
    notes: list[str] = []
    if returncode != 0:
        notes.append(
            f"`{shown}` exited {returncode} AFTER installing {after} — see its output above. "
            "On Windows this is the running `gflow.exe` launcher, which cannot be replaced "
            "while in use; it keeps working, and the next update from another shell "
            "refreshes it."
        )
    playwright_after = _version_in_venv("playwright")
    if playwright_before != playwright_after:
        notes.append(
            f"Playwright changed ({playwright_before} -> {playwright_after}); its browser "
            f"build must match: run `{sys.executable} -m playwright install chromium`."
        )
    return replace(report, latest=after, upgraded=True, notes=tuple(notes))
