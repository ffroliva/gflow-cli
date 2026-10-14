"""Live proof that `--refresh` turns an expired credits read into a balance.

Offline tests fake both the fast path and `auth/renew.py`, so they prove the control
flow, never the claim: that loading Flow headlessly rotates the cookies on disk and the
SAME fast path then reads a new session. Only a profile that is genuinely stale can show
that, and a profile stops being stale the moment one renewal succeeds. So each door
takes its own profile from ``GFLOW_CLI_E2E_STALE_PROFILES`` (comma-separated: the first
for the MCP twin, the second for the CLI), and a door whose profile turns out fresh
skips rather than passing on a read that never needed renewing.

Opt-in: ``-m e2e_auth``. Read-only GETs plus one headless Flow page load per door;
spends no credits. A profile Google has revoked, or one on the host #795 covers, fails
the renewal for reasons this test does not own — pick profiles that `auth renew` fixes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from gflow_cli.errors import AuthExpiredError

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


def _stale_profile(index: int) -> str:
    names = [
        n.strip()
        for n in os.environ.get("GFLOW_CLI_E2E_STALE_PROFILES", "").split(",")
        if n.strip()
    ]
    if len(names) <= index:
        pytest.skip(f"GFLOW_CLI_E2E_STALE_PROFILES needs at least {index + 1} profile(s)")
    return names[index]


def _spy_on_renewals(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    from gflow_cli.services import credits

    calls: list[Path] = []
    real = credits.renew_profile

    async def spy(profile_dir: Path, *, source: str = "renew") -> Any:
        calls.append(profile_dir)
        return await real(profile_dir, source=source)

    monkeypatch.setattr(credits, "renew_profile", spy)
    return calls


async def _require_stale(profile: str) -> None:
    from gflow_cli.services import credits

    try:
        await credits.inspect_profile(profile)
    except AuthExpiredError:
        return
    pytest.skip(f"{profile} is not stale — its fast path already reads a balance")


async def test_mcp_get_credits_refresh_renews_a_stale_profile(
    e2e_profile_dir: Path,  # points GFLOW_CLI_HOME at the real profile store
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.mcp import tools

    profile = _stale_profile(0)
    await _require_stale(profile)
    renewals = _spy_on_renewals(monkeypatch)

    result: dict[str, Any] = await tools.gflow_get_credits(profile=profile, refresh=True)

    assert len(renewals) == 1
    assert result.get("status") == "ok", f"refresh did not recover the profile: {result}"
    assert result["authenticated"] is True
    assert isinstance(result["credits"], int)


async def test_cli_credits_user_refresh_renews_a_stale_profile(
    e2e_profile_dir: Path,  # points GFLOW_CLI_HOME at the real profile store
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.cli import main

    profile = _stale_profile(1)
    await _require_stale(profile)
    renewals = _spy_on_renewals(monkeypatch)

    # CliRunner drives the real command; the command runs its own event loop, so it is
    # invoked from a worker thread rather than inside this test's running loop.
    import asyncio

    result = await asyncio.to_thread(
        CliRunner().invoke,
        main,
        ["credits", "user", "--profile", profile, "--refresh", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output[result.output.index("{\n") :])
    assert len(renewals) == 1
    assert payload["authenticated"] is True
    assert isinstance(payload["credits"], int)
