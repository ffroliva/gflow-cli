from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.dto import CreditsInfo
from gflow_cli.errors import AisandboxAuthError, FlowApiError, SecurityError
from gflow_cli.profile_store import ProfileMeta


class _FakeClient:
    responses: dict[str, CreditsInfo | Exception] = {}

    def __init__(self, profile_dir: Path, *, headless: bool = False) -> None:
        self.profile_dir = profile_dir
        self.headless = headless

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get_credits(self) -> CreditsInfo:
        result = self.responses[self.profile_dir.name]
        if isinstance(result, Exception):
            raise result
        return result


def _meta(name: str, *, default: bool = False) -> ProfileMeta:
    return ProfileMeta(
        name=name,
        profile_dir=Path(f"/profiles/{name}"),
        cookies_present=True,
        last_used_at=None,
        is_default=default,
        google_account=f"{name}@example.com",
    )


async def test_inspect_profile_returns_stable_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.services import credits

    _FakeClient.responses = {"one": CreditsInfo(credits=5, sku="G1_FREEMIUM")}
    monkeypatch.setattr(credits, "FlowApiClient", _FakeClient)

    async def fast(profile_dir: Path) -> CreditsInfo:
        assert profile_dir.name == "one"
        return CreditsInfo(credits=5, sku="G1_FREEMIUM")

    monkeypatch.setattr(credits, "fetch_credits_http", fast)
    monkeypatch.setattr(credits.profile_store, "resolve_profile", lambda value: "one")
    monkeypatch.setattr(
        credits.profile_store, "list_profiles", lambda: [_meta("one", default=True)]
    )

    result = await credits.inspect_profile(None)

    assert result == {
        "status": "ok",
        "profile": "one",
        "is_default": True,
        "email": "one@example.com",
        "authenticated": True,
        "credits": 5,
        "subscription_credits": None,
        "user_paygate_tier": None,
        "service_tier": None,
        "sku": "G1_FREEMIUM",
    }


async def test_inspect_all_preserves_success_when_one_profile_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.services import credits

    _FakeClient.responses = {"one": CreditsInfo(credits=5), "two": RuntimeError("secret text")}
    monkeypatch.setattr(credits, "FlowApiClient", _FakeClient)

    async def fast(profile_dir: Path) -> CreditsInfo:
        if profile_dir.name == "one":
            return CreditsInfo(credits=5)
        raise RuntimeError("fast path secret")

    monkeypatch.setattr(credits, "fetch_credits_http", fast)
    monkeypatch.setattr(
        credits.profile_store, "list_profiles", lambda: [_meta("one"), _meta("two")]
    )

    result = await credits.inspect_all_profiles()

    assert result["status"] == "partial"
    assert result["total_credits"] == 5
    assert result["count"] == 2
    assert result["profiles"][0]["authenticated"] is True
    assert result["profiles"][1]["authenticated"] is False
    assert result["profiles"][1]["error"] == "Unexpected RuntimeError"
    assert "secret text" not in str(result)


async def test_browser_client_is_fallback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.services import credits

    async def fast(profile_dir: Path) -> CreditsInfo:
        raise PermissionError("cookie decryption failed")

    _FakeClient.responses = {"one": CreditsInfo(credits=9)}
    monkeypatch.setattr(credits, "fetch_credits_http", fast)
    monkeypatch.setattr(credits, "FlowApiClient", _FakeClient)
    monkeypatch.setattr(credits.profile_store, "resolve_profile", lambda value: "one")
    monkeypatch.setattr(credits.profile_store, "list_profiles", lambda: [_meta("one")])

    result = await credits.inspect_profile(None)

    assert result["credits"] == 9


async def test_security_error_never_falls_back_to_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.services import credits

    async def fast(profile_dir: Path) -> CreditsInfo:
        raise SecurityError("outside home")

    monkeypatch.setattr(credits, "fetch_credits_http", fast)
    monkeypatch.setattr(credits, "FlowApiClient", _FakeClient)
    monkeypatch.setattr(credits.profile_store, "resolve_profile", lambda value: "one")
    monkeypatch.setattr(credits.profile_store, "list_profiles", lambda: [_meta("one")])

    with pytest.raises(SecurityError):
        await credits.inspect_profile(None)

    monkeypatch.setattr(credits.profile_store, "list_profiles", lambda: [_meta("one")])
    with pytest.raises(SecurityError):
        await credits.inspect_all_profiles()


