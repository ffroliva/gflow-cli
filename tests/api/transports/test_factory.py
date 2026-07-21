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


def test_standalone_only_transports_are_the_lease_reacquiring_experimentals():
    """bearer/sapisidhash discard the shared page and re-acquire their own
    ProfileLease in setup(); evaluate_fetch is dual-mode (shares the page) so it
    is intentionally NOT standalone-only."""
    from gflow_cli.api.transports import STANDALONE_ONLY_TRANSPORTS

    assert STANDALONE_ONLY_TRANSPORTS == frozenset({"bearer", "sapisidhash"})
    # Every standalone-only key must be a real, registered experimental transport.
    from gflow_cli.api.transports import EXPERIMENTAL_TRANSPORTS

    assert STANDALONE_ONLY_TRANSPORTS <= set(EXPERIMENTAL_TRANSPORTS)


def test_resolve_transport_name_precedence(monkeypatch):
    """arg > GFLOW_CLI_TRANSPORT env > built-in default — same precedence
    make_transport uses, exposed so the client guard resolves identically."""
    from gflow_cli.api.transports import resolve_transport_name

    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    assert resolve_transport_name() == "ui_automation"
    assert resolve_transport_name("bearer") == "bearer"
    monkeypatch.setenv("GFLOW_CLI_TRANSPORT", "sapisidhash")
    assert resolve_transport_name() == "sapisidhash"
    assert resolve_transport_name("bearer") == "bearer"  # explicit arg wins over env
