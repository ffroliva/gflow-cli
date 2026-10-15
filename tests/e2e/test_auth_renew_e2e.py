"""Live proof of the `gflow auth renew` leaf — profile selection, JSON rows, exit codes.

`tests/e2e/test_credits_refresh_e2e.py` drives the renewal only through
`credits --refresh`. This drives the command a cron job or a user actually types.

Profiles (all optional except the first; a missing one skips its test):

* ``GFLOW_CLI_E2E_PROFILE`` — any live profile; renewed and re-read.
* ``GFLOW_CLI_E2E_STALE_PROFILES`` — the third entry (index 2) is used here, so it does
  not collide with the two the credits-refresh e2e consumes. Must fail the credits fast
  path with an expired session; skips otherwise (a renewed labs token lasts ~24 h).
* ``GFLOW_CLI_E2E_ABOUT_PROFILE`` — a profile Flow holds at its public ``/about`` page
  (KNOWN_ISSUES). Renewal must NOT report it renewed.

Opt-in: ``-m e2e_auth``. Headless Flow + labs page loads; spends no credits.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from gflow_cli.errors import AuthExpiredError

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


async def _renew(*args: str) -> tuple[int, list[dict[str, Any]]]:
    from gflow_cli.cli import main

    # The command runs its own event loop, so it is invoked off this test's loop.
    result = await asyncio.to_thread(CliRunner().invoke, main, ["auth", "renew", *args, "--json"])
    rows_line = next(line for line in result.output.splitlines() if line.startswith("["))
    return result.exit_code, json.loads(rows_line)


async def _is_stale(profile: str) -> bool:
    from gflow_cli.services import credits

    try:
        await credits.inspect_profile(profile)
    except AuthExpiredError:
        return True
    return False


async def test_auth_renew_verifies_a_live_profile_and_leaves_it_readable(
    e2e_profile_dir: Path,
) -> None:
    from gflow_cli.services import credits

    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()
    # "Never worse than it found it" needs a balance to compare against. An account whose
    # labs session issues no token (#795) reads none before or after.
    if await _is_stale(profile):
        pytest.skip(f"{profile} reads no balance before renewal (#795); nothing to compare")

    code, rows = await _renew("--profile", profile)

    assert code == 0, rows
    assert [r["profile"] for r in rows] == [profile]
    assert rows[0]["result"] == "authenticated"
    # A renewal must never leave the profile worse than it found it.
    assert (await credits.inspect_profile(profile))["authenticated"] is True


async def test_auth_renew_skips_a_profile_another_holder_is_using(
    e2e_profile_dir: Path,
) -> None:
    from gflow_cli.profile_lease import ProfileLease

    profile = os.environ["GFLOW_CLI_E2E_PROFILE"].strip()

    async with ProfileLease(e2e_profile_dir):
        code, rows = await _renew("--profile", profile)

    assert code == 0, rows  # skipped, not failed
    assert rows[0]["result"] == "in_use"


async def test_auth_renew_turns_a_stale_profile_readable(e2e_profile_dir: Path) -> None:
    from gflow_cli.services import credits

    names = [n.strip() for n in os.environ.get("GFLOW_CLI_E2E_STALE_PROFILES", "").split(",")]
    names = [n for n in names if n]
    if len(names) < 3:
        pytest.skip("GFLOW_CLI_E2E_STALE_PROFILES needs a third profile for auth renew")
    profile = names[2]
    if not await _is_stale(profile):
        pytest.skip(f"{profile} is not stale — its labs token has not lapsed yet")

    code, rows = await _renew("--profile", profile)

    assert code == 0, rows
    assert rows[0]["result"] == "authenticated"
    assert (await credits.inspect_profile(profile))["authenticated"] is True


async def test_auth_renew_does_not_claim_an_about_profile_renewed(
    e2e_profile_dir: Path,
) -> None:
    """PR #982 review: the verifier says fine on an /about account, so a renewal that
    trusted it alone printed "renewed and verified" and exited 0 for profiles it had
    not fixed. Measured 2026-10-14: 3 of 10 profiles."""
    profile = os.environ.get("GFLOW_CLI_E2E_ABOUT_PROFILE", "").strip()
    if not profile:
        pytest.skip("GFLOW_CLI_E2E_ABOUT_PROFILE is not set")
    if not await _is_stale(profile):
        pytest.skip(f"{profile} reads a balance — it is not held at /about any more")

    code, rows = await _renew("--profile", profile)

    assert code == 1, rows
    # The landing wins over the verdict (RenewResult.result), so an /about account is
    # named as such whatever the verifier says — not merely "not authenticated".
    assert rows[0]["result"] == "public_landing"
    assert await _is_stale(profile)
