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


async def test_credits_never_sends_a_live_profile_to_re_login(
    e2e_profile_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#795, $0: whatever this account's cohort, `credits` answers honestly.

    Two outcomes are correct and this asserts the invariant across both — which is
    what lets it run on any profile, on a cohort we do not control and cannot choose:

    * the balance reads (labs-served account), or
    * aisandbox-pa refuses, and the failure says so **without** blaming SAPISID or
      prescribing `gflow auth login`. On an account Google has migrated to
      flow.google.com that advice cannot work and is actively harmful: re-login can
      roll the profile's `.gflow_browser_strategy` marker back and start #791.

    It also pins the no-browser claim on the failing branch. Before this fix the
    service swallowed the fast path's verdict and re-derived it through Chrome
    (~7 s), which is where the SAPISID wording came from — the shared aisandbox
    retry helper in `api/client.py` is route-blind and carries the class default.
    """
    from gflow_cli.auth import cookies
    from gflow_cli.errors import AisandboxAuthError
    from gflow_cli.services import credits as credits_service

    profile = e2e_profile_dir.name.removeprefix("profile_")

    def reject_browser(*args: object, **kwargs: object) -> None:
        pytest.fail("credits launched a browser to re-derive an answer it already had")

    # Both doors to a browser, so "no browser launched" is literal rather than
    # path-true: the service-level fallback, and the cookie reader's own
    # PermissionError-gated Playwright fallback.
    monkeypatch.setattr(credits_service, "FlowApiClient", reject_browser)
    monkeypatch.setattr(cookies, "_get_chrome_cookies_playwright", reject_browser)

    try:
        result = await credits_service.inspect_profile(profile)
    except AisandboxAuthError as exc:
        hint = exc.remediation_hint
        assert "SAPISID" not in hint, f"blames SAPISID for a credential that is fine: {hint}"
        assert "auth login" not in hint, f"prescribes a re-login that cannot help: {hint}"
        assert "flow.google.com" in hint, f"does not name the cohort: {hint}"
    else:
        assert result["authenticated"] is True
        assert result["credits"] >= 0
        # A served balance is a correct outcome, but it exercised none of #795. Skip
        # rather than pass: the nightly canary runs -m e2e_auth, so a silent green here
        # would let a cohort change retire this test without anyone noticing.
        pytest.skip("labs cohort — the #795 honest-failure branch was not exercised")
