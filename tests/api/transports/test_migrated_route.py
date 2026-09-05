"""Routing decision for the migrated flow.google.com host (Task 2 of the
migrated-host-driver plan).

One setting, three values:

* ``auto`` (default) — the host Flow actually served decides: labs.google keeps
  the labs driver, flow.google.com gets the migrated composer
* ``flow.google.com`` — force the migrated composer for every account
* ``labs.google`` — never use the migrated composer; a flagged account keeps
  exit 36 (the kill switch)
"""

from __future__ import annotations

import pytest

_LABS = "https://labs.google/fx/en/tools/flow/project/abc"
_MIGRATED = "https://flow.google.com/project/abc"


@pytest.mark.parametrize(
    ("url", "flow_host", "expected"),
    [
        (_LABS, "auto", "labs"),
        (_MIGRATED, "auto", "migrated"),
        (_LABS, "flow.google.com", "migrated"),
        (_MIGRATED, "flow.google.com", "migrated"),
        (_LABS, "labs.google", "labs"),
        (_MIGRATED, "labs.google", "blocked"),
        # unreadable / blank URL: nothing to route on — the labs path keeps probing
        (None, "auto", "labs"),
        ("about:blank", "auto", "labs"),
    ],
)
def test_migrated_route(url: str | None, flow_host: str, expected: str) -> None:
    from gflow_cli.api.transports._common import migrated_route

    assert migrated_route(url, flow_host) == expected


def test_settings_flow_host_defaults_to_auto_and_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.config import Settings

    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    assert Settings().flow_host == "auto"
    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "flow.google.com")
    assert Settings().flow_host == "flow.google.com"


def test_settings_flow_host_rejects_unknown_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.config import Settings

    monkeypatch.setenv("GFLOW_CLI_FLOW_HOST", "example.com")
    with pytest.raises(ValueError):
        Settings()


def test_exit_36_remediation_names_the_switch() -> None:
    """A flagged account that hits exit 36 with the kill switch on must learn
    which setting put it there."""
    from gflow_cli.errors import FlowHostMigratedError

    hint = FlowHostMigratedError(detail="x").remediation_hint
    assert "GFLOW_CLI_FLOW_HOST" in hint
