"""Chrome discovery and Playwright-channel helpers.

.. note::

   This module previously also shipped a packaged CDP (Chrome DevTools
   Protocol) attach/spawn lifecycle — ``get_or_launch_browser`` /
   ``close_browser`` / lockfile-managed detached Chrome — for research use
   and non-Flow use cases. It was removed 2026-07-19 (see
   ``.superpowers/sdd/cdp-decision.md`` in the
   ``chore/production-readiness-hardening`` history): zero production
   consumers shipped in the wheel, the no-lock attach path would connect to
   *any* Chrome answering on the port (a documented hijack vector), and the
   externally-discoverable CDP debug port this pattern relies on was itself
   rejected by Google's Flow surface on `aisandbox-pa.googleapis.com` in a
   2026-05-12 empirical test. The production transport is
   :class:`gflow_cli.api.transports.ui_automation.UiAutomationTransport`,
   which uses Playwright's internal CDP port (Playwright manages it
   privately, never externally exposed) rather than an externally-exposed
   debug port. CDP-attach as a distinct opt-in transport remains a parked
   backlog idea — see PLAN.md ADR #13 and the "CDP Attach Transport —
   BACKLOG" section — should a future contributor want to revisit it with a
   safe ownership model.

Single responsibility: locate the system Chrome binary and decide which
Playwright ``channel`` (if any) a given profile should launch with.

Public API
----------
is_chrome_available() -> bool
    True if a Google Chrome (or Chromium fallback) binary can be found.

resolved_chrome_binary() -> str | None
    The resolved Chrome binary path, or None. Never raises.

channel_for_profile(profile_dir) -> str | None
    ``"chrome"`` if the profile's strategy marker requests it AND Google
    Chrome proper is available at Playwright's expected paths; else None.

chrome_strategy_requested(profile_dir) -> bool
    True if the profile's ``.gflow_browser_strategy`` marker requests
    system Chrome (independent of whether Chrome is actually available).

Internal helpers (exported for tests)
--------------------------------------
_find_chrome_binary() -> str
_is_playwright_chrome_channel_available() -> bool
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import structlog

from gflow_cli.errors import ConfigurationError

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Chrome binary detection
# ---------------------------------------------------------------------------


def _find_chrome_binary() -> str:
    """Locate the system Chrome binary.

    Resolution order:
    1. ``CHROME_BINARY`` env var (override)
    2. ``shutil.which("chrome")`` / ``shutil.which("google-chrome")``
    3. Platform-standard install paths (including Chromium as last resort for
       non-Playwright-channel uses such as auth login).

    .. note::
        This function accepts Chromium as a fallback for the auth use-case.
        It must NOT be used to decide whether Playwright's ``channel="chrome"``
        is available — use :func:`_is_playwright_chrome_channel_available` for
        that, which checks only the exact paths Playwright hard-codes.

    Raises ``ConfigurationError`` if nothing found.
    """
    # 1. Environment override
    env_override = os.environ.get("CHROME_BINARY")
    if env_override:
        return env_override

    # 2. PATH probe — Google Chrome names first, Chromium last-resort
    for candidate in ("chrome", "google-chrome", "chromium"):
        found = shutil.which(candidate)
        if found:
            return found

    # 3. Platform-specific standard paths
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        platform_paths = [
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            Path(local_app_data or "") / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
    elif sys.platform == "darwin":
        platform_paths = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:
        # Linux / other POSIX
        platform_paths = [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
        ]

    for p in platform_paths:
        if p.exists():
            return str(p)

    msg = (
        "Chrome not found. Install from https://www.google.com/chrome/"
        " or set CHROME_BINARY env var."
    )
    raise ConfigurationError(
        msg,
    )


def _is_playwright_chrome_channel_available() -> bool:
    """Return True only when Playwright's ``channel="chrome"`` can find Chrome.

    Playwright's ``launch_persistent_context(channel="chrome")`` looks for
    **Google Chrome proper** at platform-specific hardcoded paths — it does NOT
    accept a plain Chromium binary. This function replicates those paths so
    :func:`channel_for_profile` can gate the ``channel="chrome"`` argument on a
    binary that Playwright will actually find, avoiding the misleading
    ``Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome``
    error that occurs when only system Chromium is present.

    The ``CHROME_BINARY`` env var override is honoured for parity with
    :func:`_find_chrome_binary`.
    """
    env_override = os.environ.get("CHROME_BINARY")
    if env_override:
        return True

    # Playwright's own resolution paths for channel="chrome". Derived from
    # playwright/_impl/_browser_type.py executables(). channel="chrome" resolves
    # ONLY to these exact Google-Chrome paths — a system Chromium does NOT
    # satisfy it, so we must not list Chromium or distro chrome shims here.
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        chrome_paths: list[Path] = [
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            Path(local_app_data or "") / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
    elif sys.platform == "darwin":
        chrome_paths = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:
        # Linux — Playwright resolves channel="chrome" only to this path.
        chrome_paths = [
            Path("/opt/google/chrome/chrome"),
        ]

    return any(p.exists() for p in chrome_paths)


def is_chrome_available() -> bool:
    """Return True if a Google Chrome (or Chromium fallback) binary can be found.

    This is used for the auth login flow. It intentionally accepts Chromium as
    a fallback so the auth browser can open even when only Chromium is
    installed. For deciding whether Playwright's ``channel="chrome"`` can be
    used, call :func:`_is_playwright_chrome_channel_available` instead.
    """
    try:
        _find_chrome_binary()
        return True
    except ConfigurationError:
        return False


def resolved_chrome_binary() -> str | None:
    """Return the system Chrome binary path, or ``None`` if Chrome can't be found.

    Public, exception-free wrapper around :func:`_find_chrome_binary` for
    diagnostics/logging callers that want the path without handling errors. It
    must NEVER raise: it feeds ``_log_and_guard_launch``'s observability log, and
    a best-effort path probe should never abort a launch. Besides
    :class:`ConfigurationError` (no binary found), ``shutil.which`` can raise on
    odd environments (e.g. an OS-detection mismatch), so any error resolves to
    ``None``.
    """
    try:
        return _find_chrome_binary()
    except Exception:  # noqa: BLE001 — exception-free by contract; any failure → None
        return None


def channel_for_profile(profile_dir: Path) -> str | None:
    """Return the Playwright channel to use for ``profile_dir``, or None.

    Reads the ``.gflow_browser_strategy`` marker written by
    :class:`~gflow_cli.auth.real_chrome.RealChromeStrategy`. When the marker
    is ``"chrome"`` and **Google Chrome proper** is available at the paths
    Playwright expects, returns ``"chrome"`` so callers can pass it to
    ``launch_persistent_context(channel=...)`` — avoiding the downgrade-cleanup
    exit-33 that occurs when Playwright's bundled Chromium opens a profile
    created by Chrome 130+.

    Critically, this gate uses :func:`_is_playwright_chrome_channel_available`
    (not :func:`is_chrome_available`) so that a system with only Chromium
    installed does NOT request ``channel="chrome"`` — Playwright's
    ``channel="chrome"`` resolves to hardcoded Google-Chrome paths and would
    otherwise fail with
    ``Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome``.

    Logs a warning when the marker requests Chrome but Chrome is no longer
    available at the expected Playwright paths, as the resulting launch against
    bundled Chromium will likely fail with the same exit-33 error.
    """
    import structlog as _structlog

    _log = _structlog.get_logger(__name__)
    marker = profile_dir / ".gflow_browser_strategy"
    if not marker.exists():
        return None
    strategy = marker.read_text(encoding="utf-8").strip()
    if strategy != "chrome":
        return None
    if _is_playwright_chrome_channel_available():
        return "chrome"
    _log.warning(
        "browser_manager.chrome_marker_but_unavailable",
        profile_dir=str(profile_dir),
        hint="Profile was captured with system Chrome but Google Chrome is not "
        "found at the paths Playwright expects (e.g. /opt/google/chrome/chrome). "
        "Falling back to Playwright's bundled Chromium. "
        "Re-run `gflow auth login --browser chrome` after installing Chrome if "
        "you need the Chrome channel.",
    )
    return None


def chrome_strategy_requested(profile_dir: Path) -> bool:
    """True if the profile's ``.gflow_browser_strategy`` marker requests system Chrome.

    Distinct from :func:`channel_for_profile`, which returns ``None`` both when no
    marker is present (legitimate bundled-Chromium use) AND when the marker says
    ``chrome`` but Chrome can't be found (a silent downgrade). Callers use this to
    tell those two cases apart — see ``FlowApiClient._log_and_guard_launch`` and
    issue #222 (macOS: a chrome→bundled downgrade yields cookies the bundled
    Chromium can't decrypt → 401).
    """
    marker = profile_dir / ".gflow_browser_strategy"
    try:
        return marker.exists() and marker.read_text(encoding="utf-8").strip() == "chrome"
    except OSError:
        return False
