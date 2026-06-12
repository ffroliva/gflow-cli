"""E2E tests for the issue-#15 Flow-session verification feature.

These probe the REAL Google `/api/auth/session` endpoint via
`verify_flow_profile`, and (positive case) confirm a verified profile is
actually usable by `FlowApiClient`. Opt-in: `-m e2e` + `GFLOW_CLI_E2E_PROFILE`.
Zero Flow credits are spent - no image generation.

Async tests need no `@pytest.mark.asyncio` decorator: `asyncio_mode = "auto"`
is set in pyproject.toml.

See docs/superpowers/specs/2026-05-17-e2e-test-coverage-design.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.auth.verification import FlowSessionOutcome, verify_flow_profile

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


async def test_e2e_verify_flow_profile_authenticated(e2e_profile_dir: Path) -> None:
    """A logged-in profile verifies as AUTHENTICATED and is usable by the client.

    Two linked assertions - the real issue-#15 invariant:
      1. verify_flow_profile pronounces the profile AUTHENTICATED.
      2. the SAME profile actually works: FlowApiClient.health_check() is True.
    """
    status = await verify_flow_profile(e2e_profile_dir, source="chrome")
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


async def test_e2e_verify_flow_profile_no_session(
    e2e_nosession_profile: Path,
) -> None:
    """A fresh, empty profile verifies as NO_SESSION.

    Empty profile dir -> direct cookie extraction finds no SAPISID cookie ->
    `/api/auth/session` returns `200 {}` -> NO_SESSION. Zero credits.

    Environmental caveat: the probe needs outbound network to labs.google.
    A VERIFICATION_ERROR result indicates no connectivity, not a bug.
    """
    status = await verify_flow_profile(e2e_nosession_profile, source="chrome")
    assert status.outcome is FlowSessionOutcome.NO_SESSION, (
        f"expected NO_SESSION for an empty profile, got {status.outcome}. "
        "If VERIFICATION_ERROR: check network connectivity to labs.google."
    )


async def test_e2e_verify_flow_profile_falls_back_to_playwright(
    e2e_profile_dir: Path,
) -> None:
    """If direct cookie decryption fails, the Chrome-strategy marker should enable fallback.

    The browser-cookie3 path is forced to fail so the test exercises the
    Playwright fallback against a real, logged-in profile.
    """

    class BrowserCookieError(Exception):
        pass

    fake_bc3 = MagicMock()
    fake_bc3.BrowserCookieError = BrowserCookieError
    fake_bc3.chrome.side_effect = BrowserCookieError("permission denied")

    with patch.dict(sys.modules, {"browser_cookie3": fake_bc3}):
        status = await verify_flow_profile(e2e_profile_dir, source="chrome")

    assert status.outcome is FlowSessionOutcome.AUTHENTICATED
    assert isinstance(status.user_email, str) and status.user_email
