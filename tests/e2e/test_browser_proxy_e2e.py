"""Live proof that GFLOW_CLI_BROWSER_PROXY routes the browser (#1036).

An APPROXIMATION of the reporter's network: a local logging CONNECT proxy on
127.0.0.1, not a network where Google is reachable only through a proxy. It proves
the browser's traffic goes through the configured proxy, that a dead proxy fails
fast with a hint naming the setting, and why proxy credentials are refused (Flow
does not load through Playwright's proxy auth); it cannot prove the direct route
was blocked.

Opt-in: ``-m e2e_image`` with ``GFLOW_CLI_E2E_PROFILE``. One image (quota, $0).
"""

from __future__ import annotations

import base64
import contextlib
import select
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

from tests.e2e._redacted_env import RedactedEnv

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_image]

_CREDENTIALS = "e2euser:pr0xy-pw"
_407 = (
    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
    b'Proxy-Authenticate: Basic realm="gflow-e2e"\r\n'
    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
)
_502 = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"


class _ConnectProxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, credentials: str | None = None) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.hosts: list[str] = []  # requests let through
        # Basic auth: None accepts everyone; "user:pass" answers 407 to anything else.
        self.credentials = credentials
        self.challenged = 0  # requests answered 407
        self.authorized = 0  # requests that presented the right credentials


class _Handler(socketserver.StreamRequestHandler):
    server: _ConnectProxy

    def handle(self) -> None:
        line = self.rfile.readline(65536).decode("latin-1")
        auth = ""
        while (header := self.rfile.readline(65536)) not in (b"\r\n", b"\n", b""):
            name, _, value = header.decode("latin-1").partition(":")
            if name.strip().lower() == "proxy-authorization":
                auth = value.strip()
        method, _, rest = line.partition(" ")
        target = rest.split(" ", 1)[0]
        if self.server.credentials is not None:
            expected = "Basic " + base64.b64encode(self.server.credentials.encode()).decode()
            if auth != expected:
                self.server.challenged += 1
                self.wfile.write(_407)
                return
            self.server.authorized += 1
        self.server.hosts.append(f"{method} {target}")
        if method != "CONNECT":
            self.wfile.write(_502)
            return
        host, _, port = target.rpartition(":")
        try:
            upstream = socket.create_connection((host, int(port)), timeout=15)
        except OSError:
            self.wfile.write(_502)
            return
        self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        self.wfile.flush()
        client = self.connection
        with upstream, contextlib.suppress(OSError):  # either side may hang up
            while True:
                ready, _, _ = select.select([client, upstream], [], [], 60)
                if not ready:
                    return
                for src in ready:
                    data = src.recv(65536)
                    if not data:
                        return
                    (upstream if src is client else client).sendall(data)


def _serve(server: _ConnectProxy) -> Iterator[_ConnectProxy]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def proxy() -> Iterator[_ConnectProxy]:
    yield from _serve(_ConnectProxy())


@pytest.fixture
def auth_proxy() -> Iterator[_ConnectProxy]:
    yield from _serve(_ConnectProxy(_CREDENTIALS))


