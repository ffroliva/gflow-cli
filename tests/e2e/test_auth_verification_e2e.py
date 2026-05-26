"""E2E tests for the issue-#15 Flow-session verification feature.

These probe the REAL Google `/api/auth/session` endpoint via
`verify_flow_session`, and (positive case) confirm a verified profile is
actually usable by `FlowApiClient`. Opt-in: `-m e2e` + `GFLOW_CLI_E2E_PROFILE`.
Zero Flow credits are spent - no image generation.

Async tests need no `@pytest.mark.asyncio` decorator: `asyncio_mode = "auto"`
is set in pyproject.toml.

See docs/superpowers/specs/2026-05-17-e2e-test-coverage-design.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.auth.verification import FlowSessionOutcome, verify_flow_session

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


async def test_e2e_verify_flow_session_authenticated(e2e_profile_dir: Path) -> None:
    """A logged-in profile verifies as AUTHENTICATED and is usable by the client.

    Two linked assertions - the real issue-#15 invariant:
      1. verify_flow_session pronounces the profile AUTHENTICATED.
      2. the SAME profile actually works: FlowApiClient.health_check() is True.
    """
    status = await verify_flow_session(e2e_profile_dir, channel="chrome", source="chrome")
    assert status.outcome is FlowSessionOutcome.AUTHENTICATED, (
        f"expected AUTHENTICATED, got {status.outcome} - is the profile's "
        "Flow session still valid? Re-run `gflow auth login` if not."
    )
    assert isinstance(status.user_email, str) and status.user_email, (
        "an AUTHENTICATED status must carry a non-empty user_email"
    )
    assert status.authenticated is True

    # Verified => usable: a profile pronounced AUTHENTICATED must actually work.
    async with FlowApiClient(profile_dir=e2e_profile_dir, transport="evaluate_fetch") as client:
        assert await client.health_check() is True, (
            "a verified profile must pass FlowApiClient.health_check()"
        )


async def test_e2e_verify_flow_session_no_session(
    e2e_nosession_profile: Path,
) -> None:
    """A fresh, empty profile verifies as NO_SESSION.

    Empty profile dir -> real headless Chrome launches -> no SAPISID cookie ->
    `/api/auth/session` returns `200 {}` -> NO_SESSION. Zero credits.

    Environmental caveat: the probe needs outbound network to labs.google.
    A VERIFICATION_ERROR result indicates no connectivity, not a bug.
    """
    status = await verify_flow_session(e2e_nosession_profile, channel="chrome", source="chrome")
    assert status.outcome is FlowSessionOutcome.NO_SESSION, (
        f"expected NO_SESSION for an empty profile, got {status.outcome}. "
        "If VERIFICATION_ERROR: check network connectivity to labs.google."
    )
