# SPDX-License-Identifier: MIT
"""`gflow capabilities --json` on the real binary path agrees with `capabilities://self`.

Zero credits, no browser, no Flow call: the command reads only GFLOW_CLI_FLOW_HOST. The
subprocess pass proves stdout carries one JSON document and nothing else (no Rich or log
leak), and that it is the same document the MCP resource serves (#1197).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


@pytest.mark.parametrize("host", ["auto", "flow.google.com", "labs.google"])
@pytest.mark.asyncio
async def test_e2e_capabilities_json_matches_the_mcp_resource(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = {**os.environ, "GFLOW_CLI_FLOW_HOST": host}
    env.pop("GFLOW_MCP_NO_SPEND", None)
    proc = subprocess.run(
        [sys.executable, "-m", "gflow_cli", "capabilities", "--json"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, f"exit {proc.returncode}; stderr tail: {proc.stderr[-400:]!r}"
    cli_doc = json.loads(proc.stdout)

    from mcp import Client

    import gflow_cli.mcp.resources  # noqa: F401  (registers the resource)
    from gflow_cli.config import reset_settings
    from gflow_cli.mcp.server import server

    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", host)
    monkeypatch.delenv("GFLOW_MCP_NO_SPEND", raising=False)
    reset_settings()
    async with Client(server, mode="2026-07-28") as client:
        read = await client.read_resource("capabilities://self")
    mcp_doc = json.loads(read.contents[0].text)  # pyright: ignore[reportAttributeAccessIssue]

    assert cli_doc == mcp_doc
    assert cli_doc["host"] == host
