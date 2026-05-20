"""Step bindings for auth_login.feature."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, scenarios, then, when

from gflow_cli import config
from gflow_cli.cli import main

scenarios("auth_login.feature")


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_result_holder() -> dict[str, Any]:
    return {"result": None}


@pytest.fixture(autouse=True)
def _isolate_profile_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.delenv("GFLOW_CLI_PROFILE", raising=False)
    config.reset_settings()
    yield
    config.reset_settings()


@pytest.fixture(autouse=True)
def _no_live_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent real Playwright from launching in any auth_login scenario."""

    async def _noop_login(self: object, profile_dir: Path, headless: bool) -> None:
        return None

    monkeypatch.setattr("gflow_cli.auth.real_chrome.RealChromeStrategy.login", _noop_login)
    monkeypatch.setattr(
        "gflow_cli.auth.internal_chromium.InternalChromiumStrategy.login", _noop_login
    )


# ---------------------------------------------------------------------------
# Given steps
# ---------------------------------------------------------------------------


@given("Chrome is installed on the system")
def _chrome_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gflow_cli.browser_manager.is_chrome_available", lambda: True)


@given("Chrome is NOT installed on the system")
def _chrome_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gflow_cli.browser_manager.is_chrome_available", lambda: False)


@given("the profile root is empty")
def _empty_profile_root(tmp_path: Path) -> None:
    assert not any(tmp_path.glob("profile_*"))


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------


@when('I run "gflow auth login --browser chrome"')
def _run_login_chrome(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["auth", "login", "--browser", "chrome"])


@when('I run "gflow auth login --browser internal"')
def _run_login_internal(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["auth", "login", "--browser", "internal"])


@when('I run "gflow auth login --browser auto"')
def _run_login_auto(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["auth", "login", "--browser", "auto"])


# ---------------------------------------------------------------------------
# Then steps
# ---------------------------------------------------------------------------


@then("the exit code is 0")
def _check_exit_0(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 0, result.output


@then("the exit code is 1")
def _check_exit_1(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 1, result.output


@then("the exit code is 11")
def _check_exit_11(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 11, result.output


@then('the output contains "Launching real Chrome"')
def _check_launching_chrome(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "Launching real Chrome" in result.output, result.output


@then('the output contains "Launching internal Chromium"')
def _check_launching_internal(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "Launching internal Chromium" in result.output, result.output


@then('the output contains "Chrome binary not found"')
def _check_chrome_not_found(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "Chrome binary not found" in result.output, result.output