def _dead_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _gflow(env: RedactedEnv, *args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "gflow_cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _google_hosts(server: _ConnectProxy) -> set[str]:
    return {h for h in server.hosts if "google" in h}


def _saw_flow(server: _ConnectProxy) -> bool:
    return bool({"CONNECT flow.google.com:443", "CONNECT labs.google:443"} & set(server.hosts))


def test_t2i_generates_through_the_proxy(
    e2e_env: RedactedEnv, proxy: _ConnectProxy, tmp_path: Path
) -> None:
    env = e2e_env | {"GFLOW_CLI_BROWSER_PROXY": f"http://127.0.0.1:{proxy.server_address[1]}"}
    proc = _gflow(
        env, "image", "t2i", "a calm forest at dawn", "--out", str(tmp_path / "out"), "--json"
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert _saw_flow(proxy), proxy.hosts


def test_auth_status_verifies_through_the_proxy(e2e_env: RedactedEnv, proxy: _ConnectProxy) -> None:
    env = e2e_env | {"GFLOW_CLI_BROWSER_PROXY": f"http://127.0.0.1:{proxy.server_address[1]}"}
    proc = _gflow(env, "auth", "status", "--profile", env["GFLOW_CLI_PROFILE"], timeout=180)
    assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    assert "verified" in proc.stdout
    # The browser-side probe (flow.google.com's server-attested myaccount check).
    assert "CONNECT myaccount.google.com:443" in proxy.hosts, proxy.hosts


def test_auth_renew_goes_through_the_proxy(e2e_env: RedactedEnv, proxy: _ConnectProxy) -> None:
    env = e2e_env | {"GFLOW_CLI_BROWSER_PROXY": f"http://127.0.0.1:{proxy.server_address[1]}"}
    proc = _gflow(env, "auth", "renew", "--profile", env["GFLOW_CLI_PROFILE"], "--json")
    assert proc.returncode == 0, proc.stderr[-800:]
    assert '"authenticated"' in proc.stdout
    assert _google_hosts(proxy), proxy.hosts


async def _load_flow(server: _ConnectProxy, tmp_path: Path, *, with_credentials: bool) -> str:
    """Open Flow on a FRESH profile (no account) through ``server``; '' on success."""
    from playwright.async_api import async_playwright

    spec = {"server": f"http://127.0.0.1:{server.server_address[1]}"}
    if with_credentials:
        user, password = _CREDENTIALS.split(":")
        spec |= {"username": user, "password": password}
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(tmp_path / f"fresh_{with_credentials}"), headless=True, proxy=spec
        )
        try:
            await ctx.pages[0].goto(
                "https://labs.google/fx/tools/flow", wait_until="domcontentloaded", timeout=30_000
            )
        except Exception as exc:  # noqa: BLE001 — the failure IS the observation
            return str(exc).splitlines()[0]
        finally:
            await ctx.close()
    return ""


async def test_proxy_credentials_break_flow_so_v1_refuses_them(
    auth_proxy: _ConnectProxy, proxy: _ConnectProxy, tmp_path: Path
) -> None:
    """Why GFLOW_CLI_BROWSER_PROXY refuses user:pass@ (#1036). Characterisation, $0, no account.

    Measured 2026-10-22, Playwright 1.61, bundled Chromium and channel="chrome" alike:
    through a Basic-auth proxy example.com loads but Flow's labs -> flow.google.com
    navigation never completes, while the same proxy without auth loads it in ~1 s.
    If the first assertion starts failing, Playwright handles it now: revisit the
    refusal in config.py rather than deleting this test.
    """
    failure = await _load_flow(auth_proxy, tmp_path, with_credentials=True)
    assert "Timeout" in failure, f"Flow loaded through proxy auth ({failure!r}): revisit #1036"
    assert auth_proxy.authorized > 0  # the credentials were presented and accepted
    assert await _load_flow(proxy, tmp_path, with_credentials=False) == ""


def test_credentialed_value_is_refused_at_startup(e2e_env: RedactedEnv) -> None:
    env = e2e_env | {"GFLOW_CLI_BROWSER_PROXY": f"http://{_CREDENTIALS}@127.0.0.1:7897"}
    proc = _gflow(env, "auth", "status", "--profile", env["GFLOW_CLI_PROFILE"], timeout=60)
    assert proc.returncode == 11, proc.stdout[-800:] + proc.stderr[-800:]
    assert "GFLOW_CLI_BROWSER_PROXY — invalid proxy" in proc.stdout + proc.stderr
    assert "pr0xy-pw" not in proc.stdout + proc.stderr


def test_dead_proxy_fails_fast_naming_the_setting(e2e_env: RedactedEnv, tmp_path: Path) -> None:
    env = e2e_env | {"GFLOW_CLI_BROWSER_PROXY": f"http://127.0.0.1:{_dead_port()}"}
    started = time.monotonic()
    proc = _gflow(env, "image", "t2i", "a calm forest", "--out", str(tmp_path / "out"))
    elapsed = time.monotonic() - started
    assert proc.returncode == 11, proc.stderr[-800:]
    assert "GFLOW_CLI_BROWSER_PROXY" in proc.stdout + proc.stderr
    print(f"dead proxy exit 11 after {elapsed:.1f}s")  # noqa: T201 — evidence for the PR
    assert elapsed < 90, f"took {elapsed:.0f}s"
