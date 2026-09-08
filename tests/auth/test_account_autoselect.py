"""Tests for FlowAccountChooserError (exit code 33) and account auto-selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.errors import (
    EXIT_CODE_MAP,
    FlowAccountChooserError,
    GFlowError,
    is_retryable,
)


def test_flow_account_chooser_error_class_invariants() -> None:
    """FlowAccountChooserError is a non-retryable GFlowError with RFC 9457 attributes."""
    err = FlowAccountChooserError(
        detail=(
            "Account chooser displayed but recorded account 'user@example.com' was not selectable."
        )
    )
    assert isinstance(err, GFlowError)
    assert not is_retryable(err)
    assert err.problem_type == "https://gflow-cli.dev/errors/flow-account-chooser"
    assert err.title == "Recorded Google account not selectable"
    assert "gflow auth login" in err.remediation_hint
    assert "--account" in err.remediation_hint


def test_flow_account_chooser_error_exit_code_33() -> None:
    """FlowAccountChooserError maps to exit code 33 in EXIT_CODE_MAP."""
    err = FlowAccountChooserError(detail="test")
    assert EXIT_CODE_MAP[FlowAccountChooserError] == 33
    # Check isinstance walk correctly resolves to 33
    code = next(c for cls, c in EXIT_CODE_MAP.items() if isinstance(err, cls))
    assert code == 33


def test_exit_code_map_ordering_with_flow_account_chooser_error() -> None:
    """Most-specific classes MUST appear before parent classes in EXIT_CODE_MAP."""
    seen: list[type] = []
    for cls in EXIT_CODE_MAP:
        for prior in seen:
            assert not issubclass(cls, prior), (
                f"{cls.__name__} is a subclass of {prior.__name__} but appears AFTER it; "
                f"swap their order in EXIT_CODE_MAP."
            )
        seen.append(cls)


def test_read_account_file_returns_email(tmp_path: Path) -> None:
    """read_account_file helper returns stripped email or None."""
    from gflow_cli.profile_store import ACCOUNT_FILE, read_account_file

    profile_dir = tmp_path / "profile_test"
    profile_dir.mkdir()
    assert read_account_file(profile_dir) is None

    (profile_dir / ACCOUNT_FILE).write_text("  User.Test@Gmail.Com \n", encoding="utf-8")
    assert read_account_file(profile_dir) == "User.Test@Gmail.Com"


def test_auth_login_with_account_mismatch_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gflow auth login --account asserts against verified session email and fails with exit 33."""
    from click.testing import CliRunner

    from gflow_cli.cli import main as cli

    # Mock login to write one email, but caller asked for a different account
    async def _mock_login(name: str, browser: str = "auto", headless: bool = False) -> Path:
        pdir = tmp_path / f"profile_{name}"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / ".gflow_account").write_text("actual@example.com", encoding="utf-8")
        return pdir

    monkeypatch.setattr("gflow_cli.auth.login", _mock_login)
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["auth", "login", "--profile", "test", "--account", "expected@example.com"],
    )
    assert result.exit_code == 33
    assert "Recorded Google account not selectable" in result.output or (
        "does not match" in result.output
    )
