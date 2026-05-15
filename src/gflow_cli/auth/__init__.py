"""Auth — capture/refresh Google session via Playwright persistent context.

Sessions live under `Settings.home / profile_<name>/`. Default location is
the OS-native user-data-dir (via `platformdirs`):

  * Windows: `%LOCALAPPDATA%\\gflow-cli\\profile_<name>`
  * macOS:   `~/Library/Application Support/gflow-cli/profile_<name>`
  * Linux:   `~/.local/share/gflow-cli/profile_<name>` (XDG)

Override the root with `GFLOW_CLI_HOME`. See `docs/AUTHENTICATION.md`.
"""

from __future__ import annotations

from pathlib import Path

from gflow_cli.config import get_settings

from .factory import AuthStrategyFactory
from .internal_chromium import InternalChromiumStrategy
from .real_chrome import RealChromeStrategy

__all__ = [
    "AuthStrategyFactory",
    "InternalChromiumStrategy",
    "RealChromeStrategy",
    "default_profile_root",
    "profile_dir",
    "status",
    "login",
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
    factory = AuthStrategyFactory()
    strategy = factory.create(browser)
    await strategy.login(pdir, headless=headless)
    return pdir


def status(name: str = "default") -> dict[str, object]:
    """Lightweight check — does the profile dir exist and have cookies file?"""
    pdir = profile_dir(name)
    cookies_file: Path | None = None
    for candidate in (pdir / "Default" / "Cookies", pdir / "Cookies"):
        if candidate.exists():
            cookies_file = candidate
            break
    return {
        "profile": str(pdir),
        "exists": pdir.exists(),
        "cookies_present": cookies_file is not None,
        "cookies_path": str(cookies_file) if cookies_file else None,
    }