async def test_fallback_log_includes_safe_gflow_error_metadata_only(
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture,
) -> None:
    # Uses a transport failure, not an auth verdict: since #795 an AisandboxAuthError
    # is re-raised rather than retried through the browser, so it never reaches this
    # log line. The redaction contract this test pins is unchanged.
    from gflow_cli.services import credits

    async def fast(profile_dir: Path) -> CreditsInfo:
        raise FlowApiError(
            detail="secret response body",
            status=503,
            route="credits",
        )

    _FakeClient.responses = {"one": CreditsInfo(credits=9)}
    monkeypatch.setattr(credits, "fetch_credits_http", fast)
    monkeypatch.setattr(credits, "FlowApiClient", _FakeClient)
    monkeypatch.setattr(credits.profile_store, "resolve_profile", lambda value: "one")
    monkeypatch.setattr(credits.profile_store, "list_profiles", lambda: [_meta("one")])

    result = await credits.inspect_profile(None)

    assert result["credits"] == 9
    event = install_log_capture.entries[0]
    assert event["event"] == "credits.http_fallback_to_browser"
    assert event["error_type"] == "FlowApiError"
    assert event["status_code"] == 503
    assert event["route"] == "credits"
    assert "secret response body" not in str(event)


def test_failure_uses_gflow_error_title_and_all_dto_fields() -> None:
    from dataclasses import fields

    from gflow_cli.services import credits

    error = AisandboxAuthError(status=401, route="credits")

    result = credits._failure(_meta("one"), error)

    assert result["error"] == error.title
    assert result["error_type"] == "AisandboxAuthError"
    assert all(result[field.name] is None for field in fields(CreditsInfo))


def _never_launches(*args: object, **kwargs: object) -> None:
    """Stand in for FlowApiClient and fail the test if it is ever constructed.

    `pytest.fail`, not `raise AssertionError`: `inspect_all_profiles` catches
    `Exception` per profile to preserve partial results, so an AssertionError here
    is swallowed into a recorded failure and the guard silently stops guarding.
    `Failed` derives from BaseException, so it propagates through that handler.
    """
    pytest.fail("the browser fallback must not run on an auth verdict (#795)")


def _install_auth_verdict(monkeypatch: pytest.MonkeyPatch, profiles: list[ProfileMeta]) -> None:
    from gflow_cli.services import credits

    async def fast(profile_dir: Path) -> CreditsInfo:
        raise AisandboxAuthError(
            detail="credits endpoint returned 401",
            status=401,
            route="credits",  # aisandbox-pa answered — the browser cannot overturn it
            remediation_hint="the accurate one",
        )

    monkeypatch.setattr(credits, "fetch_credits_http", fast)
    monkeypatch.setattr(credits, "FlowApiClient", _never_launches)
    monkeypatch.setattr(credits.profile_store, "resolve_profile", lambda value: profiles[0].name)
    monkeypatch.setattr(credits.profile_store, "list_profiles", lambda: profiles)


async def test_an_auth_verdict_is_never_re_derived_through_the_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#795: the browser fallback re-asks the SAME server with the SAME credentials.
    The fast path's cookie read already falls back to Chrome on its own
    (`auth/cookies.py::get_chrome_cookie_snapshot`), so the browser holds no
    credential the fast path lacked and cannot reach a different verdict — it just
    costs a Chrome launch and then raises from the SHARED aisandbox retry helper
    (`api/client.py`), whose class-default remediation blames SAPISID and says to
    re-login. That discarded an accurate diagnosis for a harmful one."""
    from gflow_cli.services import credits

    _install_auth_verdict(monkeypatch, [_meta("one")])

    with pytest.raises(AisandboxAuthError) as caught:
        await credits.inspect_profile(None)

    assert caught.value.remediation_hint == "the accurate one"


async def test_an_auth_verdict_is_a_recorded_failure_not_a_browser_launch_per_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`credits list` still preserves partial results — it just no longer pays a
    Chrome launch per profile to re-derive an answer the server already gave."""
    from gflow_cli.services import credits

    _install_auth_verdict(monkeypatch, [_meta("one"), _meta("two")])

    result = await credits.inspect_all_profiles()

    assert result["status"] == "partial"
    assert [item["error_type"] for item in result["profiles"]] == [
        "AisandboxAuthError",
        "AisandboxAuthError",
    ]


