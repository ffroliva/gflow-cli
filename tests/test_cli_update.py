"""`gflow update` — CLI surface over :func:`gflow_cli.update_check.run_update`.

The manager subprocess is never spawned here: `run_update` is replaced at the
seam the command imports it through (``gflow_cli.cli_update.run_update``).
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from gflow_cli import __version__, cli_update
from gflow_cli.cli import main
from gflow_cli.errors import ConfigurationError
from gflow_cli.update_check import UpdateReport


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


def test_source_install_exits_11(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake(*, check: bool) -> UpdateReport:
        raise ConfigurationError("gflow-cli here is not a PyPI install")

    monkeypatch.setattr(cli_update, "run_update", _fake)
    res = CliRunner().invoke(main, ["update"])
    assert res.exit_code == 11


def test_manager_failure_exits_11_json_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake(*, check: bool) -> UpdateReport:
        raise ConfigurationError("`uv tool upgrade gflow-cli` exited 3")

    monkeypatch.setattr(cli_update, "run_update", _fake)
    res = CliRunner().invoke(main, ["update", "--json"])
    assert res.exit_code == 11
    payload = json.loads(res.stdout)  # stderr carries the error_raised log line
    assert payload["status"] == "fail"
    assert payload["error"]["class"] == "ConfigurationError"
    assert payload["error"]["exit_code"] == 11
    assert "exited 3" in payload["error"]["detail"]
