"""Shared pytest fixtures for the gflow-cli test suite.

The ``install_log_capture`` fixture replaces 4+ inline copies of
``structlog.configure(processors=[merge_contextvars, cap])`` scattered across
test files. Centralizing prevents the most likely regression: a new test that
needs to assert on a contextvar-bound field (e.g. ``correlation_id``) and
silently omits ``merge_contextvars`` from its processor chain, yielding
mysteriously-missing fields in the captured event dict.
"""

from __future__ import annotations

import pytest
import structlog


@pytest.fixture
def install_log_capture() -> structlog.testing.LogCapture:
    """Install a fresh structlog ``LogCapture`` processor + ``merge_contextvars``.

    Use as a fixture argument::

        def test_event_shape(install_log_capture: structlog.testing.LogCapture) -> None:
            log = structlog.get_logger("test")
            log.info("hello", extra="value")
            assert install_log_capture.entries[0]["event"] == "hello"

    structlog 25.x's ``LogCapture()`` takes no constructor args; captured
    events accumulate on ``.entries``. ``merge_contextvars`` must run BEFORE
    ``LogCapture`` so contextvar-bound fields (``correlation_id``,
    ``cli_version``) land in the captured event dict — without it, any test
    that asserts on those fields silently fails with a confusing "key missing".
    """
    cap = structlog.testing.LogCapture()
    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, cap],
    )
    return cap
