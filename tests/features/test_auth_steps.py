"""Step bindings for auth.feature.

Scoped to this feature only — pytest-bdd uses module-scoped step registries
(per-conftest `scenarios()` call) so step phrases here will not leak into
video or image scenarios.

The first three scenarios touch real ``profile_store`` + filesystem under
``tmp_path`` (sandboxed via ``GFLOW_CLI_HOME``). The fourth scenario
(``Auth-expired error``) patches ``gflow_cli.cli_image._run_t2i`` to raise
:class:`AuthExpiredError`, mirroring ``tests/cli/test_error_handling.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pytest_bdd import given, scenarios, then, when

from gflow_cli import config
from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus
from gflow_cli.cli import main
from gflow_cli.errors import AuthExpiredError

scenarios("auth.feature")


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
    """Redirect ``$GFLOW_CLI_HOME`` to ``tmp_path`` so profile-store I/O is
    sandboxed. ``Settings`` is cached via ``lru_cache``; reset on enter and
    exit so cache state doesn't bleed between tests.
    """
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.delenv("GFLOW_CLI_PROFILE", raising=False)
    config.reset_settings()
    yield
    config.reset_settings()


@pytest.fixture(autouse=True)
def _no_live_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """``gflow auth`` (no subcommand) invokes ``auth login`` when no profiles
    exist — which would open a real Chromium window. Stub out the async
    Playwright login so the "no profiles" scenario stays mocked-only."""

    async def _fake_login(
        name: str = "default", browser: str = "auto", headless: bool = False
    ) -> Path:
        # Don't actually create a profile dir — the "List profiles when none
        # exist" scenario asserts the empty-state banner BEFORE the login
        # would persist anything in the real flow. ``Path(os.devnull)`` is
        # portable (``NUL`` on Windows, ``/dev/null`` on POSIX); a hardcoded
        # ``/dev/null`` would break on the project's Windows target.
        import os

        return Path(os.devnull)

    monkeypatch.setattr("gflow_cli.auth.login", _fake_login)


# ---------------------------------------------------------------------------
# Given steps
# ---------------------------------------------------------------------------


@given("the profile root is empty")
def _empty_profile_root(tmp_path: Path) -> None:
    # ``_isolate_profile_home`` already points ``GFLOW_CLI_HOME`` at
    # ``tmp_path``. Nothing to create — absence is the precondition.
    assert not any(tmp_path.glob("profile_*"))


@given('a profile "experiments" exists')
def _profile_experiments_exists(tmp_path: Path) -> None:
    """Create a minimal on-disk profile so ``profile_store.list_profiles``
    surfaces it. A ``Cookies`` file inside the profile dir is enough to
    register as a real profile per ``profile_store._last_modified``.
    """
    pdir = tmp_path / "profile_experiments"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "Cookies").write_bytes(b"")


def _patch_probe(monkeypatch: pytest.MonkeyPatch, outcome: FlowSessionOutcome) -> None:
    async def fake_probe(profile_dir: Path, *, source: str = "chrome") -> FlowSessionStatus:
        return FlowSessionStatus(outcome=outcome, user_email=None, source=source)

    monkeypatch.setattr("gflow_cli.auth.verification.verify_flow_profile", fake_probe)


@given("the Flow session probe reports authenticated")
def _probe_authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch, FlowSessionOutcome.AUTHENTICATED)


@given("the Flow session probe reports no session")
def _probe_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe(monkeypatch, FlowSessionOutcome.NO_SESSION)


@given("the mocked FlowApiClient raises AuthExpiredError")
def _mock_auth_expired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Patch ``_run_t2i`` to raise :class:`AuthExpiredError` so the CLI
    boundary maps it to exit code 3 + the ``gflow auth login`` remediation
    hint. Also patch profile resolution (a profile must exist for the t2i
    command to reach the run-helper).
    """
    monkeypatch.setattr("gflow_cli.cli_image._resolve_profile", lambda profile: "test")
    monkeypatch.setattr("gflow_cli.cli_image._make_provider_dir", lambda name: tmp_path)

    async def _raise(*args: Any, **kwargs: Any) -> None:
        raise AuthExpiredError(detail="401", status=401, route="createProject")

    monkeypatch.setattr("gflow_cli.cli_image._run_t2i", _raise)


# ---------------------------------------------------------------------------
# When steps
# ---------------------------------------------------------------------------


@when('I run "gflow auth"')
def _run_auth_bare(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["auth"])


@when('I run "gflow auth status --profile experiments"')
def _run_auth_status(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(
        main, ["auth", "status", "--profile", "experiments"]
    )


@when('I run "gflow auth use experiments"')
def _run_auth_use(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["auth", "use", "experiments"])


@when('I run "gflow image t2i some prompt"')
def _run_image_t2i_some_prompt(runner: CliRunner, cli_result_holder: dict[str, Any]) -> None:
    cli_result_holder["result"] = runner.invoke(main, ["image", "t2i", "some prompt"])


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


@then("the exit code is 3")
def _check_exit_3(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert result.exit_code == 3, result.output


@then('the output contains "No profiles found"')
def _check_no_profiles(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "No profiles found" in result.output


@then('the output contains "experiments"')
def _check_contains_experiments(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "experiments" in result.output


@then('the output contains "gflow auth login"')
def _check_login_remediation(cli_result_holder: dict[str, Any]) -> None:
    result = cli_result_holder["result"]
    assert "gflow auth login" in result.output


@then('the default profile is "experiments"')
def _check_default_profile(tmp_path: Path) -> None:
    """``gflow auth use experiments`` writes ``default_profile = "experiments"``
    into ``config.toml`` at the profile-store root. Verify on disk so we
    don't have to round-trip back through the cached ``Settings`` singleton.
    """
    cfg = tmp_path / "config.toml"
    assert cfg.exists(), f"expected {cfg} after `auth use`"
    contents = cfg.read_text(encoding="utf-8")
    assert 'default_profile = "experiments"' in contents
