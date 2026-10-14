"""`renew_profile` — headless session refresh (`gflow auth renew`).

Network- and browser-free: Playwright and the session oracle are mocked. What is
pinned here is the order that makes the refresh safe and honest — lease, then
the #477 guard, then a launch with generation's flags, a Flow visit, a context
close (which flushes rotated cookies), and only then the verdict.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import Error as PlaywrightError

from gflow_cli.auth import renew
from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus
from gflow_cli.errors import ProfileEngineDowngradeError

_FRESH = '{"access_token": "t", "expires": "2026-10-15T00:00:00Z", "user": {}}'
_STALE = (
    '{"access_token": "t", "error": "ACCESS_TOKEN_REFRESH_NEEDED",'
    ' "expires": "2026-10-14T00:00:00Z", "user": {}}'
)


def _playwright_mock(
    *,
    goto_error: Exception | None = None,
    session_error: Exception | None = None,
    session_bodies: list[str] | None = None,
) -> tuple[MagicMock, MagicMock, list[str]]:
    events: list[str] = []
    page = MagicMock(name="page")
    page.url = "https://labs.google/fx/tools/flow"
    page.goto = AsyncMock(side_effect=goto_error, return_value=None)
    page.wait_for_load_state = AsyncMock(side_effect=PlaywrightError("never idle"))

    async def _session_get(url: str, **_kwargs: object) -> MagicMock:
        events.append("labs_session")
        if session_error is not None:
            raise session_error
        bodies = session_bodies or [_FRESH]
        body = bodies.pop(0) if len(bodies) > 1 else bodies[0]
        return MagicMock(status=200, text=AsyncMock(return_value=body))

    page.request.get = AsyncMock(side_effect=_session_get)

    ctx = MagicMock(name="ctx")
    ctx.pages = [page]
    ctx.add_init_script = AsyncMock()

    async def _close() -> None:
        events.append("close")

    ctx.close = AsyncMock(side_effect=_close)

    async def _launch(**_kwargs: object) -> MagicMock:
        events.append("launch")
        return ctx

    pw = MagicMock(name="pw")
    pw.chromium.launch_persistent_context = AsyncMock(side_effect=_launch)
    cm = MagicMock(name="cm")
    cm.__aenter__ = AsyncMock(return_value=pw)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(name="async_playwright", return_value=cm), page, events


def _patch_verdict(
    monkeypatch: pytest.MonkeyPatch, events: list[str], outcome: FlowSessionOutcome
) -> None:
    async def _verify(profile_dir: Path, *, source: str = "chrome") -> FlowSessionStatus:
        events.append("verify")
        return FlowSessionStatus(outcome=outcome, user_email="user@example.com", source=source)

    monkeypatch.setattr(renew, "verify_flow_profile", _verify)


@pytest.fixture(autouse=True)
def _bundled_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gflow_cli.browser_manager.channel_for_profile", lambda _p: None)
    monkeypatch.setattr(
        "gflow_cli.browser_manager.ensure_profile_engine_compatible", lambda _p, _c: None
    )


@pytest.mark.asyncio
async def test_visits_flow_closes_then_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_ap, page, events = _playwright_mock()
    monkeypatch.setattr("gflow_cli.auth.strategies.async_playwright", mock_ap)
    _patch_verdict(monkeypatch, events, FlowSessionOutcome.AUTHENTICATED)

    verdict = await renew.renew_profile(tmp_path)

    assert verdict.authenticated
    # Verifying before the close would read the pre-rotation cookie store.
    assert events == ["launch", "labs_session", "close", "verify"]
    assert "labs.google/fx/tools/flow" in page.goto.await_args_list[0].args[0]


@pytest.mark.asyncio
async def test_asks_labs_nextauth_for_the_session_from_inside_the_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured live 2026-10-14: `labs.google/fx/tools/flow` 308s to flow.google.com,
    so the Flow visit alone never runs labs' NextAuth. Eight stale profiles renewed
    that way all verified, and aisandbox-pa still refused every one of their Bearers.
    The credits fast path reads the labs session cookie; only a request to labs'
    session endpoint from the browser's full jar makes NextAuth rotate it."""
    mock_ap, page, events = _playwright_mock()
    monkeypatch.setattr("gflow_cli.auth.strategies.async_playwright", mock_ap)
    _patch_verdict(monkeypatch, events, FlowSessionOutcome.AUTHENTICATED)

    await renew.renew_profile(tmp_path)

    visited = [call.args[0] for call in page.goto.await_args_list]
    assert visited[-1] == "https://labs.google/fx/"
    assert page.request.get.await_args.args[0] == "https://labs.google/fx/api/auth/session"


