"""CLI behaviour for invalid GFLOW_CLI_* enum settings.

A typo'd enum env var (e.g. GFLOW_CLI_BROWSER_ENGINE=patchwright) must fail with
a clean configuration error and exit 11 — naming the offending variable — instead
of leaking a raw pydantic ValidationError traceback and exiting 1.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.config import reset_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    reset_settings()
    yield
    reset_settings()


@pytest.mark.parametrize("env_var", ["GFLOW_CLI_BROWSER_ENGINE", "GFLOW_CLI_PROVIDER"])
def test_invalid_enum_setting_exits_11_cleanly(
    monkeypatch: pytest.MonkeyPatch, env_var: str
) -> None:
    monkeypatch.setenv(env_var, "definitely-not-a-valid-value")
    result = CliRunner().invoke(main, ["auth", "status", "--profile", "x"])
    assert result.exit_code == 11
    assert "Configuration error" in result.output
    assert env_var in result.output
    # The raw pydantic traceback must NOT leak to the user.
    assert "Traceback" not in result.output
    assert "ValidationError" not in result.output
