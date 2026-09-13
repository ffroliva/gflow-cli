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


class _NeverLaunches:
    """A FlowApiClient stand-in that fails the test if it is ever constructed."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        msg = "the browser fallback must not run on an auth verdict (#795)"
        raise AssertionError(msg)


def _install_auth_verdict(monkeypatch: pytest.MonkeyPatch, profiles: list[ProfileMeta]) -> None:
    from gflow_cli.services import credits

    async def fast(profile_dir: Path) -> CreditsInfo:
        raise AisandboxAuthError(
            detail="credits endpoint returned 401",
            status=401,
            route="credits",
            remediation_hint="the accurate one",
        )

    monkeypatch.setattr(credits, "fetch_credits_http", fast)
    monkeypatch.setattr(credits, "FlowApiClient", _NeverLaunches)
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