@pytest.mark.asyncio
async def test_a_failed_labs_session_request_still_returns_the_oracle_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_ap, _page, events = _playwright_mock(session_error=PlaywrightError("net::ERR"))
    monkeypatch.setattr("gflow_cli.auth.strategies.async_playwright", mock_ap)
    _patch_verdict(monkeypatch, events, FlowSessionOutcome.AUTHENTICATED)

    verdict = await renew.renew_profile(tmp_path)

    assert verdict.authenticated
    assert events == ["launch", "labs_session", "close", "verify"]


@pytest.mark.asyncio
async def test_launches_headless_with_generation_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_ap, _page, events = _playwright_mock()
    monkeypatch.setattr("gflow_cli.auth.strategies.async_playwright", mock_ap)
    _patch_verdict(monkeypatch, events, FlowSessionOutcome.AUTHENTICATED)

    await renew.renew_profile(tmp_path)

    pw = mock_ap.return_value.__aenter__.return_value
    kwargs = pw.chromium.launch_persistent_context.await_args.kwargs
    assert kwargs["user_data_dir"] == str(tmp_path)
    assert kwargs["headless"] is True
    assert "--password-store=basic" in kwargs["args"]
    assert "--enable-automation" in kwargs["ignore_default_args"]


@pytest.mark.asyncio
async def test_navigation_failure_still_returns_the_oracle_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_ap, _page, events = _playwright_mock(goto_error=PlaywrightError("net::ERR"))
    monkeypatch.setattr("gflow_cli.auth.strategies.async_playwright", mock_ap)
    _patch_verdict(monkeypatch, events, FlowSessionOutcome.VERIFICATION_ERROR)

    verdict = await renew.renew_profile(tmp_path)

    assert verdict.outcome is FlowSessionOutcome.VERIFICATION_ERROR
    # Every goto fails here, so the labs session request is never reached either.
    assert events == ["launch", "close", "verify"]


@pytest.mark.asyncio
async def test_engine_downgrade_refuses_before_any_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_ap, _page, events = _playwright_mock()
    monkeypatch.setattr("gflow_cli.auth.strategies.async_playwright", mock_ap)
    _patch_verdict(monkeypatch, events, FlowSessionOutcome.AUTHENTICATED)

    def _refuse(_p: Path, _c: str | None) -> None:
        raise ProfileEngineDowngradeError("newer profile")

    monkeypatch.setattr("gflow_cli.browser_manager.ensure_profile_engine_compatible", _refuse)

    with pytest.raises(ProfileEngineDowngradeError):
        await renew.renew_profile(tmp_path)

    mock_ap.assert_not_called()
    assert events == []


@pytest.mark.asyncio
async def test_waits_for_labs_to_finish_its_silent_token_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured live 2026-10-14 (sykamel): a stale labs session answers 200 with an
    expired token and `ACCESS_TOKEN_REFRESH_NEEDED`. The labs app's own JS then signs
    in again silently (signin/google -> OAuth 302 -> callback) ~2.5 s after load.
    Closing the browser before that lands saves nothing, so renewal polls until the
    error clears."""
    mock_ap, _page, events = _playwright_mock(session_bodies=[_STALE, _STALE, _FRESH])
    monkeypatch.setattr("gflow_cli.auth.strategies.async_playwright", mock_ap)
    monkeypatch.setattr(renew, "_LABS_POLL_INTERVAL_S", 0)
    _patch_verdict(monkeypatch, events, FlowSessionOutcome.AUTHENTICATED)

    await renew.renew_profile(tmp_path)

    assert events == ["launch", "labs_session", "labs_session", "labs_session", "close", "verify"]


@pytest.mark.asyncio
async def test_a_labs_refresh_that_never_lands_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_ap, _page, events = _playwright_mock(session_bodies=[_STALE])
    monkeypatch.setattr("gflow_cli.auth.strategies.async_playwright", mock_ap)
    monkeypatch.setattr(renew, "_LABS_POLL_INTERVAL_S", 0)
    monkeypatch.setattr(renew, "_LABS_REFRESH_TIMEOUT_S", 0)
    _patch_verdict(monkeypatch, events, FlowSessionOutcome.AUTHENTICATED)

    await renew.renew_profile(tmp_path)

    assert events == ["launch", "labs_session", "close", "verify"]
