"""Live proof that the MCP credits twin works against real Flow.

`tests/e2e/test_credits_e2e.py` proves the CLI-side HTTP fast path. This proves the
*other* surface: `gflow_get_credits`, the tool an agent actually calls. The two share
`services/credits.py`, so the delta under test here is the MCP adapter — profile
resolution and validation, the `all_profiles` branch, and the shape of the dict that
crosses the tool boundary.

That delta is exactly what an offline test cannot see. `tests/mcp/test_credits_tool.py`
fakes the service, so it asserts the adapter forwards a dict it was handed; only a live
run shows the adapter resolving a real profile against real saved cookies and returning a
real balance.

Opt-in: ``-m e2e_auth`` with ``GFLOW_CLI_E2E_PROFILE`` set. Read-only GETs; spends
nothing. `[[feedback-e2e-is-the-required-evidence-layer]]`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.dto import CreditsInfo

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

#: Every key the tool contract promises a caller for a successful single-profile read.
_SUCCESS_KEYS = {
    "status",
    "profile",
    "is_default",
    "email",
    "authenticated",
    *(f.name for f in CreditsInfo.__dataclass_fields__.values()),
}


async def test_mcp_get_credits_returns_a_real_balance_for_one_profile(
    e2e_profile_dir: Path,
) -> None:
    """The MCP tool resolves a real profile and reports its live balance."""
    from gflow_cli.mcp import tools

    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()
    result: dict[str, Any] = await tools.gflow_get_credits(profile=profile)

    assert result.get("status") == "ok", f"MCP credits call failed: {result}"
    assert result["authenticated"] is True
    assert result["profile"] == profile
    # The balance is a real reading, not a coerced default: CreditsInfo.from_response
    # fails closed rather than turning a malformed value into zero.
    assert isinstance(result["credits"], int)
    assert result["credits"] >= 0
    # The contract an agent codes against — a missing key is a silent breakage for it.
    assert _SUCCESS_KEYS <= set(result), f"missing keys: {_SUCCESS_KEYS - set(result)}"


async def test_mcp_get_credits_all_profiles_preserves_partial_results(
    e2e_profile_dir: Path,
) -> None:
    """`all_profiles=True` reports every saved profile, and one bad profile does not sink it.

    A machine with any stale profile is the normal case, so this asserts the aggregate
    survives a mixture rather than requiring every profile to be healthy.
    """
    from gflow_cli.mcp import tools

    result: dict[str, Any] = await tools.gflow_get_credits(all_profiles=True)

    assert result["status"] in {"ok", "partial"}
    profiles = result["profiles"]
    assert profiles, "no saved profiles found — the e2e environment has none to read"
    assert result["count"] == len(profiles)

    healthy = [p for p in profiles if p["authenticated"]]
    assert healthy, f"no profile authenticated; statuses: {[p.get('error') for p in profiles]}"
    assert result["total_credits"] == sum(int(p["credits"]) for p in healthy)

    # A failed profile must degrade to its own row rather than raising: that is the
    # whole point of the partial-result contract an agent depends on.
    for row in profiles:
        assert {"profile", "authenticated", "is_default"} <= set(row)
        if not row["authenticated"]:
            assert row["credits"] is None
            assert row.get("error"), "a failed profile must carry a reason"


async def test_mcp_get_credits_rejects_an_unknown_profile_without_raising(
    e2e_profile_dir: Path,
) -> None:
    """An agent passing a bad profile gets a structured refusal, not an exception.

    An MCP tool that raises crosses the boundary as a transport error the model cannot
    reason about; a returned error dict is something it can act on.
    """
    from gflow_cli.mcp import tools

    result: dict[str, Any] = await tools.gflow_get_credits(profile="definitely-not-a-profile")

    assert isinstance(result, dict)
    assert result.get("status") != "ok"
