"""Auth — capture/refresh Google session via Playwright persistent context.

Sessions live under `Settings.home / profile_<name>/`. Default location is
the OS-native user-data-dir (via `platformdirs`):

  * Windows: `%LOCALAPPDATA%\\gflow-cli\\profile_<name>`
  * macOS:   `~/Library/Application Support/gflow-cli/profile_<name>`
  * Linux:   `~/.local/share/gflow-cli/profile_<name>` (XDG)

Override the root with `GFLOW_CLI_HOME`. See `docs/AUTHENTICATION.md`.
"""

from __future__ import annotations

import csv
import io
import subprocess
import sys
from typing import TYPE_CHECKING

import structlog

from gflow_cli.config import get_settings
from gflow_cli.paths import get_cookies_path

from .factory import AuthStrategyFactory
from .internal_chromium import InternalChromiumStrategy
from .real_chrome import RealChromeStrategy

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

__all__ = [
    "AuthStrategyFactory",
    "InternalChromiumStrategy",
    "RealChromeStrategy",
    "default_profile_root",
    "login",
    "profile_dir",
    "restrict_dir_to_current_user",
    "status",
]


def _current_user_sid() -> str:
    """The current user's SID via ``whoami /user /fo csv`` (locale-safe —
    header text localizes, the CSV shape and the SID cell do not)."""
    out = subprocess.run(
        ["whoami", "/user", "/fo", "csv"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    last_row = next(csv.reader(io.StringIO(out.stdout.strip().splitlines()[-1])))
    return last_row[-1]


def restrict_dir_to_current_user(path: Path) -> bool:
    """Best-effort Windows DACL hardening: strip inherited ACEs and grant only
    the current user, recursively (issue #472).

    POSIX mode bits (0700/0600) cover Unix; on Windows ``chmod`` is a no-op,
    so a profile created under a world-readable ``GFLOW_CLI_HOME`` inherits
    that visibility. Never raises — a hardening failure must not break login.
    Returns True only when the ACL was actually applied.
    """
    if sys.platform != "win32":
        return False
    try:
        sid = _current_user_sid()
        # Two steps, verified empirically: applying the inheritance-flagged
        # grant directly to files via /t leaves them without effective access
        # (PermissionError on read). Harden the top dir, then /reset children
        # so they INHERIT the single owner-only ACE.
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F", "/q"],
            check=True,
            capture_output=True,
            timeout=60,
        )
        subprocess.run(
            ["icacls", str(path / "*"), "/reset", "/t", "/q"],
            check=True,
            capture_output=True,
            timeout=120,  # /t rewrites every file in a Chromium profile
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, login must proceed
        logger.warning("auth_profile_acl_failed", error=type(exc).__name__)
        return False
    return True


def default_profile_root() -> Path:
    """Root dir under which `profile_<name>/` subdirectories live.

    Returns `Settings.home`. Reads env via `get_settings()` so changes to
    `GFLOW_CLI_HOME` after import are honoured (provided the cache is reset).
    """
    return get_settings().home


def profile_dir(name: str = "default") -> Path:
    return get_settings().profile_subdir(name)


async def login(name: str = "default", browser: str = "auto", headless: bool = False) -> Path:
    """Open a HEADED Chromium window, let user sign into Google, persist session.

    Returns the profile directory path. On subsequent runs the saved cookies
    are reused; if Google's session expires, calling this again re-captures it.
    """
    pdir = profile_dir(name)
    # Harden BEFORE the browser runs so cookies never land in a dir with
    # inherited world-readable ACLs (issue #472; no-op off Windows).
    pdir.mkdir(parents=True, exist_ok=True)
    restrict_dir_to_current_user(pdir)
    factory = AuthStrategyFactory()
    strategy = factory.create(browser)
    await strategy.login(pdir, headless=headless)
    return pdir


def status(name: str = "default") -> dict[str, object]:
    """Lightweight check — does the profile dir exist and have cookies file?"""
    pdir = profile_dir(name)

    cookies_file: Path | None
    try:
        cookies_file = get_cookies_path(pdir)
    except FileNotFoundError:
        cookies_file = None

    return {
        "profile": str(pdir),
        "exists": pdir.exists(),
        "cookies_present": cookies_file is not None,
        "cookies_path": str(cookies_file) if cookies_file else None,
    }
