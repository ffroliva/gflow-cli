"""The default transport is ``direct_http``; ``ui_automation`` stays as the kill switch."""

from __future__ import annotations

import pytest

from gflow_cli.api.transports import (
    EXPERIMENTAL_TRANSPORTS,
    make_transport,
    resolve_transport_name,
    transport_choices,
)


def test_default_transport_is_direct_http(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.api.transports.direct_http import DirectHttpTransport

    monkeypatch.delenv("GFLOW_CLI_TRANSPORT", raising=False)
    assert resolve_transport_name() == "direct_http"
    assert isinstance(make_transport(), DirectHttpTransport)
    # Production, not experimental: the CLI lists it without the experimental gate.
    monkeypatch.delenv("GFLOW_CLI_EXPERIMENTAL_TRANSPORTS", raising=False)
    assert "direct_http" not in EXPERIMENTAL_TRANSPORTS
    assert "direct_http" in transport_choices()


def test_ui_automation_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.api.transports.direct_http import DirectHttpTransport

    monkeypatch.setenv("GFLOW_CLI_TRANSPORT", "ui_automation")
    monkeypatch.delenv("GFLOW_CLI_EXPERIMENTAL_TRANSPORTS", raising=False)
    transport = make_transport()
    assert transport.name == "ui_automation"
    assert not isinstance(transport, DirectHttpTransport)
    assert "ui_automation" in transport_choices()