async def test_a_tokenless_verdict_still_gets_its_browser_rescue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#795: only the aisandbox refusal is proven unreachable by the browser.

    `labs answered 200 with no token` is a different claim: httpx sends labs.google
    cookies only, while the browser carries the full jar and bootstraps a real
    navigation, which can renew a session httpx cannot. That rescue is kept — assuming
    it away would trade one silent wrong answer for another.
    """
    from gflow_cli.services import credits

    async def fast(profile_dir: Path) -> CreditsInfo:
        raise AisandboxAuthError(
            detail="the labs.google session returned no access token",
            status=200,
            route="auth/session",
        )

    _FakeClient.responses = {"one": CreditsInfo(credits=9)}
    monkeypatch.setattr(credits, "fetch_credits_http", fast)
    monkeypatch.setattr(credits, "FlowApiClient", _FakeClient)
    monkeypatch.setattr(credits.profile_store, "resolve_profile", lambda value: "one")
    monkeypatch.setattr(credits.profile_store, "list_profiles", lambda: [_meta("one")])

    assert (await credits.inspect_profile(None))["credits"] == 9


async def test_a_failed_rescue_reports_the_fast_paths_diagnosis_not_the_browsers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the rescue fails too, the accurate verdict must survive it.

    The browser's own error comes from the route-blind shared retry helper and carries
    the class default; the fast path knew which endpoint refused and why.
    """
    from gflow_cli.services import credits

    async def fast(profile_dir: Path) -> CreditsInfo:
        raise AisandboxAuthError(
            detail="the labs.google session returned no access token",
            status=200,
            route="auth/session",
            remediation_hint="the accurate one",
        )

    _FakeClient.responses = {
        "one": AisandboxAuthError(
            detail="aisandbox-pa returned 401 after token refresh",
            status=401,
            route="credits",
        )
    }
    monkeypatch.setattr(credits, "fetch_credits_http", fast)
    monkeypatch.setattr(credits, "FlowApiClient", _FakeClient)
    monkeypatch.setattr(credits.profile_store, "resolve_profile", lambda value: "one")
    monkeypatch.setattr(credits.profile_store, "list_profiles", lambda: [_meta("one")])

    with pytest.raises(AisandboxAuthError) as caught:
        await credits.inspect_profile(None)

    assert caught.value.remediation_hint == "the accurate one"
    assert "no access token" in caught.value.detail


def test_a_recorded_failure_carries_the_remediation_not_just_the_title() -> None:
    """#795: `credits list` renders `error` (the generic class title), so without the
    hint the multi-profile surface is the one place that still cannot say why."""
    from gflow_cli.services import credits

    result = credits._failure(  # pyright: ignore[reportPrivateUsage]
        _meta("one"),
        AisandboxAuthError(status=401, route="credits", remediation_hint="do not re-login"),
    )

    assert result["error"] == "aisandbox-pa authentication failed"
    assert result["remediation_hint"] == "do not re-login"


def test_a_recorded_failure_has_a_remediation_key_even_for_an_unexpected_error() -> None:
    """The key is part of the shape both doors read — never conditionally absent."""
    from gflow_cli.services import credits

    result = credits._failure(_meta("one"), RuntimeError("boom"))  # pyright: ignore[reportPrivateUsage]

    assert result["remediation_hint"] is None


# --- --refresh: renew an expired session, then re-ask the fast path ----------------
#
# #802 stopped the browser fallback from re-deriving an aisandbox-pa verdict: it asked
# the same endpoint with the same Bearer. A renewal is a different act — it loads Flow
# so Google and NextAuth rotate the cookies on disk (`auth/renew.py`), so the retried
# fast path reads a NEW session. It costs a headless Chrome launch, so it is opt-in.


def _verdict(outcome: str) -> object:
    from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus

    return FlowSessionStatus(
        outcome=FlowSessionOutcome(outcome), user_email=None, source="credits_refresh"
    )


