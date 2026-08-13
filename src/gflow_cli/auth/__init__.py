"""Auth — capture/refresh Google session via Playwright persistent context.

Sessions live under `Settings.home / profile_<name>/`. Default location is
the OS-native user-data-dir (via `platformdirs`):

  * Windows: `%LOCALAPPDATA%\\gflow-cli\\profile_<name>`
  * macOS:   `~/Library/Application Support/gflow-cli/profile_<name>`
  * Linux:   `~/.local/share/gflow-cli/profile_<name>` (XDG)

Override the root with `GFLOW_CLI_HOME`. See `docs/AUTHENTICATION.md`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gflow_cli.config import get_settings
from gflow_cli.errors import SecurityError
from gflow_cli.paths import get_cookies_path
from gflow_cli.winsec import ensure_profile_hardened, restrict_dir_to_current_user

from .factory import AuthStrategyFactory
from .internal_chromium import InternalChromiumStrategy
from .real_chrome import RealChromeStrategy

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "AuthStrategyFactory",
    "InternalChromiumStrategy",
    "RealChromeStrategy",
    "default_profile_root",
    "ensure_profile_hardened",
    "login",
    "profile_dir",
    "restrict_dir_to_current_user",
    "status",
]


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
    # Boundary check BEFORE any mkdir or ACL rewrite: a traversal profile
    # name must never create — let alone re-ACL — a directory outside
    # GFLOW_CLI_HOME. The strategies re-validate, but they run too late to
    # guard the orchestrator-level mkdir/harden below.
    home = get_settings().home.resolve()
    if not pdir.resolve().is_relative_to(home):
        msg = f"Profile directory {pdir} is outside of GFLOW_CLI_HOME ({home})."
        raise SecurityError(msg)
    # Harden BEFORE the browser runs so cookies never land in a dir with
    # inherited world-readable ACLs (issue #472; no-op off Windows).
    pdir.mkdir(parents=True, exist_ok=True)
    ensure_profile_hardened(pdir)
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
