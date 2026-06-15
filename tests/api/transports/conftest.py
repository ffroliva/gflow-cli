"""Shared fixtures for transport unit tests.

`detect_ui_mode` polls the DOM for up to several seconds before defaulting to
classic (so the live driver doesn't race the agentic composer's render — see
`drivers/factory.py`). Transport unit tests drive heavily-mocked pages that
expose no cohort signal, which would make every `get_ui_driver` call sit through
the full poll window. Collapse the window to zero here so those tests stay fast;
detection logic still runs (one probe pass), and `test_factory.py` exercises the
real polling behaviour via explicit `timeout_s` arguments.
"""

from __future__ import annotations

import pytest

from gflow_cli.api.transports.drivers import factory


@pytest.fixture(autouse=True)
def _fast_ui_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory, "_DETECT_TIMEOUT_S", 0.0)
    monkeypatch.setattr(factory, "_DETECT_POLL_INTERVAL_S", 0.0)
