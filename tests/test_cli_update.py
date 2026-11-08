"""`gflow update` — CLI surface over :func:`gflow_cli.update_check.run_update`.

The manager subprocess is never spawned here: `run_update` is replaced at the
seam the command imports it through (``gflow_cli.cli_update.run_update``).
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from gflow_cli import __version__, cli, cli_update
from gflow_cli.cli import main
from gflow_cli.errors import ConfigurationError
from gflow_cli.update_check import UpdateNotice, UpdateReport


def _report(**overrides: object) -> UpdateReport:
    base: dict[str, object] = {
        "installed": __version__,
        "latest": "999.0.0",
        "installer": "uv",
        "command": ("uv", "tool", "upgrade", "gflow-cli"),
        "update_available": True,
        "upgraded": False,
    }
    base.update(overrides)
    return UpdateReport(**base)  # type: ignore[arg-type]


def test_check_text(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def _fake(*, check: bool) -> UpdateReport:
        seen.append(check)
        return _report()

    monkeypatch.setattr(cli_update, "run_update", _fake)
    res = CliRunner().invoke(main, ["update", "--check"])
    assert res.exit_code == 0, res.output
    assert seen == [True]
    assert "999.0.0" in res.output
    assert __version__ in res.output
    assert "uv tool upgrade gflow-cli" in res.output
    assert "gflow update" in res.output


def test_check_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_update, "run_update", lambda *, check: _report())
    res = CliRunner().invoke(main, ["update", "--check", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["status"] == "ok"
    assert payload["installed"] == __version__
    assert payload["latest"] == "999.0.0"
    assert payload["update_available"] is True
    assert payload["upgraded"] is False
    assert payload["installer"] == "uv"
    assert payload["command"] == ["uv", "tool", "upgrade", "gflow-cli"]


def test_up_to_date_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_update,
        "run_update",
        lambda *, check: _report(latest=__version__, update_available=False),
    )
    res = CliRunner().invoke(main, ["update"])
    assert res.exit_code == 0, res.output
    assert "up to date" in res.output


def test_upgraded_text_with_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_update,
        "run_update",
        lambda *, check: _report(upgraded=True, notes=("Playwright changed: run X",)),
    )
    res = CliRunner().invoke(main, ["update"])
    assert res.exit_code == 0, res.output
    assert "999.0.0" in res.output
    assert "Playwright changed: run X" in res.output


def test_check_pypi_unreachable_names_next_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_update,
        "run_update",
        lambda *, check: _report(latest=None, update_available=None),
    )
    res = CliRunner().invoke(main, ["update", "--check"])
    assert res.exit_code == 0, res.output
    assert "PyPI unreachable" in res.output
    assert "gflow update" in res.output


def _notice() -> UpdateNotice:
    return UpdateNotice(installed="0.1.0", latest="999.0.0")


def test_banner_one_line_when_stderr_is_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "maybe_notify_update", _notice)
    monkeypatch.setattr(cli, "_stderr_is_terminal", lambda: False)
    res = CliRunner().invoke(main, ["models", "--json"])
    assert res.exit_code == 0, res.output
    json.loads(res.stdout)  # stdout stays a single JSON document
    assert res.stderr.count("\n") == 1
    assert "999.0.0" in res.stderr
    assert "gflow update" in res.stderr


def test_banner_panel_when_stderr_is_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "maybe_notify_update", _notice)
    monkeypatch.setattr(cli, "_stderr_is_terminal", lambda: True)
    res = CliRunner().invoke(main, ["models", "--json"])
    assert res.exit_code == 0, res.output
    json.loads(res.stdout)
    assert "Update available" in res.stderr
    assert "gflow-cli 999.0.0" in res.stderr
    assert "releases/tag/v999.0.0" in res.stderr


def test_source_install_exits_11(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake(*, check: bool) -> UpdateReport:
        raise ConfigurationError("gflow-cli here is not a PyPI install")

    monkeypatch.setattr(cli_update, "run_update", _fake)
    res = CliRunner().invoke(main, ["update"])
    assert res.exit_code == 11


def test_manager_failure_exits_11_json_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake(*, check: bool, **_kwargs: object) -> UpdateReport:
        raise ConfigurationError("`uv tool upgrade gflow-cli` exited 3")

    monkeypatch.setattr(cli_update, "run_update", _fake)
    res = CliRunner().invoke(main, ["update", "--json"])
    assert res.exit_code == 11
    payload = json.loads(res.stdout)  # stderr carries the error_raised log line
    assert payload["status"] == "fail"
    assert payload["error"]["class"] == "ConfigurationError"
    assert payload["error"]["exit_code"] == 11
    assert "exited 3" in payload["error"]["detail"]


def test_update_suppresses_banner_with_and_without_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "maybe_notify_update", _notice)
    monkeypatch.setattr(cli, "_stderr_is_terminal", lambda: True)
    monkeypatch.setattr(cli_update, "run_update", lambda *, check, **kw: _report())

    # gflow update --check suppresses the banner
    res_check = CliRunner().invoke(main, ["update", "--check"])
    assert res_check.exit_code == 0
    assert "Update available" not in res_check.output

    # gflow update suppresses the banner
    res_update = CliRunner().invoke(main, ["update"])
    assert res_update.exit_code == 0
    assert "Update available" not in res_update.output


def test_update_verbose_option_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_verbose: list[bool] = []

    def _fake(*, check: bool, verbose: bool = False, **_kwargs: object) -> UpdateReport:
        seen_verbose.append(verbose)
        return _report()

    monkeypatch.setattr(cli_update, "run_update", _fake)
    res = CliRunner().invoke(main, ["update", "--verbose"])
    assert res.exit_code == 0
    assert seen_verbose == [True]

    seen_verbose.clear()
    res = CliRunner().invoke(main, ["update", "-v"])
    assert res.exit_code == 0
    assert seen_verbose == [True]

    seen_verbose.clear()
    res = CliRunner().invoke(main, ["-v", "update"])
    assert res.exit_code == 0
    assert seen_verbose == [True]


def test_status_callback_prints_when_not_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake(*, check: bool, status_callback: object = None, **_kwargs: object) -> UpdateReport:
        if callable(status_callback):
            status_callback("Updating gflow-cli 0.1.0 -> 999.0.0 via uv...")
        return _report(upgraded=True)

    monkeypatch.setattr(cli_update, "run_update", _fake)
    res = CliRunner().invoke(main, ["update"])
    assert res.exit_code == 0
    assert "Updating gflow-cli 0.1.0 -> 999.0.0 via uv..." in res.output
    assert "Updated to 999.0.0. Restart any running `gflow serve` / MCP server." in res.output

    # In --json mode, status_callback does not write to stdout
    res_json = CliRunner().invoke(main, ["update", "--json"])
    assert res_json.exit_code == 0
    payload = json.loads(res_json.stdout)
    assert payload["status"] == "ok"
    assert "Updating" not in res_json.stdout
