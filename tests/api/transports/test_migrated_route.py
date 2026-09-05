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
    ("url", "flow_host", "prefer", "expected"),
    [
        # auto: the served host decides for requests the new host cannot serve …
        (_LABS, "auto", False, "labs"),
        # … and flow.google.com is the default for what it CAN serve, on any account
        (_LABS, "auto", True, "migrated"),
        ("about:blank", "auto", True, "migrated"),
        (_MIGRATED, "auto", False, "migrated"),
        (_LABS, "flow.google.com", False, "migrated"),
        (_MIGRATED, "flow.google.com", False, "migrated"),
        (_LABS, "labs.google", False, "labs"),
        (_LABS, "labs.google", True, "labs"),  # kill switch beats preference
        (_MIGRATED, "labs.google", False, "blocked"),
        # unreadable / blank URL with nothing to prefer: the labs path keeps probing
        (None, "auto", False, "labs"),
        ("about:blank", "auto", False, "labs"),
    ],
)
def test_migrated_route(url: str | None, flow_host: str, prefer: bool, expected: str) -> None:
    from gflow_cli.api.transports._common import migrated_route

    assert migrated_route(url, flow_host, prefer_migrated=prefer) == expected


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
