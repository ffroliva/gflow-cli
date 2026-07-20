from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest


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
    client = httpx.AsyncClient()

    try:
        # 2. Wait for daemon to bind and start listening
        url = f"http://{host}:{port}/mcp/sse"
        connected = False
        for _ in range(30):
            try:
                # Short timeout to check if port is open
                res = await client.get(url, timeout=0.5, headers={"Host": f"localhost:{port}"})
                if res.status_code == 200:
                    connected = True
                    break
            except (httpx.ConnectError, httpx.ConnectTimeout):
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

        # 3. Connect to SSE stream and read endpoint
        session_id = None
        message_path = None

        # We wrap in a try block to handle streaming response safely
        async with client.stream("GET", url, headers={"Host": f"localhost:{port}"}) as response:
            assert response.status_code == 200

            # Read line by line to parse the endpoint event containing the session_id
            async for line in response.iter_lines():
                if line.startswith("event: endpoint"):
                    # Next line should be data
                    continue
                if line.startswith("data:"):
                    # data: %2Fmessages%3Fsession_id%3D...
                    uri_data = line[len("data:") :].strip()
                    # Parse session_id from the query parameters in the URI
                    parsed_url = urlparse(uri_data)
                    queries = parse_qs(parsed_url.query)
                    if "session_id" in queries:
                        session_id = queries["session_id"][0]
                        message_path = parsed_url.path
                        break

            assert session_id is not None
            assert message_path is not None

            # 4. Dispatch MCP tool command gflow_list_projects via POST
            post_url = f"http://{host}:{port}/mcp/message"
            payload = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "gflow_list_projects", "arguments": {"limit": 5}},
                "id": "1",
            }
            # Message POST sends JSON-RPC to the proxy handler (using Host header)
            post_res = await client.post(
                post_url,
                json=payload,
                params={"session_id": session_id},
                headers={"Host": f"localhost:{port}"},
            )
            assert post_res.status_code in (200, 202)

            # 5. Read SSE stream to get the JSON-RPC response event
            response_received = False
            async for line in response.iter_lines():
                if line.startswith("event: message"):
                    continue
                if line.startswith("data:"):
                    data_str = line[len("data:") :].strip()
                    import json

                    resp_json = json.loads(data_str)
                    assert resp_json.get("id") == "1"
                    assert "result" in resp_json
                    response_received = True
                    break

            assert response_received

    finally:
        # 6. Cleanup is ALWAYS reached, even if the daemon never bound or an
        #    assertion fired mid-stream — no orphaned client or subprocess.
        await client.aclose()
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
