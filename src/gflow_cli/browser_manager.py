"""BrowserManager — persistent system Chrome via CDP attach.

.. note::

   This module is retained for research use and non-Flow use cases. It is
   **not** on the v0.5.0a1 image-generation critical path; the production
   transport is :class:`gflow_cli.api.transports.ui_automation.UiAutomationTransport`,
   which uses Playwright's internal CDP port (Playwright manages it
   privately) rather than an externally-exposed debug port.

   Empirical results from 2026-05-12 showed that an externally-discoverable
   CDP debug port (the pattern this module implements) is itself rejected
   by Google's Flow surface on `aisandbox-pa.googleapis.com`. The module
   stays in the codebase so the lifecycle helpers remain available for
   any future tool that needs a managed Chrome process for a non-Flow
   target, but ``gflow image t2i`` / ``gflow image i2i`` / ``gflow run``
   do not route through here.

Single responsibility: manage a long-lived system Chrome process that
survives CLI invocations. Subsequent CLI calls attach via CDP instead of
spawning fresh Playwright Chromium.

Public API
----------
get_or_launch_browser(profile_dir, port=9222) -> BrowserContext
    Attach to running Chrome on ``port`` if alive; otherwise spawn detached
    Chrome and attach. Returns a Playwright BrowserContext.

close_browser(profile_dir, port=9222) -> None
    Opt-in shutdown.

is_browser_running(port=9222) -> bool
    Sync health check. CDP ``GET /json/version`` with 3s timeout + 1 retry.

Internal helpers (exported for tests)
--------------------------------------
_find_chrome_binary() -> str
_spawn_chrome(binary, profile_dir, port) -> subprocess.Popen
_find_available_cdp_port(profile_dir, start_port) -> int
_check_chrome_singleton_lock(profile_dir) -> None
_pid_alive(pid) -> bool
_is_logged_in_to_flow(page) -> bool  (async, awaits Playwright Locator.count)
_connect_cdp(endpoint) -> BrowserContext  (async, patched in tests)
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import structlog

from gflow_cli.errors import AuthMissingError, ConfigurationError

log = structlog.get_logger(__name__)

_FLOW_HOME_URL = "https://labs.google/fx/tools/flow"

# Port range for CDP auto-increment (9222 … 9229 inclusive — 8 ports)
_CDP_PORT_START = 9222
_CDP_PORT_END = 9229

# Lockfile name inside the profile directory
_LOCK_FILENAME = ".gflow-cdp.lock"

# Chrome startup grace period after spawn (seconds)
_SPAWN_WAIT_SECONDS = 3.0

# Health-check timeout (seconds) — bumped to 3s to survive cold-boot / GC pauses
_HEALTH_TIMEOUT = 3


# ---------------------------------------------------------------------------
# PID liveness
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    """Cross-platform PID liveness check. Never raises."""
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    else:
        # POSIX: os.kill(pid, 0) raises if PID doesn't exist / no permission
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we don't own it — still alive
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Chrome binary detection
# ---------------------------------------------------------------------------


def _find_chrome_binary() -> str:
    """Locate the system Chrome binary.

    Resolution order:
    1. ``CHROME_BINARY`` env var (override)
    2. ``shutil.which("chrome")`` / ``shutil.which("google-chrome")``
    3. Platform-standard install paths

    Raises ``ConfigurationError`` if nothing found.
    """
    # 1. Environment override
    env_override = os.environ.get("CHROME_BINARY")
    if env_override:
        return env_override

    # 2. PATH probe
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

    raise ConfigurationError(
        "Chrome not found. Install from https://www.google.com/chrome/"
        " or set CHROME_BINARY env var."
    )


def is_chrome_available() -> bool:
    """Return True if a Google Chrome binary can be found on this system."""
    try:
        _find_chrome_binary()
        return True
    except ConfigurationError:
        return False


def channel_for_profile(profile_dir: Path) -> str | None:
    """Return the Playwright channel to use for ``profile_dir``, or None.

    Reads the ``.gflow_browser_strategy`` marker written by
    :class:`~gflow_cli.auth.real_chrome.RealChromeStrategy`. When the marker
    is ``"chrome"`` and system Chrome is available, returns ``"chrome"`` so
    callers can pass it to ``launch_persistent_context(channel=...)`` —
    avoiding the downgrade-cleanup exit-33 that occurs when Playwright's
    bundled Chromium opens a profile created by Chrome 130+.

    Logs a warning when the marker requests Chrome but Chrome is no longer
    available, as the resulting launch against bundled Chromium will likely
    fail with the same exit-33 error.
    """
    import structlog as _structlog

    _log = _structlog.get_logger(__name__)
    marker = profile_dir / ".gflow_browser_strategy"
    if not marker.exists():
        return None
    strategy = marker.read_text(encoding="utf-8").strip()
    if strategy != "chrome":
        return None
    if is_chrome_available():
        return "chrome"
    _log.warning(
        "browser_manager.chrome_marker_but_unavailable",
        profile_dir=str(profile_dir),
        hint="Profile was captured with system Chrome but Chrome is not found. "
        "Re-run `gflow auth login --browser chrome` after installing Chrome.",
    )
    return None


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


def is_browser_running(port: int = 9222) -> bool:
    """Sync health check via CDP ``GET /json/version``.

    Returns True if a Chrome instance responds on ``port``.
    Never raises — all exceptions return False.
    Uses 3-second timeout with ONE retry on timeout.
    """
    url = f"http://localhost:{port}/json/version"

    def _attempt() -> bool:
        try:
            resp = httpx.get(url, timeout=_HEALTH_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            # Sanity-check: must be a dict with at least a "Browser" key
            if not isinstance(data, dict) or "Browser" not in data:
                return False
            return True
        except httpx.TimeoutException:
            raise  # re-raise so caller can retry
        except Exception:
            return False

    try:
        return _attempt()
    except httpx.TimeoutException:
        # ONE retry on timeout
        try:
            return _attempt()
        except Exception:
            return False


# ---------------------------------------------------------------------------
# CDP attach (thin wrapper — easy to patch in tests)
# ---------------------------------------------------------------------------


async def _connect_cdp(endpoint: str) -> Any:
    """Connect Playwright to a running Chrome via CDP endpoint.

    Separated into its own function so tests can patch it without
    needing a real Playwright installation.
    """
    from playwright.async_api import async_playwright  # type: ignore[import]

    playwright = await async_playwright().start()
    browser = await playwright.chromium.connect_over_cdp(endpoint)
    contexts = browser.contexts
    if contexts:
        return contexts[0]
    return await browser.new_context()


# ---------------------------------------------------------------------------
# Singleton lock (Chrome's own profile lock)
# ---------------------------------------------------------------------------


def _check_chrome_singleton_lock(profile_dir: Path) -> None:
    """Check for Chrome's native profile lock.

    - POSIX: ``<profile_dir>/SingletonLock``
    - Windows: ``<profile_dir>/lockfile``

    If the lock file exists AND references a live PID → raises
    ``ConfigurationError`` with a message distinguishing "profile in use"
    from "CDP port busy".
    """
    if sys.platform == "win32":
        lock_candidate = profile_dir / "lockfile"
    else:
        lock_candidate = profile_dir / "SingletonLock"

    if not lock_candidate.exists():
        return

    # SEC-M2: on POSIX, Chrome stores SingletonLock as a SYMBOLIC LINK whose
    # target is the string "hostname-PID" — there is no regular file to read.
    # Path.read_text() would dereference the symlink and try to open whatever
    # the attacker pointed it at (potentially blocking on a fifo, reading a
    # huge file, etc.). os.readlink reads the link contents directly without
    # following.
    if sys.platform != "win32" and lock_candidate.is_symlink():
        raw = os.readlink(str(lock_candidate)).strip()
    else:
        raw = lock_candidate.read_text().strip()
    # Extract numeric PID from the raw content (may be "hostname-PID" on POSIX)
    pid_str = raw.split("-")[-1] if "-" in raw else raw
    try:
        pid = int(pid_str)
    except ValueError:
        # Cannot parse PID — skip check
        return

    if _pid_alive(pid):
        raise ConfigurationError(
            f"Profile in use by another Chrome (PID {pid}) — "
            "close it OR run with `--browser cdp:<port>` to attach to the existing one."
        )


# ---------------------------------------------------------------------------
# Spawn Chrome (detached)
# ---------------------------------------------------------------------------


def _spawn_chrome(binary: str, profile_dir: Path, port: int) -> subprocess.Popen[bytes]:
    """Spawn a detached Chrome process that survives the CLI parent process.

    Platform-specific detachment:
    - Windows: ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP``
    - POSIX:   ``start_new_session=True``
    """
    cmd = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
    ]

    log.info("chrome_spawn", binary=binary, port=port, profile_dir=str(profile_dir))

    if sys.platform == "win32":
        return subprocess.Popen(
            cmd,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    else:
        return subprocess.Popen(
            cmd,
            start_new_session=True,
            close_fds=True,
        )


# ---------------------------------------------------------------------------
# Lockfile (atomic)
# ---------------------------------------------------------------------------


def _write_lock(lock_path: Path, pid: int, port: int, profile_name: str) -> None:
    """Atomically write the gflow CDP lockfile.

    Strategy:
    1. Write payload to a sibling ``<lock>.tmp`` file using ``O_CREAT|O_EXCL``
       so two concurrent writers cannot share a tmp name.
    2. ``os.replace()`` the tmp into the final lockfile path — atomic on POSIX
       and Windows (Python 3.8+).

    This protects against the failure mode where a process crashes after
    ``os.open`` but before ``os.write``/``os.close`` finishes, leaving an
    empty lockfile that ``_read_lock`` would return as ``None`` and trigger
    a double-spawn.

    Security hardening:
    - ``mode=0o600`` so the lockfile (containing PID + CDP port) is not
      world-readable on multi-user POSIX boxes.
    - ``O_NOFOLLOW`` (no-op on Windows) prevents symlink-squatting where an
      attacker pre-creates the lockfile path as a symlink to a sensitive
      target.

    Raises:
        FileExistsError: another process already holds the lock OR a stale
            tmp file blocks creation (caller should treat as race-lost).
    """
    payload = json.dumps({"pid": pid, "port": port, "profile_name": profile_name})
    tmp_path = lock_path.with_suffix(lock_path.suffix + ".tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)

    # Step 1: write payload fully to a sibling tmp file under O_EXCL +
    # O_NOFOLLOW (mode 0o600 on POSIX). If we crash here, only the .tmp
    # exists — the public lockfile is untouched.
    fd = os.open(str(tmp_path), flags, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)

    # Step 2: atomically link tmp → lock_path. os.link fails with
    # FileExistsError if lock_path already exists (atomic on POSIX, atomic
    # on Windows for NTFS). This gives us O_EXCL semantics on the FINAL
    # path while keeping the write itself crash-safe.
    try:
        os.link(str(tmp_path), str(lock_path))
    except BaseException:
        # Race lost (or hardlink unsupported) — clean up tmp and propagate
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    else:
        # Success: drop the tmp hardlink; the lockfile now stands alone
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _read_lock(lock_path: Path) -> dict[str, Any] | None:
    """Read the CDP lockfile. Returns None if absent or unparseable."""
    if not lock_path.exists():
        return None
    try:
        return json.loads(lock_path.read_text())  # type: ignore[no-any-return]
    except (ValueError, OSError):
        return None


def _remove_lock(lock_path: Path) -> None:
    """Remove the CDP lockfile, ignoring FileNotFoundError."""
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Port range probe
# ---------------------------------------------------------------------------


def _find_available_cdp_port(profile_dir: Path, start_port: int = _CDP_PORT_START) -> int:
    """Find a CDP port in [start_port … _CDP_PORT_END] that is either:

    - Not responding (free — Chrome not running), OR
    - Responding AND our own lockfile matches (same profile)

    If a port is responding but NOT ours → try the next one.
    If all ports are taken by non-gflow Chrome → raise ``ConfigurationError``.
    """
    lock_path = profile_dir / _LOCK_FILENAME
    our_lock = _read_lock(lock_path)
    our_port = our_lock.get("port") if our_lock else None

    for port in range(start_port, _CDP_PORT_END + 1):
        port_is_free = not is_browser_running(port=port)
        port_is_ours = port == our_port
        if port_is_free or port_is_ours:
            return port
        log.warning(
            "cdp_port_busy_non_gflow",
            port=port,
            msg="Port taken by non-gflow Chrome; trying next port",
        )

    raise ConfigurationError(
        f"All CDP ports {_CDP_PORT_START}-{_CDP_PORT_END} in use. "
        "Close other Chrome debug sessions or specify a custom port."
    )


# ---------------------------------------------------------------------------
# Logged-in check
# ---------------------------------------------------------------------------


async def _is_logged_in_to_flow(page: Any) -> bool:
    """Heuristic: return True if the page looks like an authenticated Flow session.

    Checks:
    1. URL does NOT contain ``accounts.google.com`` (redirect means not logged in)
    2. No <button> with text "Sign in" exists in the DOM. We scope to ``button``
       to avoid false-positives from footer links (``a:has-text("Sign in")``).

    Async because real Playwright's ``Locator.count`` returns a coroutine. The
    previous sync implementation assigned the coroutine directly to a variable
    making it always truthy — every real session would fail the logged-in check.
    """
    if "accounts.google.com" in page.url:
        return False

    try:
        sign_in_count = await page.locator('button:has-text("Sign in")').count()
        if sign_in_count and sign_in_count > 0:
            return False
    except Exception:
        # If the locator call fails for any reason, assume logged in
        # (fail-open is safer than blocking every session on a DOM race)
        pass

    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Module-level asyncio lock prevents two concurrent get_or_launch_browser calls
# from both attempting to spawn Chrome simultaneously.
_spawn_lock = asyncio.Lock()


def _sanitize_existing_lock(
    lock_path: Path,
    existing_lock: dict[str, Any] | None,
    port: int,
) -> tuple[dict[str, Any] | None, int, int]:
    """Validate an existing lockfile's pid (SEC-M1) and return normalized fields.

    Returns ``(lock_or_None, locked_pid, locked_port)``. If the lockfile's pid
    is not a positive int, the lock is removed and ``(None, 0, port)`` is
    returned — treat as "no lock".
    """
    if existing_lock is None:
        return None, 0, port
    locked_pid_raw = existing_lock.get("pid", 0)
    locked_port = existing_lock.get("port", port)
    if not isinstance(locked_pid_raw, int) or locked_pid_raw <= 0:
        log.warning(
            "lockfile_corrupted_or_tampered",
            removing=True,
            raw_pid=str(locked_pid_raw),
        )
        _remove_lock(lock_path)
        return None, 0, port
    return existing_lock, locked_pid_raw, locked_port


async def _attach_and_verify_login(port: int, profile_name: str) -> Any:
    """Connect to CDP on ``port``, open Flow, raise AuthMissingError if not logged in."""
    endpoint = f"http://localhost:{port}"
    context = await _connect_cdp(endpoint)
    page = await context.new_page()
    await page.goto(_FLOW_HOME_URL, wait_until="domcontentloaded")
    if not await _is_logged_in_to_flow(page):
        raise AuthMissingError(
            f"Profile not logged in to Flow. Run: gflow auth login --profile {profile_name}"
        )
    return context


async def _wait_chrome_ready(port: int) -> None:
    """Poll the CDP port until it responds or the spawn-wait budget is exhausted."""
    for _ in range(int(_SPAWN_WAIT_SECONDS / 0.5)):
        if is_browser_running(port=port):
            return
        await asyncio.sleep(0.5)


async def _resolve_race_loss(lock_path: Path, actual_port: int) -> int:
    """Handle the FileExistsError race: read the winner's lock and wait for them.

    Returns the winner's port. Raises ConfigurationError if the winner's
    Chrome never comes up — that means the lock is stale and the user must
    clean it up.
    """
    log.warning("lock_race_lost", port=actual_port)
    existing_lock = _read_lock(lock_path)
    winner_pid = existing_lock.get("pid", 0) if existing_lock else 0
    if existing_lock:
        actual_port = existing_lock.get("port", actual_port)
    await _wait_chrome_ready(actual_port)
    if not is_browser_running(port=actual_port):
        raise ConfigurationError(
            f"Lockfile exists (PID {winner_pid}, port {actual_port}) but "
            "Chrome is not responsive. Run `gflow chrome stop` to clean up."
        ) from None
    return actual_port


async def get_or_launch_browser(
    profile_dir: Path,
    port: int = 9222,
) -> Any:
    """Attach to running Chrome on ``port`` if alive; otherwise spawn detached Chrome.

    Returns a Playwright ``BrowserContext`` suitable for use with gflow strategies.

    Raises:
        ConfigurationError: Chrome not found, all ports taken, or profile lock conflict.
        AuthMissingError: The attached Chrome profile is not logged in to Flow.
    """
    lock_path = profile_dir / _LOCK_FILENAME
    profile_name = profile_dir.name

    async with _spawn_lock:
        existing_lock, locked_pid, locked_port = _sanitize_existing_lock(
            lock_path, _read_lock(lock_path), port
        )

        if existing_lock is not None:
            if _pid_alive(locked_pid):
                if is_browser_running(port=locked_port):
                    log.info("chrome_attach", port=locked_port, pid=locked_pid)
                    return await _attach_and_verify_login(locked_port, profile_name)
            else:
                log.warning("stale_lock_removed", pid=locked_pid, lock_path=str(lock_path))
                _remove_lock(lock_path)

        # No live lock — check if Chrome is already running on the port (fresh attach).
        # SEC-3: this is a trust caveat — we attach to whoever owns the CDP port.
        # On a shared / multi-user box this is a hijack vector. We log a warning
        # so operators can spot unmanaged-Chrome attaches in production logs.
        if is_browser_running(port=port):
            log.warning(
                "chrome_attach_no_lock",
                port=port,
                attached_to_unmanaged_chrome=True,
                hint="Verify localhost CDP trust on shared machines",
            )
            return await _attach_and_verify_login(port, profile_name)

        # Chrome not running — need to spawn.
        _check_chrome_singleton_lock(profile_dir)
        binary = _find_chrome_binary()
        actual_port = _find_available_cdp_port(profile_dir, start_port=port)
        proc = _spawn_chrome(binary, profile_dir, actual_port)

        race_lost = False
        try:
            _write_lock(lock_path, proc.pid, actual_port, profile_name)
        except FileExistsError:
            race_lost = True
            actual_port = await _resolve_race_loss(lock_path, actual_port)

        log.info("chrome_spawned", pid=proc.pid, port=actual_port)
        if not race_lost:
            await _wait_chrome_ready(actual_port)

        return await _attach_and_verify_login(actual_port, profile_name)


async def close_browser(profile_dir: Path, port: int = 9222) -> None:
    """Opt-in shutdown — removes the gflow CDP lockfile.

    Does NOT kill the Chrome process directly (the user may want to keep it
    open). This just clears the lock so the next CLI call treats Chrome as
    unmanaged. Use ``gflow chrome stop`` (D.2.3d) for full lifecycle control.
    """
    await asyncio.sleep(0)  # yield to event loop — keep async signature on public API
    lock_path = profile_dir / _LOCK_FILENAME
    if not lock_path.exists():
        return  # nothing to release
    # Remove regardless of whether the lock matches `port` — the recorded port
    # may have changed since the lock was written, and either way the user is
    # explicitly asking to drop the lock.
    _remove_lock(lock_path)
    log.info("chrome_lock_released", port=port)
