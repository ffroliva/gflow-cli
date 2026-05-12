"""Factory selection precedence, unknown-name errors, and CLI gating."""

from __future__ import annotations

import pytest

from gflow_cli.api.transports import make_transport, transport_choices
from gflow_cli.errors import ConfigurationError


def test_default_transport_is_ui_automation(monkeypatch):
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    t = make_transport()
    assert t.name == "ui_automation"


def test_explicit_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv("GFLOW_CLI_TRANSPORT", "ui_automation")
    t = make_transport("bearer")
    assert t.name == "bearer"


def test_env_var_resolves_when_no_arg(monkeypatch):
    monkeypatch.setenv("GFLOW_CLI_TRANSPORT", "evaluate_fetch")
    t = make_transport()
    assert t.name == "evaluate_fetch"


def test_unknown_strategy_raises_configuration_error_listing_options(monkeypatch):
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    with pytest.raises(ConfigurationError) as exc:
        make_transport("nonexistent_transport_xyz")
    msg = str(exc.value)
    assert "nonexistent_transport_xyz" in msg
    assert "ui_automation" in msg
    assert "evaluate_fetch" in msg
    assert "bearer" in msg
    assert "sapisidhash" in msg


def test_transport_choices_default_only_lists_ui_automation(monkeypatch):
    monkeypatch.delenv("GFLOW_CLI_EXPERIMENTAL_TRANSPORTS", raising=False)
    assert transport_choices() == ["ui_automation"]


def test_transport_choices_with_experimental_env_lists_all(monkeypatch):
    monkeypatch.setenv("GFLOW_CLI_EXPERIMENTAL_TRANSPORTS", "1")
    choices = transport_choices()
    assert "ui_automation" in choices
    assert "evaluate_fetch" in choices
    assert "bearer" in choices
    assert "sapisidhash" in choices


def test_factory_accepts_experimental_keys_without_env_gate(monkeypatch):
    """Python API stays unrestricted — gating is at CLI Choice list only."""
    monkeypatch.delenv("GFLOW_CLI_EXPERIMENTAL_TRANSPORTS", raising=False)
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    t = make_transport("bearer")
    assert t.name == "bearer"
