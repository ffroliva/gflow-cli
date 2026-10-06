"""GFLOW_CLI_AGENT_CONFIRM — the agent-only composer's "Confirm before generating" choice (#799)."""

from __future__ import annotations

import pytest


def test_agent_confirm_defaults_to_account(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.config import Settings

    monkeypatch.delenv("GFLOW_CLI_AGENT_CONFIRM", raising=False)
    assert Settings().agent_confirm == "account"


@pytest.mark.parametrize("value", ["account", "always", "never"])
def test_agent_confirm_reads_env(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from gflow_cli.config import Settings

    monkeypatch.setenv("GFLOW_CLI_AGENT_CONFIRM", value)
    assert Settings().agent_confirm == value


def test_agent_confirm_rejects_unknown_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.config import Settings

    monkeypatch.setenv("GFLOW_CLI_AGENT_CONFIRM", "sometimes")
    with pytest.raises(ValueError):
        Settings()
