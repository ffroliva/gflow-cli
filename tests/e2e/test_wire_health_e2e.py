"""E2E for G6 (#1229): `gflow doctor --json` and MCP `gflow_auth_status` agree on wire health.

SCENARIO row 25. Doctor runs as a real subprocess (its own process, so no wire state is
shared), then the MCP tool in-process, one after the other so no Chrome holds the profile's
cookie jar during the doctor run. Both must say ``accepted`` with the same non-empty build
label; agreeing on ``disabled`` would prove nothing, so the wire is switched on explicitly.

Opt-in: ``-m e2e_auth`` with ``GFLOW_CLI_E2E_PROFILE`` set. **Cost: zero.** Two shell GETs and
two ``nzlxg`` balance reads, plus the existing MCP session probe (may open the profile's
browser headlessly). Nothing is generated.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api import flow_wire
from gflow_cli.config import reset_settings

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

_SUMMARY = re.compile(r"^status=(\S+) build=(\S+) ")


async def test_doctor_and_mcp_auth_status_agree_on_an_accepted_wire(
    e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.mcp import tools

    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()
    monkeypatch.setenv("GFLOW_CLI_WIRE_READS", "1")
    monkeypatch.setenv("GFLOW_CLI_PROFILE", profile)  # doctor probes the default profile
    reset_settings()
    for name, empty in (("_tokens", {}), ("_signed_out", set()), ("_throttled", {})):
        monkeypatch.setattr(flow_wire, name, empty)
    monkeypatch.setattr(flow_wire, "_locks", {})

    proc = subprocess.run(  # noqa: S603 - our own CLI, fixed arguments
        [sys.executable, "-m", "gflow_cli", "doctor", "--json"],
        capture_output=True,
        text=True,
        timeout=120,
        env=dict(os.environ),
        check=False,
    )
    assert proc.returncode in (0, 33), proc.stderr[-2000:]  # 33: unrelated local findings
    [check] = [c for c in json.loads(proc.stdout)["checks"] if c["check"] == "auth.wire_read"]
    match = _SUMMARY.match(check["summary"])
    assert match, check
    doctor_status, doctor_build = match.groups()

    result: dict[str, Any] = await tools.gflow_auth_status(profile=profile)

    assert doctor_status == "accepted", check
    assert check["severity"] == "pass"
    assert result["wire"]["status"] == "accepted", result["wire"]
    assert result["wire"]["build"] and result["wire"]["build"] == doctor_build
    assert result["status"] == "authenticated", result
