from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from gflow_cli.mcp.server import HTTP_PATH


def _free_port(host: str) -> str:
    """Bind :0 to grab a free localhost port, then release it for the daemon.

    Avoids a hard-coded port colliding with a parallel run or a leftover listener.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return str(sock.getsockname()[1])


@pytest.mark.e2e
@pytest.mark.e2e_data
@pytest.mark.asyncio
async def test_daemon_e2e_lifecycle(e2e_env: dict[str, str], e2e_profile_dir: Path) -> None:
    from gflow_cli.profile_lease import ProfileLease

    profile_name = e2e_env["GFLOW_CLI_PROFILE"]
    host = "127.0.0.1"
    port = _free_port(host)

    # D3: the daemon no longer writes an overwriteable ``profile.lock`` and holds
    # no profile lease while idle. If the cross-process lease is already held,
    # a real daemon or task owns this profile — skip rather than contend.
    lease_probe = ProfileLease(e2e_profile_dir)
    if not lease_probe.try_acquire():
        pytest.skip(f"profile {e2e_profile_dir} is already leased; a daemon/task owns it")
    lease_probe.release()

    # 1. Spawn the daemon process
    cmd = [
        sys.executable,
        "-m",
        "gflow_cli.cli",
        "serve",
        "--port",
        port,
        "--host",
        host,
        "--profile",
        profile_name,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=e2e_env,
        text=True,
    )
    # The daemon serves Streamable HTTP at ``HTTP_PATH`` (``/mcp``) — imported
    # rather than hardcoded so a mount-path change fails the import, not the
    # assertion. This test previously drove the LEGACY SSE transport (GET
    # /mcp/sse -> ``event: endpoint`` -> POST /mcp/message?session_id=...),
    # which the daemon no longer serves, so it could never pass: the readiness
    # probe never saw a 200 and every run died in the "failed to start" branch
    # while the daemon was in fact up and healthy.
    url = f"http://{host}:{port}{HTTP_PATH}"

    try:
        # 2. Wait for the daemon to bind. Probe the TCP socket rather than an
        #    HTTP status: Streamable HTTP replies to a bare GET with 4xx/406
        #    (it wants a POST, or an Accept negotiating text/event-stream), so
        #    "port accepts a connection" is the honest readiness signal here.
        connected = False
        for _ in range(50):
            try:
                _r, _w = await asyncio.wait_for(
                    asyncio.open_connection(host, int(port)), timeout=0.5
                )
                _w.close()
                await _w.wait_closed()
                connected = True
                break
            except (OSError, TimeoutError):
                await asyncio.sleep(0.2)

        if not connected:
            proc.terminate()
            stdout, stderr = proc.communicate(timeout=5)
            pytest.fail(
                f"Daemon failed to start on port {port}. stdout:\n{stdout}\nstderr:\n{stderr}"
            )

        # D3: an idle daemon owns no profile lease and writes no lock file. The
        # profile stays reacquirable while the daemon runs (ownership is per
        # browser task, not daemon-lifetime).
        assert not (e2e_profile_dir / "profile.lock").exists()
        idle_probe = ProfileLease(e2e_profile_dir)
        assert idle_probe.try_acquire() is True
        idle_probe.release()

        # 3. Speak the real protocol via the SDK's own client instead of
        #    hand-rolling framing. Hand-rolled transport plumbing is exactly
        #    what rotted here: it kept asserting a wire format the server had
        #    already stopped serving. The SDK client tracks the server's
        #    transport, so a future migration surfaces as a client error rather
        #    than a silently unreachable endpoint.
        async with streamable_http_client(url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                init_result = await asyncio.wait_for(session.initialize(), timeout=30)
                assert init_result.server_info.name == "gflow-cli"

                # 4. The tool registry is served over the live daemon, not an
                #    in-process import — this is what proves the daemon wired up.
                tools = await asyncio.wait_for(session.list_tools(), timeout=30)
                tool_names = {t.name for t in tools.tools}
                assert "gflow_list_projects" in tool_names, tool_names

                # 5. Dispatch a real tool call and require a non-error result.
                result = await asyncio.wait_for(
                    session.call_tool("gflow_list_projects", {"limit": 5}),
                    timeout=120,
                )
                assert result.is_error is not True, result.content
                assert result.content is not None

    finally:
        # 6. Cleanup is ALWAYS reached, even if the daemon never bound or an
        #    assertion fired mid-stream — no orphaned client or subprocess.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    # After a clean shutdown the profile is free — proven by a real ProfileLease
    # reacquire on the profile dir (D3 lease world; replaces the old
    # file-presence check on the removed profile.lock).
    reacquire = ProfileLease(e2e_profile_dir)
    assert reacquire.try_acquire() is True
    reacquire.release()
    assert not (e2e_profile_dir / "profile.lock").exists()
