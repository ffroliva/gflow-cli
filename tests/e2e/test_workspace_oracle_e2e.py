"""Real signed-in Flow workspace and anonymous negative control, no writes or quota."""

from pathlib import Path

import pytest

from gflow_cli.auth.strategies import async_playwright
from gflow_cli.auth.verification import _verify_migrated_workspace
from gflow_cli.browser_manager import channel_for_profile
from gflow_cli.profile_lease import ProfileLease

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


async def test_real_workspace_and_anonymous_control(e2e_profile_dir: Path) -> None:
    async with ProfileLease(e2e_profile_dir), async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(e2e_profile_dir),
            channel=channel_for_profile(e2e_profile_dir),
            headless=True,
            args=["--password-store=basic"],
        )
        try:
            status = await _verify_migrated_workspace(ctx, "e2e")
            assert status is not None and status.authenticated
            assert status.user_email
        finally:
            await ctx.close()
        browser = await pw.chromium.launch(
            channel=channel_for_profile(e2e_profile_dir), headless=True
        )
        try:
            anonymous = await browser.new_context()
            assert await _verify_migrated_workspace(anonymous, "e2e-anonymous") is None
        finally:
            await browser.close()
