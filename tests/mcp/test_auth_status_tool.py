# SPDX-License-Identifier: MIT
"""#497: gflow_auth_status — non-interactive, credit-free session probe.

Agents call this BEFORE enqueueing a credit-spending generation; the queue is
async, so an auth failure otherwise surfaces only later, from the daemon. The
tool must never launch an interactive login flow — it wraps the existing
``verify_flow_profile`` probe and answers with a structured envelope carrying
a remediation hint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus


def _probe(outcome: FlowSessionOutcome, email: str | None = None) -> Any:
    async def fake(profile_dir: Path, *, source: str = "chrome") -> FlowSessionStatus:
        return FlowSessionStatus(outcome=outcome, user_email=email, source=source)

    return fake


@pytest.mark.asyncio
async def test_authenticated_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.mcp import tools

    monkeypatch.setattr(tools, "_resolve_and_validate_profile", lambda p: "denon")
    monkeypatch.setattr(
        tools.verification,
        "verify_flow_profile",
        _probe(FlowSessionOutcome.AUTHENTICATED, "user@example.com"),
    )
    result = await tools.gflow_auth_status()
    assert result == {
        "status": "authenticated",
        "profile": "denon",
        "user_email": "user@example.com",
    }


@pytest.mark.asyncio
async def test_unauthenticated_envelope_has_remediation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.mcp import tools

    monkeypatch.setattr(tools, "_resolve_and_validate_profile", lambda p: "denon")
    monkeypatch.setattr(
        tools.verification,
        "verify_flow_profile",
        _probe(FlowSessionOutcome.NO_SESSION),
    )
    result = await tools.gflow_auth_status()
    assert result["status"] == "no_session"
    assert result["profile"] == "denon"
    err = result["error"]
    assert err["status"] == 401
    assert "gflow auth login --profile denon" in err["remediation_hint"]
    assert "local terminal" in err["remediation_hint"]


@pytest.mark.asyncio
async def test_verification_error_does_not_advise_relogin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.mcp import tools

    monkeypatch.setattr(tools, "_resolve_and_validate_profile", lambda p: "denon")
    monkeypatch.setattr(
        tools.verification,
        "verify_flow_profile",
        _probe(FlowSessionOutcome.VERIFICATION_ERROR),
    )
    result = await tools.gflow_auth_status()
    assert result["status"] == "verification_error"
    # A network problem is not fixed by re-login (mirrors the CLI's guidance).
    assert "auth login" not in result["error"]["remediation_hint"]
    assert "retry" in result["error"]["remediation_hint"].lower()


async def test_a_missing_profile_marker_is_not_retryable_and_not_a_dead_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#796 + MCP parity: the CLI now distinguishes a missing browser-strategy
    marker from a network fault, so the machine-readable surface must too. It is
    neither retryable (the marker will not reappear) nor an expired session, so an
    agent that retries or re-logins on 503/401 would learn nothing."""
    from gflow_cli.mcp import tools

    monkeypatch.setattr(tools, "_resolve_and_validate_profile", lambda p: "denon")
    monkeypatch.setattr(
        tools.verification,
        "verify_flow_profile",
        _probe(FlowSessionOutcome.PROFILE_MARKER_MISSING),
    )
    result = await tools.gflow_auth_status()
    assert result["status"] == "profile_marker_missing"
    assert result["error"]["retryable"] is False
    assert result["error"]["status"] == 409
    assert "--browser chrome" in result["error"]["remediation_hint"]
