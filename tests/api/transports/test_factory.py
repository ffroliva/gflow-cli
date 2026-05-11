"""Factory selection precedence + unknown-name error tests."""
from __future__ import annotations

import pytest

from gflow_cli.api.transports import make_transport
from gflow_cli.errors import ConfigurationError


def test_default_transport_is_evaluate_fetch(monkeypatch):
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    t = make_transport()
    assert t.name == "evaluate_fetch"


def test_explicit_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv("GFLOW_CLI_TRANSPORT", "evaluate_fetch")
    t = make_transport("bearer")
    assert t.name == "bearer"


def test_env_var_resolves_when_no_arg(monkeypatch):
    monkeypatch.setenv("GFLOW_CLI_TRANSPORT", "sapisidhash")
    t = make_transport()
    assert t.name == "sapisidhash"


def test_unknown_strategy_raises_configuration_error_listing_options(monkeypatch):
    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    with pytest.raises(ConfigurationError) as exc:
        make_transport("nonexistent_transport_xyz")
    msg = str(exc.value)
    assert "nonexistent_transport_xyz" in msg
    assert "evaluate_fetch" in msg
    assert "bearer" in msg
    assert "sapisidhash" in msg
