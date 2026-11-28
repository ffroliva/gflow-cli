"""``--browser-visibility``: one shared option that overrides the env setting per invocation.

SCENARIO.md row 22 (the user sets the visibility explicitly).
"""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from gflow_cli import config
from gflow_cli._cli_helpers import browser_visibility_option, client_visibility
from gflow_cli.api.generation_route import Route
from gflow_cli.api.submit_policy import BrowserVisibility
from gflow_cli.cli import main


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GFLOW_CLI_HEADLESS", raising=False)
    monkeypatch.delenv("GFLOW_CLI_BROWSER_VISIBILITY", raising=False)
    config.reset_settings()
    yield
    config.reset_settings()


def _probe() -> click.Command:
    """A leaf that reports the visibility a direct-only run would launch with."""

    @click.command("probe")
    @browser_visibility_option()
    def probe() -> None:
        click.echo(client_visibility(config.get_settings(), (Route.DIRECT,)).value)

    return probe


def test_option_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_BROWSER_VISIBILITY", "headless")
    config.reset_settings()
    result = CliRunner().invoke(_probe(), ["--browser-visibility", "headed"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == BrowserVisibility.HEADED.value


def test_headed_option_disables_headless_mint() -> None:
    result = CliRunner().invoke(_probe(), ["--browser-visibility", "headed"])
    assert result.output.strip() == BrowserVisibility.HEADED.value


def test_absent_option_keeps_the_env_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_BROWSER_VISIBILITY", "headed")
    config.reset_settings()
    result = CliRunner().invoke(_probe(), [])
    assert result.output.strip() == BrowserVisibility.HEADED.value


def test_invalid_value_is_a_usage_error() -> None:
    result = CliRunner().invoke(_probe(), ["--browser-visibility", "sideways"])
    assert result.exit_code == 2


@pytest.mark.parametrize(
    "path",
    [
        ("image", "t2i"),
        ("image", "i2i"),
        ("image", "generate"),
        ("video", "t2v"),
        ("video", "i2v"),
        ("video", "generate"),
        ("video", "upscale"),
        ("run",),
    ],
)
def test_help_text_names_env_and_values(path: tuple[str, ...]) -> None:
    result = CliRunner().invoke(main, [*path, "--help"], terminal_width=200)
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "--browser-visibility [auto|headless|headed]" in flat
    assert "GFLOW_CLI_BROWSER_VISIBILITY" in flat
