from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.dto import CreditsInfo
from gflow_cli.errors import AisandboxAuthError, SecurityError
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
    from gflow_cli.services import credits

    async def fast(profile_dir: Path) -> CreditsInfo:
        raise AisandboxAuthError(
            detail="secret response body",
            status=403,
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
    assert event["error_type"] == "AisandboxAuthError"
    assert event["status_code"] == 403
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
