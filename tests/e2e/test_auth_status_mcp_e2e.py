"""Live proof that the MCP auth-status twin verifies a real session (#1023).

`tests/e2e/test_auth_verification_e2e.py` proves `verify_flow_profile`. This proves the
tool an agent calls, `gflow_auth_status`: its profile resolution and the dict that
crosses the tool boundary. Since labs.google's session URL answers 410 (#1023) an
agent saw `verification_error` on a healthy profile, and nothing offline could show it.

Opt-in: ``-m e2e_auth`` with ``GFLOW_CLI_E2E_PROFILE`` set. Read-only; spends nothing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


async def test_mcp_auth_status_reports_a_live_profile_authenticated(
    e2e_profile_dir: Path,
) -> None:
    from gflow_cli.mcp import tools

    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()
    result: dict[str, Any] = await tools.gflow_auth_status(profile=profile)

    assert result.get("status") == "authenticated", f"MCP auth status failed: {result}"
    assert result["profile"] == profile
    assert result["user_email"], "an authenticated result must name the account"


async def test_mcp_auth_status_on_a_live_profile_is_not_flagged_as_an_error(
    e2e_profile_dir: Path,
) -> None:
    """#1464: the server sets `isError` from the envelope, so go through `call_tool`.

    Calling `tools.gflow_auth_status` directly skips `GflowMCPServer.call_tool`, the one
    place the flag is set; only a real client sees what an agent sees.
    """
    from mcp import Client

    import gflow_cli.mcp.tools  # noqa: F401  # registers the tools on the server
    from gflow_cli.mcp.server import server

    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()
    async with Client(server, mode="2026-07-28") as client:
        result = await client.call_tool("gflow_auth_status", {"profile": profile})

    assert result.structured_content is not None
    assert result.structured_content["status"] == "authenticated", result.structured_content
    assert result.is_error is False