def _install_expiring_fast_path(
    monkeypatch: pytest.MonkeyPatch,
    profiles: list[ProfileMeta],
    *,
    renewed: str = "authenticated",
) -> list[str]:
    """Fast path refuses until the profile is renewed. Returns the renew-call log."""
    from gflow_cli.services import credits

    renewals: list[str] = []

    async def fast(profile_dir: Path) -> CreditsInfo:
        if profile_dir.name in renewals and renewed == "authenticated":
            return CreditsInfo(credits=7, sku="G1_FREEMIUM")
        raise AisandboxAuthError(status=401, route="credits", remediation_hint="the accurate one")

    async def renew(profile_dir: Path, *, source: str = "renew") -> object:
        assert source == "credits_refresh"
        renewals.append(profile_dir.name)
        return _verdict(renewed)

    monkeypatch.setattr(credits, "fetch_credits_http", fast)
    monkeypatch.setattr(credits, "renew_profile", renew)
    monkeypatch.setattr(credits, "FlowApiClient", _never_launches)
    monkeypatch.setattr(credits.profile_store, "resolve_profile", lambda value: profiles[0].name)
    monkeypatch.setattr(credits.profile_store, "list_profiles", lambda: profiles)
    return renewals


async def test_refresh_renews_an_expired_session_and_retries_the_fast_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.services import credits

    renewals = _install_expiring_fast_path(monkeypatch, [_meta("one")])

    result = await credits.inspect_profile(None, refresh=True)

    assert renewals == ["one"]
    assert result["authenticated"] is True
    assert result["credits"] == 7


async def test_without_refresh_an_expired_session_is_never_renewed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default stays #802's: no browser once aisandbox-pa has answered."""
    from gflow_cli.services import credits

    renewals = _install_expiring_fast_path(monkeypatch, [_meta("one")])

    with pytest.raises(AisandboxAuthError):
        await credits.inspect_profile(None)

    assert renewals == []


async def test_a_failed_renewal_reports_the_fast_paths_diagnosis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revoked session cannot be renewed; the user still needs the accurate hint,
    and the fast path is not re-asked a question whose answer cannot have changed."""
    from gflow_cli.services import credits

    renewals = _install_expiring_fast_path(
        monkeypatch, [_meta("one")], renewed="google_session_only"
    )

    with pytest.raises(AisandboxAuthError) as caught:
        await credits.inspect_profile(None, refresh=True)

    assert renewals == ["one"]
    assert caught.value.remediation_hint == "the accurate one"


async def test_refresh_does_not_renew_a_non_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renewal fixes stale cookies, nothing else. A malformed reply is not stale cookies."""
    from gflow_cli.services import credits

    renewals: list[str] = []

    async def fast(profile_dir: Path) -> CreditsInfo:
        raise SecurityError(detail="refused")

    async def renew(profile_dir: Path, *, source: str = "renew") -> object:
        renewals.append(profile_dir.name)
        return _verdict("authenticated")

    monkeypatch.setattr(credits, "fetch_credits_http", fast)
    monkeypatch.setattr(credits, "renew_profile", renew)
    monkeypatch.setattr(credits.profile_store, "resolve_profile", lambda value: "one")
    monkeypatch.setattr(credits.profile_store, "list_profiles", lambda: [_meta("one")])

    with pytest.raises(SecurityError):
        await credits.inspect_profile(None, refresh=True)

    assert renewals == []


async def test_refresh_list_renews_only_the_profiles_that_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.services import credits

    renewals = _install_expiring_fast_path(monkeypatch, [_meta("one"), _meta("two")])
    real_fast = credits.fetch_credits_http

    async def fast(profile_dir: Path) -> CreditsInfo:
        if profile_dir.name == "two":
            return CreditsInfo(credits=3, sku="G1_FREEMIUM")
        return await real_fast(profile_dir)

    monkeypatch.setattr(credits, "fetch_credits_http", fast)

    result = await credits.inspect_all_profiles(refresh=True)

    assert renewals == ["one"]
    assert result["status"] == "ok"
    assert result["total_credits"] == 10


async def test_refresh_list_records_a_profile_in_use_and_carries_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.errors import ProfileLockedError
    from gflow_cli.services import credits

    _install_expiring_fast_path(monkeypatch, [_meta("one"), _meta("two")])
    calls: list[str] = []

    async def renew(profile_dir: Path, *, source: str = "renew") -> object:
        calls.append(profile_dir.name)
        if profile_dir.name == "one":
            raise ProfileLockedError(detail="held")
        return _verdict("google_session_only")

    monkeypatch.setattr(credits, "renew_profile", renew)

    result = await credits.inspect_all_profiles(refresh=True)

    assert calls == ["one", "two"]
    assert [item["error_type"] for item in result["profiles"]] == [
        "ProfileLockedError",
        "AisandboxAuthError",
    ]
