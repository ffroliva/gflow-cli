# SPDX-License-Identifier: MIT
"""``/v1/images/generations`` on a real ``gflow serve`` process (#1365).

The in-process tests build the ASGI app directly; this one proves the route is mounted by
the command users run, with its real middleware stack, over a real socket. It runs with
``GFLOW_MCP_NO_SPEND=1`` and an empty ``GFLOW_CLI_HOME``: no browser, no profile, no Flow
call, $0 — so it runs offline in CI.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

PATH = "/v1/images/generations"
#: Not a credential — a fixed literal for the auth test.
TOKEN = "test-daemon-token"
_TOKEN_VARS = ("GFLOW_CLI_DAEMON_TOKEN", "GFLOW_DAEMON_TOKEN")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(proc: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 60  # a cold start compiles bytecode first
    while time.monotonic() < deadline and proc.poll() is None:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        except OSError:
            time.sleep(0.2)
        else:
            return
    proc.kill()
    stdout, stderr = proc.communicate(timeout=10)
    pytest.fail(f"gflow serve did not bind. stdout:\n{stdout}\nstderr:\n{stderr}")


@contextmanager
def _serve(home: Path, token: str | None = None) -> Iterator[httpx.Client]:
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k not in _TOKEN_VARS}
    env |= {"GFLOW_CLI_HOME": str(home), "GFLOW_MCP_NO_SPEND": "1", "PYTHONUTF8": "1"}
    if token is not None:
        env["GFLOW_DAEMON_TOKEN"] = token
    proc = subprocess.Popen(
        [sys.executable, "-m", "gflow_cli", "serve", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        _wait_for_port(proc, port)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            yield client
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=10)


def test_route_is_mounted_refuses_spend_and_cross_site_posts(tmp_path: Path) -> None:
    with _serve(tmp_path) as client:
        refused = client.post(PATH, json={"prompt": "a paper boat"})
        cross_site = client.post(
            PATH,
            content=b'{"prompt": "x"}',
            headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
        )
        foreign_origin = client.post(
            PATH, json={"prompt": "x"}, headers={"Origin": "https://evil.example"}
        )

    assert refused.status_code == 403, refused.text
    error = refused.json()["error"]
    assert {"message", "type", "param", "code"} <= set(error)
    assert (error["type"], error["code"]) == ("permission_error", "no_spend")
    assert cross_site.status_code == 415, cross_site.text
    assert foreign_origin.status_code == 403
    assert foreign_origin.json()["error"]["code"] == "invalid_origin"


def test_daemon_token_is_enforced_in_openai_shape(tmp_path: Path) -> None:
    with _serve(tmp_path, token=TOKEN) as client:
        missing = client.post(PATH, json={"prompt": "x"})
        accepted = client.post(
            PATH, json={"prompt": "x"}, headers={"Authorization": f"Bearer {TOKEN}"}
        )

    assert missing.status_code == 401
    assert missing.json()["error"]["type"] == "authentication_error"
    # Past the token, the no-spend refusal answers: the token let the request in.
    assert accepted.status_code == 403
    assert accepted.json()["error"]["code"] == "no_spend"
