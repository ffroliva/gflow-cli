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

from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus
from gflow_cli.cli import main
from gflow_cli.config import get_settings
from gflow_cli.errors import ProfileLockedError


def _make_profile(name: str) -> Path:
    pdir = get_settings().profile_subdir(name)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "Cookies").write_bytes(b"")
    return pdir


def _mock_renew(
    monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, FlowSessionOutcome | Exception]
) -> list[str]:
    visited: list[str] = []

    async def fake_renew(profile_dir: Path, *, source: str = "renew") -> FlowSessionStatus:
        name = profile_dir.name.removeprefix("profile_")
        visited.append(name)
        outcome = outcomes[name]
        if isinstance(outcome, Exception):
            raise outcome
        return FlowSessionStatus(outcome=outcome, user_email=f"{name}@example.com", source=source)

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

    async def fake_renew(profile_dir: Path, *, source: str = "renew") -> FlowSessionStatus:
        return FlowSessionStatus(
            outcome=FlowSessionOutcome.AUTHENTICATED, user_email="[user]@example.com", source=source
        )

    monkeypatch.setattr("gflow_cli.auth.renew.renew_profile", fake_renew)

    result = CliRunner().invoke(main, ["auth", "renew", "--profile", "alice"])

    assert result.exit_code == 0
    assert "[user]@example.com" in result.output
