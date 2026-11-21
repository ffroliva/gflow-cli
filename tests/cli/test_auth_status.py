"""`gflow auth status` proves the Flow session and exits 0/1 (issue #471).

The probe is `verify_flow_profile` — mocked here (network-free); its own
behavior is covered by tests/auth/test_verification*.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus
from gflow_cli.cli import main
from gflow_cli.config import get_settings


def _make_profile(name: str) -> Path:
    pdir = get_settings().profile_subdir(name)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "Cookies").write_bytes(b"")
    return pdir


def _mock_probe(
    monkeypatch: pytest.MonkeyPatch, outcome: FlowSessionOutcome, email: str | None = None
) -> dict[str, int]:
    calls = {"n": 0}

    async def fake_probe(profile_dir: Path, *, source: str = "chrome") -> FlowSessionStatus:
        calls["n"] += 1
        return FlowSessionStatus(outcome=outcome, user_email=email, source=source)

    monkeypatch.setattr("gflow_cli.auth.verification.verify_flow_profile", fake_probe)
    return calls


def test_exit_0_when_flow_session_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_profile("probed")
    calls = _mock_probe(monkeypatch, FlowSessionOutcome.AUTHENTICATED, "user@example.com")

    result = CliRunner().invoke(main, ["auth", "status", "--profile", "probed"])

    assert result.exit_code == 0
    assert calls["n"] == 1
    assert "user@example.com" in result.output


def test_exit_1_with_hint_when_session_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_profile("probed")
    calls = _mock_probe(monkeypatch, FlowSessionOutcome.GOOGLE_SESSION_ONLY)

    result = CliRunner().invoke(main, ["auth", "status", "--profile", "probed"])

    assert result.exit_code == 1
    assert calls["n"] == 1
    assert "gflow auth login" in result.output


def test_exit_1_without_probe_when_no_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    pdir = get_settings().profile_subdir("empty")
    pdir.mkdir(parents=True, exist_ok=True)  # no Cookies file
    calls = _mock_probe(monkeypatch, FlowSessionOutcome.AUTHENTICATED)

    result = CliRunner().invoke(main, ["auth", "status", "--profile", "empty"])

    assert result.exit_code == 1
    assert calls["n"] == 0  # nothing to probe without cookies
    assert "gflow auth login" in result.output


def test_exit_1_on_verification_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed: an unreachable endpoint is NOT an authenticated session —
    but the remediation must point at connectivity, not a pointless re-login."""
    _make_profile("probed")
    _mock_probe(monkeypatch, FlowSessionOutcome.VERIFICATION_ERROR)

    result = CliRunner().invoke(main, ["auth", "status", "--profile", "probed"])

    assert result.exit_code == 1
    assert "gflow auth login" not in result.output
    assert "connectivity" in result.output


def test_exit_1_naming_chrome_when_the_profile_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1400: Chrome holding the profile (or not starting) is local, not the network."""
    _make_profile("probed")
    _mock_probe(monkeypatch, FlowSessionOutcome.PROFILE_UNAVAILABLE)

    result = CliRunner().invoke(main, ["auth", "status", "--profile", "probed"])

    assert result.exit_code == 1
    assert "connectivity" not in result.output
    assert "in use" in result.output
    assert "Google Chrome" in result.output


@pytest.mark.skipif(
    sys.platform != "win32",
    reason=(
        "browser_cookie3 reads the keyring key before opening the DB on Linux/macOS; "
        "without a keyring (CI) it falls back first and never reaches the corrupt file"
    ),
)
def test_corrupt_cookie_store_is_not_blamed_on_the_network() -> None:
    """#796, end to end through the real probe: a corrupt cookie DB on disk
    (no mock of verify_flow_profile or browser_cookie3) must not tell the user to
    check network connectivity — the fault is the file on their own disk."""
    pdir = get_settings().profile_subdir("corrupt")
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "Cookies").write_bytes(b"SQLite format 3\x00" + b"\xa5" * 4080)

    result = CliRunner().invoke(main, ["auth", "status", "--profile", "corrupt"])

    assert result.exit_code == 1
    assert "connectivity" not in result.output
    assert "cookie store" in result.output.lower()
    assert "gflow auth login --profile corrupt" in result.output


def test_bracketed_email_renders_without_markup_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server-supplied email is untrusted text — Rich markup must be escaped
    (this repo's known bracketed-text crash class)."""
    _make_profile("probed")
    _mock_probe(monkeypatch, FlowSessionOutcome.AUTHENTICATED, "[user]@example.com")

    result = CliRunner().invoke(main, ["auth", "status", "--profile", "probed"])

    assert result.exit_code == 0
    assert result.exception is None
