"""`gflow_auth_status` carries the direct-read ("wire") health, like `gflow doctor` (G6, #1229).

SCENARIO row 25. The probe's sending rules are proven in tests/api/test_flow_wire.py; here
the field's presence on every result, and the order (wire first, while no Chrome holds the
cookie jar), are under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api import flow_wire
from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from gflow_cli.mcp import tools

    order: list[str] = []
    monkeypatch.setattr(tools, "_resolve_and_validate_profile", lambda _p: "denon")

    async def health(_d: Path) -> dict[str, str | None]:
        order.append("wire")
        return {"status": "store_unreadable", "build": None}

    monkeypatch.setattr(flow_wire, "health", health)
    return order


def _verify(order: list[str], outcome: FlowSessionOutcome) -> object:
    async def fake(_d: Path, *, source: str = "chrome") -> FlowSessionStatus:
        order.append("browser")
        return FlowSessionStatus(outcome=outcome, user_email="u@example.com", source=source)

    return fake


@pytest.mark.parametrize("outcome", list(FlowSessionOutcome))
async def test_every_result_carries_the_wire_field_and_probes_it_first(
    monkeypatch: pytest.MonkeyPatch, calls: list[str], outcome: FlowSessionOutcome
) -> None:
    from gflow_cli.mcp import tools

    monkeypatch.setattr(tools.verification, "verify_flow_profile", _verify(calls, outcome))

    result = await tools.gflow_auth_status()

    assert result["wire"] == {"status": "store_unreadable", "build": None}
    assert calls == ["wire", "browser"]


def test_the_description_names_the_wire_field_and_its_statuses() -> None:
    from gflow_cli.mcp.tools import server as mcp_server

    tool = mcp_server._tool_manager._tools["gflow_auth_status"]  # pyright: ignore[reportPrivateUsage]
    text = tool.description or ""
    for word in ("wire", "accepted", "signed_out_shell", "store_unreadable", "disabled", "build"):
        assert word in text


async def test_a_throttled_wire_skips_the_browser_probe_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The browser probe would send more requests to an account Flow is throttling."""
    from gflow_cli.mcp import tools

    monkeypatch.setattr(tools, "_resolve_and_validate_profile", lambda _p: "denon")

    async def throttled(_d: Path) -> dict[str, object]:
        return {"status": "throttled", "build": None, "retry_after": 42}

    browser: list[Path] = []

    async def verify(d: Path, *, source: str = "chrome") -> FlowSessionStatus:
        browser.append(d)
        raise AssertionError("the browser probe must not run")

    monkeypatch.setattr(flow_wire, "health", throttled)
    monkeypatch.setattr(tools.verification, "verify_flow_profile", verify)

    result = await tools.gflow_auth_status()

    assert browser == []
    assert result["status"] == "throttled"
    assert result["wire"]["status"] == "throttled"
    assert result["error"]["status"] == 429
    assert result["error"]["retryable"] is True
    assert result["error"]["retry_after"] == 42
