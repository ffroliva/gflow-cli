"""$0 live proof that ``UiAutomationTransport._check_logged_in`` is locale-invariant (#1289).

Before the fix, ``_check_logged_in`` probed ``button:has-text('Sign in'),
a:has-text('Sign in')``. On a non-English locale such as Portuguese
(``hl=pt-BR``, where the Google Bar renders ``Fazer login``), a signed-out landing
on ``flow.google.com/about`` had zero matches and read as signed in (``True``).

These two tests drive the production ``_check_logged_in`` gate against real Chrome
with ``Accept-Language: pt-BR,pt;q=0.9`` and ``?hl=pt-BR``:

1. Signed-out empty profile -> rejects ``/about`` as ``False`` and observes the
   locale-invariant ``ServiceLogin`` / ``flow-landing-page`` anchors on ``pt``.
2. Signed-in profile -> settles on the Flow workspace as ``True`` with zero matches
   on ``_SIGNIN_CTA_SELECTOR`` (proving no collision with ``SignOutOptions``).

Cost: 0 credits (navigation and read-only DOM counts only).

    GFLOW_CLI_E2E_PROFILE=<profile> uv run pytest -m e2e_auth \
        tests/e2e/test_check_logged_in_locale_e2e.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.transports._common import flow_host_kind, flow_landing_kind
from gflow_cli.api.transports._ui_session import _SIGNIN_CTA_SELECTOR  # noqa: PLC2701
from gflow_cli.api.transports.ui_automation import UiAutomationTransport

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


async def test_e2e_check_logged_in_rejects_signed_out_pt_br(
    e2e_nosession_profile: Path,
) -> None:
    """Signed-out Portuguese landing must evaluate ``_check_logged_in`` as False."""
    # Prefer system Chrome when Playwright's Chrome channel is installed;
    # channel_for_profile() still falls back to bundled Chromium otherwise.
    (e2e_nosession_profile / ".gflow_browser_strategy").write_text("chrome", encoding="utf-8")
    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_nosession_profile)
        page = transport._page  # noqa: SLF001 - drives the live page directly
        assert page is not None
        await page.set_extra_http_headers({"Accept-Language": "pt-BR,pt;q=0.9"})
        await page.goto(
            "https://flow.google.com/?hl=pt-BR",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        await page.locator(_SIGNIN_CTA_SELECTOR).first.wait_for(
            state="attached",
            timeout=20_000,
        )
        # Check immediately on mount (often still on /?hl=pt-BR before the client
        # redirect to /about, exercising the structural DOM selector directly).
        assert await UiAutomationTransport._check_logged_in(page) is False  # noqa: SLF001
        await page.locator("a[href^='https://accounts.google.com/ServiceLogin']").first.wait_for(
            state="attached",
            timeout=10_000,
        )
        lang = await page.evaluate("document.documentElement.lang")
        assert isinstance(lang, str) and lang.lower().startswith("pt"), lang
        assert flow_host_kind(page.url) == "migrated", page.url
        assert await UiAutomationTransport._check_logged_in(page) is False  # noqa: SLF001
    finally:
        await transport.teardown()


async def test_e2e_check_logged_in_accepts_signed_in_pt_br(
    e2e_profile_dir: Path,
) -> None:
    """Signed-in Portuguese session must evaluate ``_check_logged_in`` as True."""
    transport = UiAutomationTransport()
    try:
        await transport.setup(e2e_profile_dir)
        page = transport._page  # noqa: SLF001 - drives the live page directly
        assert page is not None
        await page.set_extra_http_headers({"Accept-Language": "pt-BR,pt;q=0.9"})
        await page.goto(
            "https://flow.google.com/?hl=pt-BR",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        await page.locator("aisandbox-root").first.wait_for(
            state="attached",
            timeout=20_000,
        )
        await page.wait_for_timeout(3_000)
        assert flow_host_kind(page.url) is not None, page.url
        assert flow_landing_kind(page.url) is None, page.url
        assert await page.locator(_SIGNIN_CTA_SELECTOR).count() == 0
        assert await UiAutomationTransport._check_logged_in(page) is True  # noqa: SLF001
    finally:
        await transport.teardown()
