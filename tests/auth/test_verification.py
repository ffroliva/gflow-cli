from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.auth.verification import (
    FlowSessionOutcome,
    FlowSessionStatus,  # noqa: F401 — imported to assert it's part of the public API
    evaluate_session_response,
    verify_flow_profile,
    verify_flow_session,
)
from gflow_cli.errors import SecurityError

# Representative authenticated /api/auth/session body. Sanitised — no real
# PII. Pins the endpoint contract: if Google changes the response shape, the
# AUTHENTICATED assertions below fail loudly instead of the change going silent.
AUTHENTICATED_BODY = json.dumps(
    {
        "user": {
            "name": "Test User",
            "email": "test.user@example.com",
            "image": "https://lh3.googleusercontent.com/a/fake",
        },
        "expires": "2026-06-16T08:39:21.000Z",
    }
)


class TestEvaluateSessionResponse:
    def test_authenticated_user_with_email(self) -> None:
        status = evaluate_session_response(
            200, AUTHENTICATED_BODY, google_session=True, source="chrome"
        )
        assert status.outcome is FlowSessionOutcome.AUTHENTICATED
        assert status.authenticated is True
        assert status.user_email == "test.user@example.com"
        assert status.detail == "Flow app session verified."

    def test_empty_session_with_google_cookie(self) -> None:
        status = evaluate_session_response(200, "{}", google_session=True, source="chrome")
        assert status.outcome is FlowSessionOutcome.GOOGLE_SESSION_ONLY
        assert status.authenticated is False
        assert status.user_email is None

    def test_empty_session_no_google_cookie(self) -> None:
        status = evaluate_session_response(200, "{}", google_session=False, source="chrome")
        assert status.outcome is FlowSessionOutcome.NO_SESSION

    def test_null_user_does_not_crash(self) -> None:
        status = evaluate_session_response(
            200, '{"user": null}', google_session=False, source="chrome"
        )
        assert status.outcome is FlowSessionOutcome.NO_SESSION

    def test_null_user_with_google_cookie(self) -> None:
        status = evaluate_session_response(
            200, '{"user": null}', google_session=True, source="chrome"
        )
        assert status.outcome is FlowSessionOutcome.GOOGLE_SESSION_ONLY

    @pytest.mark.parametrize(
        "body",
        [
            '{"user": {"name": "x"}}',  # user present, no email key
            '{"user": {"email": ""}}',  # empty-string email
            '{"user": ["not", "a", "dict"]}',  # user is not a dict
            "[]",  # JSON array, not an object
            '{"user":',  # truncated JSON
            "",  # empty body
            "   ",  # whitespace only
            "not json at all",  # garbage
        ],
    )
    def test_unexpected_or_malformed_body_is_verification_error(self, body: str) -> None:
        status = evaluate_session_response(200, body, google_session=True, source="chrome")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        assert status.detail == "Could not verify the Flow session."

    @pytest.mark.parametrize("status_code", [302, 401, 403, 404, 500, 503])
    def test_non_200_is_verification_error(self, status_code: int) -> None:
        # google_session is irrelevant on the error path.
        status = evaluate_session_response(
            status_code, AUTHENTICATED_BODY, google_session=True, source="chrome"
        )
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR

    def test_source_is_passed_through(self) -> None:
        status = evaluate_session_response(200, "{}", google_session=False, source="internal")
        assert status.source == "internal"


# ---------------------------------------------------------------------------
# verify_flow_session — async headless probe
# ---------------------------------------------------------------------------


def _build_verify_mock(
    *,
    cookies: list[dict] | None = None,
    response_status: int = 200,
    response_body: str = "{}",
    get_side_effect: object = None,
) -> tuple[MagicMock, MagicMock]:
    """Return (mock_async_playwright, mock_ctx) for verify_flow_session.

    Mocks the headless persistent context: ctx.cookies(), ctx.request.get()
    (an APIResponse-like object with `.status` and async `.text()`), and
    ctx.close(). Patch target for the shim is gflow_cli.auth.strategies.
    """
    if cookies is None:
        cookies = [{"name": "SAPISID", "value": "x"}]

    mock_resp = MagicMock(name="resp")
    mock_resp.status = response_status
    mock_resp.text = AsyncMock(return_value=response_body)

    mock_request = MagicMock(name="request")
    if get_side_effect is not None:
        mock_request.get = AsyncMock(side_effect=get_side_effect)
    else:
        mock_request.get = AsyncMock(return_value=mock_resp)

    mock_ctx = MagicMock(name="ctx")
    mock_ctx.cookies = AsyncMock(return_value=cookies)
    mock_ctx.request = mock_request
    mock_ctx.close = AsyncMock()

    mock_pw_obj = MagicMock(name="pw")
    mock_pw_obj.chromium.launch_persistent_context = AsyncMock(return_value=mock_ctx)

    mock_cm = MagicMock(name="cm")
    mock_cm.__aenter__ = AsyncMock(return_value=mock_pw_obj)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_ap = MagicMock(name="async_playwright", return_value=mock_cm)
    return mock_ap, mock_ctx


