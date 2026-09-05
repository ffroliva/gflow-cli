"""Live proof that credits use the browser-free HTTP path.

Opt-in: ``-m e2e_auth`` with ``GFLOW_CLI_E2E_PROFILE`` set. The test performs
two read-only GET requests and spends no Flow credits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.credits import fetch_credits_http

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]


async def test_credits_http_fast_path_live(
    e2e_profile_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.auth import cookies

    async def reject_browser_fallback(profile_dir: Path) -> None:
        pytest.fail(f"credits launched a browser for {profile_dir}")

    monkeypatch.setattr(cookies, "_get_chrome_cookies_playwright", reject_browser_fallback)
    info = await fetch_credits_http(e2e_profile_dir)

    assert isinstance(info.credits, int)
    assert info.credits >= 0
