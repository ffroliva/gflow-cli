"""E2E for #902: `auth login` must watch where Flow LANDS, not only what the cookies say.

Drives the real owned-browser login (`RealChromeStrategy`, headed real Chrome) against
real Flow. Zero credits: it signs nothing in, creates nothing and submits nothing.

* ``test_healthy_login_still_closes_by_itself``: ``GFLOW_CLI_E2E_PROFILE``, a profile
  whose session is healthy. It must still auto-close and verify, now after the landing
  watch.
* ``test_about_account_is_not_reported_signed_in``: ``GFLOW_CLI_E2E_ABOUT_PROFILE``, a
  profile Flow currently routes to ``flow.google.com/about``. The login must hold Chrome
  open and then exit with ``IdentityRecheckPendingError``. It must not print ``[OK]``.
  Nobody finishes Google's check here, so the account is left exactly as it was.

Async tests need no ``@pytest.mark.asyncio``: ``asyncio_mode = "auto"``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from structlog.testing import capture_logs

from gflow_cli.auth.real_chrome import RealChromeStrategy
from gflow_cli.errors import IdentityRecheckPendingError

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

_ABOUT_PROFILE_ENV = "GFLOW_CLI_E2E_ABOUT_PROFILE"


@pytest.fixture
def about_profile_dir(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Path:
    name = os.environ.get(_ABOUT_PROFILE_ENV, "").strip()
    if not name:
        pytest.skip(f"set {_ABOUT_PROFILE_ENV} to a profile Flow routes to /about")
    monkeypatch.setenv("GFLOW_CLI_E2E_PROFILE", name)
    return request.getfixturevalue("e2e_profile_dir")


async def test_healthy_login_still_closes_by_itself(e2e_profile_dir: Path) -> None:
    with capture_logs() as logs:
        await RealChromeStrategy(timeout_seconds=120).login(e2e_profile_dir, headless=False)

    events = [e.get("event") for e in logs]
    assert "auth_login_session_detected" in events or (
        "auth_login_migrated_session_detected" in events
    ), events
    assert "auth_login_identity_recheck_pending" not in events
    assert (e2e_profile_dir / ".gflow_account").read_text(encoding="utf-8").strip()


async def test_about_account_is_not_reported_signed_in(about_profile_dir: Path) -> None:
    with capture_logs() as logs:
        try:
            await RealChromeStrategy(timeout_seconds=45).login(about_profile_dir, headless=False)
        except IdentityRecheckPendingError:
            pass
        except Exception as exc:
            # Not the recheck state at all (e.g. a signed-out profile): say so, with
            # the event trail, instead of a bare traceback that reads as a regression.
            trail = [(e.get("event"), e.get("outcome"), e.get("probe")) for e in logs]
            pytest.fail(f"{type(exc).__name__} — profile is not on /about? trail={trail}")
        else:
            pytest.fail("login reported success on a profile routed to /about")

    events = [e.get("event") for e in logs]
    assert "auth_login_identity_recheck_pending" in events, events
    assert "auth_flow_session_verified" not in [
        e.get("event") for e in logs if e.get("probe") == "on_disk"
    ]
