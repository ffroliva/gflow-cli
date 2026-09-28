"""Opt-in real-Chrome launch: spawn the user's installed chrome.exe with
``--remote-debugging-port`` and attach via ``connect_over_cdp``.

Why: Playwright's ``launch_persistent_context`` always adds
``--enable-automation`` (which Chrome turns into a visible
"automated test software" infobar and ``navigator.webdriver=true`` on the wire)
and — under the default engine — enables ``Runtime.enable`` on every frame,
which reCAPTCHA Enterprise reads as automation. Spawning the binary ourselves
keeps the command line indistinguishable from a user-launched Chrome; connecting
over CDP through patchright avoids the ``Runtime.enable`` leak.

Active only when ``GFLOW_CLI_CDP_LAUNCH=1`` and the selected engine is
``patchright`` — the playwright engine still enables ``Runtime.enable`` on the
attached page and reintroduces the tell.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import urllib.request
from typing import Any

import structlog

from gflow_cli.errors import ConfigurationError

log = structlog.get_logger()

_FLAG = "GFLOW_CLI_CDP_LAUNCH"


def _cdp_chrome_binary() -> str | None:
    """Return the real chrome.exe — never a .CMD shim, never Chromium.

    The PATH probe in ``resolved_chrome_binary`` can pick up a proxy-shim
    ``chrome.CMD`` (e.g. ``~/.local/bin/chrome.CMD``), which goes through
    cmd.exe and adds an indirection the whole point of cdp_launch is to
    remove. Platform-standard install paths are checked first; only when
    none exists do we fall back to the shared resolver.
    """
    from pathlib import Path

    from gflow_cli.browser_manager import resolved_chrome_binary

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            Path(local_app_data or "") / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
    elif sys.platform == "darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    else:
        candidates = [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/chromium"),
        ]
    for p in candidates:
        if p.exists():
            return str(p)
    return resolved_chrome_binary()


def cdp_launch_requested() -> bool:
    return os.environ.get(_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _spawn(cmd: list[str]) -> subprocess.Popen[bytes]:
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(
        cmd,
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_ws_url(port: int, chrome: subprocess.Popen[bytes], deadline: float) -> str | None:
    """Blocking poll for /json/version — called via asyncio.to_thread."""
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as resp:
                return json.loads(resp.read())["webSocketDebuggerUrl"]
        except (OSError, KeyError, ValueError):
            if chrome.poll() is not None:
                return None
            if _now() >= deadline:
                return None
            _sleep(0.25)


_now = __import__("time").monotonic
_sleep = __import__("time").sleep


class _CdpContext:
    """BrowserContext shim: CDP-connected context + the spawned chrome.exe.

    ``close()`` terminates the chrome process we spawned; the caller owns the
    patchright driver's lifecycle (``self._pw.stop()`` lives in the same teardown
    path a persistent-context launch uses).
    """

    def __init__(self, ctx: Any, chrome_proc: subprocess.Popen[bytes]):
        self._ctx = ctx
        self._chrome = chrome_proc

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)

    @property
    def pages(self) -> Any:
        return self._ctx.pages

    async def close(self) -> None:
        try:
            await self._ctx.close()
        finally:
            if self._chrome.poll() is None:
                # taskkill /T takes the whole tree — a bare terminate() on a
                # .CMD shim leaves the chrome.exe child holding the profile.
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self._chrome.pid)],
                        capture_output=True,
                        check=False,
                    )
                else:
                    self._chrome.terminate()
                    try:
                        self._chrome.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self._chrome.kill()


async def launch_via_cdp(pw: Any, kwargs: dict[str, Any]) -> Any:
    """Spawn chrome.exe with a debug port; return a context-like wrapper.

    ``kwargs`` mirrors ``launch_persistent_context``: ``user_data_dir`` is used
    verbatim, ``args`` are forwarded minus automation tells, and Playwright-only
    keys (``channel``, ``headless``, ``viewport``, ``locale``,
    ``extra_http_headers``) are translated onto the connected context where the
    CDP session allows.
    """
    user_data_dir = kwargs.get("user_data_dir")
    if not user_data_dir:
        raise ConfigurationError(
            detail="cdp_launch: user_data_dir is required — the profile owns the session",
            remediation_hint="Unset GFLOW_CLI_CDP_LAUNCH to use the stock launcher.",
        )
    chrome = _cdp_chrome_binary()
    if not chrome:
        raise ConfigurationError(
            detail="cdp_launch: system Chrome not found at the standard locations",
            remediation_hint=(
                "Install Google Chrome or set CHROME_BINARY to the chrome.exe path, "
                "or unset GFLOW_CLI_CDP_LAUNCH."
            ),
        )
    port = _free_port()
    chrome_args = [
        chrome,
        f"--user-data-dir={user_data_dir}",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    raw_args: Any = kwargs.get("args") or []
    extra_args: list[str] = [a for a in raw_args if isinstance(a, str)]
    for extra in extra_args:
        if extra.startswith("--enable-automation"):
            continue
        if extra not in chrome_args:
            chrome_args.append(extra)
    chrome_proc = await asyncio.to_thread(_spawn, chrome_args)
    ws_url = await asyncio.to_thread(_wait_ws_url, port, chrome_proc, _now() + 30.0)
    if ws_url is None:
        exit_code = chrome_proc.poll()
        if exit_code is None:
            chrome_proc.kill()
        raise ConfigurationError(
            detail=(
                "cdp_launch: chrome "
                f"{'exited before' if exit_code is not None else 'never opened'} "
                "the debug port"
                f"{'' if exit_code is None else f' (exit {exit_code})'} "
                f"on profile dir {user_data_dir}"
            ),
            remediation_hint=(
                "Close Chrome windows using this profile or unset GFLOW_CLI_CDP_LAUNCH."
            ),
        )
    log.info(
        "client.cdp_launch",
        chrome=chrome,
        port=port,
        user_data_dir=str(user_data_dir),
    )
    browser = await pw.chromium.connect_over_cdp(ws_url)
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    # A user-launched Chrome restores the profile's session; close every tab
    # that isn't the one we will drive so nothing competes for focus or state.
    for extra_page in ctx.pages[1:]:
        try:
            await extra_page.close()
        except Exception:  # noqa: BLE001 - a closing tab is not a launch failure
            log.debug("client.cdp_launch.tab_close_failed", exc_info=True)
    return _CdpContext(ctx, chrome_proc)
