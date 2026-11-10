from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.dto import CreditsInfo
from gflow_cli.errors import AisandboxAuthError, AuthExpiredError, SecurityError
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


def _install_reader(monkeypatch: pytest.MonkeyPatch, read: Any) -> None:
    """Stand in for the browser read (`FlowApiClient.get_credits`, nzlxg since #795)."""
    from gflow_cli.services import credits

    class _Reader(_FakeClient):
        async def get_credits(self) -> CreditsInfo:
            return await read(self.profile_dir)

    _Reader.read = staticmethod(read)  # type: ignore[attr-defined]
    monkeypatch.setattr(credits, "FlowApiClient", _Reader)


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

    async def fast(profile_dir: Path) -> CreditsInfo:
        assert profile_dir.name == "one"
        return CreditsInfo(credits=5, sku="G1_FREEMIUM")

    _install_reader(monkeypatch, fast)
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

    async def fast(profile_dir: Path) -> CreditsInfo:
        if profile_dir.name == "one":
            return CreditsInfo(credits=5)
        raise RuntimeError("secret text")

    _install_reader(monkeypatch, fast)
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


def test_failure_uses_gflow_error_title_and_all_dto_fields() -> None:
    from dataclasses import fields

    from gflow_cli.services import credits

    error = AisandboxAuthError(status=401, route="credits")

    result = credits._failure(_meta("one"), error)

    assert result["error"] == error.title
    assert result["error_type"] == "AisandboxAuthError"
    assert all(result[field.name] is None for field in fields(CreditsInfo))


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


# --- --refresh: renew a signed-out session, then read again ----------------------
#
# A renewal loads Flow so Google rotates the cookies on disk (`auth/renew.py`), so the
# retried read sees a NEW session. It costs a headless Chrome launch, so it is opt-in.


def _verdict(
    outcome: str, *, landing: str | None = None, labs_stale: bool | None = False
) -> object:
    from gflow_cli.auth.renew import RenewResult
    from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus

    verdict = FlowSessionStatus(
        outcome=FlowSessionOutcome(outcome), user_email=None, source="credits_refresh"
    )
    return RenewResult(verdict=verdict, landing=landing, labs_stale=labs_stale)


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
        raise AuthExpiredError(detail="signed out", remediation_hint="the accurate one")

    async def renew(profile_dir: Path, *, source: str = "renew") -> object:
        assert source == "credits_refresh"
        renewals.append(profile_dir.name)
        return _verdict(renewed)

    _install_reader(monkeypatch, fast)
    monkeypatch.setattr(credits, "renew_profile", renew)
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

    with pytest.raises(AuthExpiredError):
        await credits.inspect_profile(None)

    assert renewals == []


async def test_a_failed_renewal_reports_the_fast_paths_diagnosis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revoked session cannot be renewed; the user still needs the accurate hint,
    and the read is not re-asked a question whose answer cannot have changed."""
    from gflow_cli.services import credits

    renewals = _install_expiring_fast_path(
        monkeypatch, [_meta("one")], renewed="google_session_only"
    )

    with pytest.raises(AuthExpiredError) as caught:
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

    _install_reader(monkeypatch, fast)
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
    real_fast = credits.FlowApiClient.read  # type: ignore[attr-defined]

    async def fast(profile_dir: Path) -> CreditsInfo:
        if profile_dir.name == "two":
            return CreditsInfo(credits=3, sku="G1_FREEMIUM")
        return await real_fast(profile_dir)

    _install_reader(monkeypatch, fast)

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
        "AuthExpiredError",
    ]


async def test_a_renewal_that_cannot_launch_keeps_the_fast_paths_diagnosis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user's own Chrome holds the profile: the browser never starts. That must
    not replace an accurate AisandboxAuthError with an UnexpectedError."""
    from playwright.async_api import Error as PlaywrightError

    from gflow_cli.services import credits

    _install_expiring_fast_path(monkeypatch, [_meta("one")])

    async def renew(profile_dir: Path, *, source: str = "renew") -> object:
        raise PlaywrightError("ProcessSingleton: profile in use")

    monkeypatch.setattr(credits, "renew_profile", renew)

    with pytest.raises(AuthExpiredError) as caught:
        await credits.inspect_profile(None, refresh=True)

    assert caught.value.remediation_hint == "the accurate one"
    assert isinstance(caught.value.__cause__, PlaywrightError)


@pytest.mark.parametrize(("landing", "labs_stale"), [("public", False), (None, True)])
async def test_an_about_account_is_not_re_asked(
    monkeypatch: pytest.MonkeyPatch, landing: str | None, labs_stale: bool
) -> None:
    """The verifier says AUTHENTICATED on an /about account; renewal did not fix it."""
    from gflow_cli.services import credits

    renewals = _install_expiring_fast_path(monkeypatch, [_meta("one")])
    asked: list[str] = []
    real_fast = credits.FlowApiClient.read  # type: ignore[attr-defined]

    async def fast(profile_dir: Path) -> CreditsInfo:
        asked.append(profile_dir.name)
        return await real_fast(profile_dir)

    async def renew(profile_dir: Path, *, source: str = "renew") -> object:
        renewals.append(profile_dir.name)
        return _verdict("authenticated", landing=landing, labs_stale=labs_stale)

    _install_reader(monkeypatch, fast)
    monkeypatch.setattr(credits, "renew_profile", renew)

    with pytest.raises(AuthExpiredError):
        await credits.inspect_profile(None, refresh=True)

    assert asked == ["one"]


async def test_an_unverified_labs_state_is_still_re_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`labs_unverified` keeps `auth renew` from claiming success, but the re-ask is one
    self-checking HTTP call: if the Flow visit's rotated cookies did the job, use it."""
    from gflow_cli.services import credits

    renewals = _install_expiring_fast_path(monkeypatch, [_meta("one")])

    async def renew(profile_dir: Path, *, source: str = "renew") -> object:
        renewals.append(profile_dir.name)
        return _verdict("authenticated", labs_stale=None)

    monkeypatch.setattr(credits, "renew_profile", renew)

    info = await credits.inspect_profile(None, refresh=True)

    assert info["credits"] == 7


@pytest.mark.parametrize(
    ("labs_stale", "verdict", "lifted"),
    [
        (False, "authenticated", True),  # renewed
        (None, "authenticated", True),  # labs_unverified: Flow itself verified the session
        (True, "authenticated", False),  # labs_stale: not re-asked at all
    ],
)
async def test_a_flow_verified_renewal_lifts_the_wire_signed_out_mark(
    monkeypatch: pytest.MonkeyPatch, labs_stale: bool | None, verdict: str, lifted: bool
) -> None:
    """The direct read only needs flow.google.com's cookies, so a renewal Flow verified gets
    the wire one more try whatever labs answered (labs answers 410 to everyone now)."""
    from gflow_cli.services import credits

    _install_expiring_fast_path(monkeypatch, [_meta("one")])
    recovered: list[str] = []

    async def renew(profile_dir: Path, *, source: str = "renew") -> object:
        return _verdict(verdict, labs_stale=labs_stale)

    monkeypatch.setattr(credits, "renew_profile", renew)
    monkeypatch.setattr(credits.flow_wire, "session_recovered", lambda p: recovered.append(p.name))

    with contextlib.suppress(Exception):
        await credits.inspect_profile(None, refresh=True)

    assert recovered == (["one"] if lifted else [])
