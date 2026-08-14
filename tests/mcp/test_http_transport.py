# SPDX-License-Identifier: MIT
"""Subprocess smoke of the DEFAULT Streamable HTTP transport (#500).

``gflow serve`` defaults to Streamable HTTP, yet only the non-default stdio
transport had CI-runnable protocol coverage (and that smoke has caught two
named regressions). The real-subprocess HTTP test lives behind the ``e2e`` /
live-profile gate and never runs in CI. This is the CI-runnable half: spawn
``gflow serve`` with no live profile, drive MCP ``initialize`` + ``tools/list``
over the SDK's own client, assert protocol-level success and clean shutdown.
No generation, no browser, no profile.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from gflow_cli.mcp.server import HTTP_PATH


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_serve_speaks_streamable_http(tmp_path) -> None:
    host, port = "127.0.0.1", _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "gflow_cli", "serve", "--host", host, "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Point HOME at an empty dir: the smoke must not depend on any profile
        # existing (CI has none) — boot and protocol only.
        env={
            k: v
            for k, v in {**os.environ, "GFLOW_CLI_HOME": str(tmp_path), "PYTHONUTF8": "1"}.items()
            # An ambient no-spend export would strip the generate tools this
            # smoke asserts present — isolate BOTH env knobs, not just HOME.
            if k != "GFLOW_MCP_NO_SPEND"
        },
        text=True,
    )
    try:
        # Streamable HTTP answers a bare GET with 4xx, so "port accepts a
        # connection" is the honest readiness signal (same as the e2e test).
        connected = False
        for _ in range(75):
            if proc.poll() is not None:
                break
            try:
                _r, _w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=0.5)
                _w.close()
                await _w.wait_closed()
                connected = True
                break
            except (OSError, TimeoutError):
                await asyncio.sleep(0.2)
        if not connected:
            stdout, stderr = proc.communicate(timeout=5)
            pytest.fail(f"serve failed to bind. stdout:\n{stdout}\nstderr:\n{stderr}")

        url = f"http://{host}:{port}{HTTP_PATH}"
        async with streamable_http_client(url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                init = await asyncio.wait_for(session.initialize(), timeout=30)
                assert init.server_info.name == "gflow-cli"

                tools = await asyncio.wait_for(session.list_tools(), timeout=30)
                tool_names = {t.name for t in tools.tools}
                assert "gflow_list_projects" in tool_names, tool_names
                assert "gflow_generate_image" in tool_names, tool_names
    finally:
        proc.terminate()
        try:
            # communicate() also drains the stdout/stderr PIPEs — never leave
            # them undrained on the success path (latent block if logging
            # output ever exceeds the pipe buffer).
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=10)
    # (communicate() above only returns once the process has exited, so no
    # separate returncode assert — it could never fail.)
