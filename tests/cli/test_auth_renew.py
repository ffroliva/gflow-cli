"""`gflow auth renew` — headless session refresh, one profile or all.

`renew_profile` is mocked (browser-free); its own behavior is covered by
tests/auth/test_renew.py. Pinned here: which profiles are visited, how each
verdict is reported, and the exit code a cron job branches on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli.auth.renew import RenewResult
from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus
from gflow_cli.cli import main
from gflow_cli.config import get_settings
from gflow_cli.errors import ProfileEngineDowngradeError, ProfileLockedError


def _make_profile(name: str) -> Path:
    pdir = get_settings().profile_subdir(name)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "Cookies").write_bytes(b"")
    return pdir


def _mock_renew(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: dict[str, FlowSessionOutcome | Exception],
    *,
    landing: str | None = None,
    labs_stale: bool | None = False,
) -> list[str]:
    visited: list[str] = []

    async def fake_renew(profile_dir: Path, *, source: str = "renew") -> RenewResult:
        name = profile_dir.name.removeprefix("profile_")
        visited.append(name)
        outcome = outcomes[name]
        if isinstance(outcome, Exception):
            raise outcome
        verdict = FlowSessionStatus(
            outcome=outcome, user_email=f"{name}@example.com", source=source
        )
        return RenewResult(verdict=verdict, landing=landing, labs_stale=labs_stale)

    monkeypatch.setattr("gflow_cli.auth.renew.renew_profile", fake_renew)
    return visited


def test_single_profile_renewed_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_profile("alice")
    visited = _mock_renew(monkeypatch, {"alice": FlowSessionOutcome.AUTHENTICATED})

    result = CliRunner().invoke(main, ["auth", "renew", "--profile", "alice"])

    assert result.exit_code == 0, result.output
    assert visited == ["alice"]
    assert "alice@example.com" in result.output


def test_dead_session_exits_1_and_points_at_login(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_profile("alice")
    _mock_renew(monkeypatch, {"alice": FlowSessionOutcome.GOOGLE_SESSION_ONLY})

    result = CliRunner().invoke(main, ["auth", "renew", "--profile", "alice"])

    assert result.exit_code == 1
    assert "gflow auth login --profile alice" in result.output


def test_verification_error_points_at_network_not_login(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_profile("alice")
    _mock_renew(monkeypatch, {"alice": FlowSessionOutcome.VERIFICATION_ERROR})

    result = CliRunner().invoke(main, ["auth", "renew", "--profile", "alice"])

    assert result.exit_code == 1
    assert "connectivity" in result.output
    assert "gflow auth login" not in result.output


def test_all_visits_every_profile_with_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_profile("alice")
    _make_profile("bob")
    get_settings().profile_subdir("empty").mkdir(parents=True, exist_ok=True)
    visited = _mock_renew(
        monkeypatch,
        {"alice": FlowSessionOutcome.AUTHENTICATED, "bob": FlowSessionOutcome.NO_SESSION},
    )

    result = CliRunner().invoke(main, ["auth", "renew", "--all", "--json"])

    assert result.exit_code == 1  # bob failed
    assert visited == ["alice", "bob"]  # a profile without cookies is not opened
    rows = {r["profile"]: r["result"] for r in json.loads(result.output)}
    assert rows == {"alice": "authenticated", "bob": "no_session"}


def test_profile_in_use_is_skipped_not_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_profile("alice")
    _make_profile("bob")
    _mock_renew(
        monkeypatch,
        {"alice": ProfileLockedError("held"), "bob": FlowSessionOutcome.AUTHENTICATED},
    )

    result = CliRunner().invoke(main, ["auth", "renew", "--all"])

    assert result.exit_code == 0, result.output
    assert "skipped" in result.output


def test_profile_and_all_are_mutually_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_profile("alice")
    visited = _mock_renew(monkeypatch, {})

    result = CliRunner().invoke(main, ["auth", "renew", "--all", "--profile", "alice"])

    assert result.exit_code == 2
    assert visited == []


def test_bracketed_email_renders_without_markup_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_profile("alice")

    async def fake_renew(profile_dir: Path, *, source: str = "renew") -> RenewResult:
        verdict = FlowSessionStatus(
            outcome=FlowSessionOutcome.AUTHENTICATED, user_email="[user]@example.com", source=source
        )
        return RenewResult(verdict=verdict, landing=None, labs_stale=False)

    monkeypatch.setattr("gflow_cli.auth.renew.renew_profile", fake_renew)

    result = CliRunner().invoke(main, ["auth", "renew", "--profile", "alice"])

    assert result.exit_code == 0
    assert "[user]@example.com" in result.output


@pytest.mark.parametrize(
    ("landing", "labs_stale", "expected"),
    [
        ("public", False, "public_landing"),
        (None, True, "labs_stale"),
        (None, None, "labs_unverified"),
    ],
)
def test_an_about_account_is_not_reported_as_renewed(
    monkeypatch: pytest.MonkeyPatch, landing: str | None, labs_stale: bool | None, expected: str
) -> None:
    """The verifier says fine on an /about account (KNOWN_ISSUES, #902). Renewal saw
    Flow hold it at the public page, or labs never finish its re-sign-in, so it must
    not print "renewed and verified" and exit 0. Measured live 2026-10-14: 3 of 10."""
    _make_profile("alice")
    _mock_renew(
        monkeypatch,
        {"alice": FlowSessionOutcome.AUTHENTICATED},
        landing=landing,
        labs_stale=labs_stale,
    )

    result = CliRunner().invoke(main, ["auth", "renew", "--profile", "alice", "--json"])

    assert result.exit_code == 1, result.output
    assert json.loads(result.output)[0]["result"] == expected

    text = CliRunner().invoke(main, ["auth", "renew", "--profile", "alice"])
    assert "renewed and verified" not in text.output
    # Only a public landing was observed at /about; a labs row must not claim it.
    assert ("public page" in text.output) is (expected == "public_landing")


def test_a_browser_that_cannot_start_is_a_row_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from playwright.async_api import Error as PlaywrightError

    _make_profile("alice")
    _make_profile("bob")
    _mock_renew(
        monkeypatch,
        {
            "alice": PlaywrightError("ProcessSingleton: profile in use"),
            "bob": FlowSessionOutcome.AUTHENTICATED,
        },
    )

    result = CliRunner().invoke(main, ["auth", "renew", "--all", "--json"])

    assert result.exit_code == 1, result.output
    rows = {r["profile"]: r for r in json.loads(result.output)}
    assert rows["alice"]["result"] == "launch_error"
    assert rows["bob"]["result"] == FlowSessionOutcome.AUTHENTICATED.value


def test_an_engine_downgrade_is_a_row_with_its_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_profile("alice")
    _mock_renew(
        monkeypatch,
        {"alice": ProfileEngineDowngradeError(detail="newer", remediation_hint="use chrome")},
    )

    result = CliRunner().invoke(main, ["auth", "renew", "--profile", "alice", "--json"])

    assert result.exit_code == 1, result.output
    row = json.loads(result.output)[0]
    assert row["result"] == "engine_downgrade"
    assert row["hint"] == "use chrome"


def test_all_with_no_saved_sessions_still_speaks_json() -> None:
    result = CliRunner().invoke(main, ["auth", "renew", "--all", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "status": "error",
        "error": "No profiles with a saved session.",
    }


def test_profile_and_all_conflict_still_speaks_json() -> None:
    result = CliRunner().invoke(main, ["auth", "renew", "--all", "--profile", "x", "--json"])

    assert result.exit_code == 2
    assert json.loads(result.output)["status"] == "error"