class TestVerifyFlowSession:
    @pytest.fixture
    def gflow_home(self, tmp_path: Path) -> Path:
        home = tmp_path / "gflow_home"
        home.mkdir()
        return home

    @pytest.mark.asyncio
    async def test_authenticated_profile(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        mock_ap, mock_ctx = _build_verify_mock(response_body=AUTHENTICATED_BODY)
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, channel="chrome", source="chrome")
        assert status.outcome is FlowSessionOutcome.AUTHENTICATED
        assert status.user_email == "test.user@example.com"
        mock_ctx.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_probe_wraps_launch_in_profile_lease(
        self, gflow_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """verify_flow_session owns the profile for its headless probe context:
        acquire before launch, release after the context closes (D3)."""
        from gflow_cli.profile_lease import ProfileLease

        profile = gflow_home / "profile_default"
        profile.mkdir()

        events: list[str] = []

        def acq(self: ProfileLease) -> ProfileLease:
            events.append("acquire")
            return self

        monkeypatch.setattr(ProfileLease, "acquire", acq)
        monkeypatch.setattr(ProfileLease, "release", lambda self: events.append("release"))

        mock_ap, mock_ctx = _build_verify_mock(response_body=AUTHENTICATED_BODY)
        chromium = mock_ap.return_value.__aenter__.return_value.chromium
        original_launch = chromium.launch_persistent_context

        async def _launch(*a: object, **k: object) -> object:
            events.append("launch")
            return await original_launch(*a, **k)

        chromium.launch_persistent_context = _launch

        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            await verify_flow_session(profile, channel="chrome", source="chrome")
        assert events == ["acquire", "launch", "release"]

    @pytest.mark.asyncio
    async def test_authenticated_profile_httpx(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()

        fake_bc3 = MagicMock()
        fake_bc3.chrome.return_value = [
            SimpleNamespace(name="SAPISID", value="google", domain=".google.com"),
            SimpleNamespace(
                name="__Secure-next-auth.session-token",
                value="flow-session",
                domain="labs.google",
            ),
        ]

        fake_resp = MagicMock(status_code=200, text=AUTHENTICATED_BODY)
        fake_client = MagicMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.get = AsyncMock(return_value=fake_resp)

        fake_httpx = MagicMock()
        fake_httpx.AsyncClient.return_value = fake_client

        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.cookies.get_cookies_path",
                return_value=Path("/fake/Cookies"),
            ),
            patch.dict(sys.modules, {"browser_cookie3": fake_bc3, "httpx": fake_httpx}),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_profile(profile)

        assert status.outcome is FlowSessionOutcome.AUTHENTICATED
        assert status.user_email == "test.user@example.com"
        _, client_kwargs = fake_httpx.AsyncClient.call_args
        assert client_kwargs["cookies"] == {"__Secure-next-auth.session-token": "flow-session"}
        assert client_kwargs["follow_redirects"] is False

    @pytest.mark.asyncio
    async def test_profile_outside_home_raises_security_error_httpx(self, gflow_home: Path) -> None:
        outside = gflow_home.parent / "outside_profile"
        outside.mkdir()
        with patch("gflow_cli.auth.verification.get_settings") as mock_settings:
            mock_settings.return_value.home = gflow_home
            with pytest.raises(SecurityError):
                await verify_flow_profile(outside)

    @pytest.mark.asyncio
    async def test_missing_cookie_store_is_no_session(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()

        fake_resp = MagicMock(status_code=200, text="{}")
        fake_client = MagicMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.get = AsyncMock(return_value=fake_resp)

        fake_httpx = MagicMock()
        fake_httpx.AsyncClient.return_value = fake_client

        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.cookies.get_cookies_path",
                side_effect=FileNotFoundError,
            ),
            patch.dict(sys.modules, {"httpx": fake_httpx}),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_profile(profile)

        assert status.outcome is FlowSessionOutcome.NO_SESSION
        _, client_kwargs = fake_httpx.AsyncClient.call_args
        assert client_kwargs["cookies"] == {}

    @pytest.mark.asyncio
    async def test_verification_error_on_browser_cookie_permission_error(
        self, gflow_home: Path
    ) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()

        fake_bc3 = MagicMock()
        fake_bc3.chrome.side_effect = PermissionError("Permission denied")

        fake_httpx = MagicMock()

        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.cookies.get_cookies_path",
                return_value=Path("/fake/Cookies"),
            ),
            patch.dict(sys.modules, {"browser_cookie3": fake_bc3, "httpx": fake_httpx}),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_profile(profile)

        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR

    @pytest.mark.asyncio
    async def test_playwright_fallback_requires_chrome_marker(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()

        class BrowserCookieError(Exception):
            pass

        fake_bc3 = MagicMock()
        fake_bc3.BrowserCookieError = BrowserCookieError
        fake_bc3.chrome.side_effect = BrowserCookieError("Unable to get key")

        fake_httpx = MagicMock()
        fake_async_playwright = MagicMock()

        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.cookies.get_cookies_path",
                return_value=Path("/fake/Cookies"),
            ),
            patch.dict(sys.modules, {"browser_cookie3": fake_bc3, "httpx": fake_httpx}),
            patch("gflow_cli.auth.strategies.async_playwright", fake_async_playwright),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_profile(profile)

        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        fake_async_playwright.assert_not_called()

    @pytest.mark.asyncio
    async def test_verification_error_on_browser_cookie_decryption_error(
        self, gflow_home: Path
    ) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()

        # Define a mock exception mimicking browser_cookie3.BrowserCookieError
        class BrowserCookieError(Exception):
            pass

        fake_bc3 = MagicMock()
        fake_bc3.BrowserCookieError = BrowserCookieError
        fake_bc3.chrome.side_effect = BrowserCookieError("Unable to get key for cookie decryption")

        fake_httpx = MagicMock()

        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.cookies.get_cookies_path",
                return_value=Path("/fake/Cookies"),
            ),
            patch.dict(sys.modules, {"browser_cookie3": fake_bc3, "httpx": fake_httpx}),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_profile(profile)

        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR

    @pytest.mark.asyncio
    async def test_dpapi_runtime_error_triggers_playwright_fallback(self, gflow_home: Path) -> None:
        """Regression for fix #1: RuntimeError('Failed to decrypt the cipher text with DPAPI')

        browser-cookie3==0.20.1 raises RuntimeError (not BrowserCookieError) from
        _crypt_unprotect_data on Windows when the DPAPI master key is unavailable.
        This must be caught and re-raised as PermissionError so get_chrome_cookie_snapshot
        routes it to the Playwright fallback, not silently returns VERIFICATION_ERROR.
        """
        profile = gflow_home / "profile_default"
        profile.mkdir()
        # Write chrome marker so the Playwright fallback is allowed.
        (profile / ".gflow_browser_strategy").write_text("chrome", encoding="utf-8")

        # Simulate DPAPI failure — RuntimeError, not BrowserCookieError.
        class BrowserCookieError(Exception):
            pass

        fake_bc3 = MagicMock()
        fake_bc3.BrowserCookieError = BrowserCookieError
        fake_bc3.chrome.side_effect = RuntimeError("Failed to decrypt the cipher text with DPAPI")

        # httpx mock: returns the authenticated session body.
        fake_resp = MagicMock(status_code=200, text=AUTHENTICATED_BODY)
        fake_client = MagicMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.get = AsyncMock(return_value=fake_resp)
        fake_httpx = MagicMock()
        fake_httpx.AsyncClient.return_value = fake_client

        # Playwright fallback mock — called by _get_chrome_cookies_playwright.
        # It must return cookies so verify_flow_profile can probe via httpx.
        mock_ap, _ = _build_verify_mock(
            cookies=[
                {"name": "SAPISID", "value": "google", "domain": ".google.com"},
                {
                    "name": "__Secure-next-auth.session-token",
                    "value": "flow-session",
                    "domain": "labs.google",
                },
            ]
        )

        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.cookies.get_cookies_path",
                return_value=Path("/fake/Cookies"),
            ),
            patch.dict(sys.modules, {"browser_cookie3": fake_bc3, "httpx": fake_httpx}),
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_profile(profile)

        # DPAPI RuntimeError must trigger the Playwright fallback, yielding AUTHENTICATED.
        assert status.outcome is FlowSessionOutcome.AUTHENTICATED
        assert status.user_email == "test.user@example.com"

    @pytest.mark.asyncio
    async def test_httpx_retryable_status_retried_then_verification_error(
        self, gflow_home: Path
    ) -> None:
        """Regression for fix #3: verify_flow_profile must retry on 429/503/504.

        The httpx fast path previously performed a single client.get() with no retry.
        A transient 503 should be retried up to _MAX_ATTEMPTS times (matching the
        Playwright path), then resolve to VERIFICATION_ERROR.
        """
        profile = gflow_home / "profile_default"
        profile.mkdir()

        fake_bc3 = MagicMock()

        class BrowserCookieError(Exception):
            pass

        fake_bc3.BrowserCookieError = BrowserCookieError
        fake_bc3.chrome.return_value = [
            SimpleNamespace(name="SAPISID", value="google", domain=".google.com"),
        ]

        # Every attempt returns 503.
        fake_resp_503 = MagicMock(status_code=503, text="{}")
        fake_client = MagicMock()
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)
        fake_client.get = AsyncMock(return_value=fake_resp_503)

        fake_httpx = MagicMock()
        fake_httpx.AsyncClient.return_value = fake_client

        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.cookies.get_cookies_path",
                return_value=Path("/fake/Cookies"),
            ),
            patch.dict(sys.modules, {"browser_cookie3": fake_bc3, "httpx": fake_httpx}),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_profile(profile)

        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        # Must have attempted exactly _MAX_ATTEMPTS (3) times, not just once.
        assert fake_client.get.await_count == 3

    @pytest.mark.asyncio
    async def test_google_session_only(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        mock_ap, _ = _build_verify_mock(
            cookies=[{"name": "SAPISID", "value": "x"}], response_body="{}"
        )
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.GOOGLE_SESSION_ONLY

    @pytest.mark.asyncio
    async def test_no_session(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        mock_ap, _ = _build_verify_mock(cookies=[], response_body="{}")
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.NO_SESSION

    @pytest.mark.asyncio
    async def test_launch_failure_is_verification_error(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        mock_ap, _ = _build_verify_mock()
        mock_ap.return_value.__aenter__.return_value.chromium.launch_persistent_context = AsyncMock(
            side_effect=RuntimeError("launch failed")
        )
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR

    @pytest.mark.asyncio
    async def test_transient_errors_exhaust_to_verification_error(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        # Every fetch attempt raises a network error -> retries exhausted.
        mock_ap, mock_ctx = _build_verify_mock(get_side_effect=[RuntimeError("net::ERR")] * 3)
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        assert mock_ctx.request.get.await_count == 3

    @pytest.mark.asyncio
    async def test_retryable_status_retried_then_verification_error(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        # HTTP 503 every time -> retried 3x, then evaluated as VERIFICATION_ERROR.
        mock_ap, mock_ctx = _build_verify_mock(response_status=503)
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        assert mock_ctx.request.get.await_count == 3

    @pytest.mark.asyncio
    async def test_mixed_transient_failures_exhaust_to_verification_error(
        self, gflow_home: Path
    ) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        # Sequence: exception -> retryable 503 -> exception.
        # All three retry branches are exercised in one pass.
        resp_503 = MagicMock(name="resp_503")
        resp_503.status = 503
        resp_503.text = AsyncMock(return_value="{}")
        mock_ap, mock_ctx = _build_verify_mock(
            get_side_effect=[RuntimeError("net::ERR"), resp_503, RuntimeError("net::ERR")]
        )
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        assert mock_ctx.request.get.await_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_status_not_retried(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        # HTTP 401 -> not retried; one attempt, then VERIFICATION_ERROR.
        mock_ap, mock_ctx = _build_verify_mock(response_status=401)
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        assert mock_ctx.request.get.await_count == 1

    @pytest.mark.asyncio
    async def test_ctx_closed_on_error_path(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        mock_ap, mock_ctx = _build_verify_mock(get_side_effect=[RuntimeError("net::ERR")] * 3)
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            await verify_flow_session(profile, source="chrome")
        mock_ctx.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_profile_outside_home_raises_security_error(self, gflow_home: Path) -> None:
        outside = gflow_home.parent / "outside_profile"
        outside.mkdir()
        with patch("gflow_cli.auth.verification.get_settings") as mock_settings:
            mock_settings.return_value.home = gflow_home
            with pytest.raises(SecurityError):
                await verify_flow_session(outside, source="chrome")


# ---------------------------------------------------------------------------
# #477: profile-engine downgrade guard in the headless probe
# ---------------------------------------------------------------------------


class TestVerifyFlowSessionEngineDowngrade:
    @pytest.mark.asyncio
    async def test_probe_refuses_downgrade_without_launching(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#477: a profile last written by a newer Chromium maps to
        VERIFICATION_ERROR (fail-closed contract) and the persistent context is
        never launched — the probe must not trigger downgrade cleanup either."""
        import gflow_cli.browser_manager as bm

        gflow_home = tmp_path / "gflow_home"
        gflow_home.mkdir()
        profile = gflow_home / "profile_default"
        profile.mkdir()
        (profile / "Last Version").write_text("999.0.0.0", encoding="utf-8")
        monkeypatch.setattr(bm, "installed_chromium_version", lambda: "149.0.7827.55")

        mock_ap, mock_ctx = _build_verify_mock()
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, channel=None, source="internal")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        mock_ap.return_value.__aenter__.assert_not_awaited()
        mock_ctx.cookies.assert_not_awaited()
