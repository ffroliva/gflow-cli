from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main


def test_tools_list_shows_creative_director() -> None:
    result = CliRunner().invoke(main, ["tools", "list"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "creative-director" in result.output


def test_tools_show_lists_styles() -> None:
    result = CliRunner().invoke(
        main, ["tools", "show", "creative-director"], catch_exceptions=False
    )
    assert result.exit_code == 0
    assert "cinema" in result.output.lower()


def test_tools_run_json_when_unconfigured_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: object,  # noqa: ARG001 — configures structlog LogCapture before invoke
) -> None:
    for _var in ("GFLOW_CLI_LLM_API_KEY", "GFLOW_CLI_LLM_BASE_URL", "GFLOW_CLI_LLM_MODEL"):
        monkeypatch.delenv(_var, raising=False)
    # Isolate from any developer `.env` (issue #264): `delenv` only clears the
    # process env, but Settings re-reads the values from a CWD/home `.env` on the
    # next build — so on a machine that configures the prompt tools in `.env`
    # the tool WOULD expand and this "unconfigured" assertion fails. Neutralize
    # the dotenv sources so the deleted env vars actually mean "unconfigured".
    monkeypatch.setattr("gflow_cli.config._env_files", tuple)
    # Prevent main() from overriding the LogCapture structlog config with a
    # PrintLogger that would route the "no key" warning into result.output.
    monkeypatch.setattr("gflow_cli.cli.configure_logging", lambda *_a, **_kw: None)
    from gflow_cli.config import reset_settings

    reset_settings()
    result = CliRunner().invoke(
        main,
        ["tools", "run", "creative-director", "cat in space", "--json"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["name"] == "creative-director"
    assert payload["original"] == "cat in space"
    assert payload["was_expanded"] is False  # no key → graceful fallback
    assert payload["expanded"] == "cat in space"
