"""No gflow process logs an httpx request URL (#1229 review, security).

Constructing an ``MCPServer`` configures stdlib logging at INFO, and httpx logs every request
at INFO as ``HTTP Request: <METHOD> <full URL>``. The direct credits read carries ``f.sid``
and ``bl`` in its batchexecute URL, and MCP clients persist the server's stderr. gflow sets
the ``httpx`` and ``httpcore`` loggers to WARNING when the package is imported.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from mcp.server import MCPServer

_FAKE_FSID = "-987654321fakefsid"


def _request() -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, text="ok"))
    with httpx.Client(transport=transport) as client:
        client.post(f"https://flow.google.com/_/x/data/batchexecute?f.sid={_FAKE_FSID}&bl=b")


def test_an_mcp_server_process_never_logs_an_httpx_request_url(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Every gflow process imports the package (and its logger clamp) first.
    import gflow_cli  # noqa: F401

    MCPServer(name="probe")  # configures the root logger at INFO, as gflow's server does
    caplog.set_level(logging.DEBUG)
    httpx_logger = logging.getLogger("httpx")
    clamped = httpx_logger.level

    # Negative control: without the clamp this setup DOES leak the URL, so the assertion
    # below can see a regression.
    httpx_logger.setLevel(logging.NOTSET)  # setLevel, not .level: it clears the level cache
    try:
        _request()
        assert _FAKE_FSID in caplog.text
    finally:
        httpx_logger.setLevel(clamped)
    caplog.clear()

    _request()

    assert _FAKE_FSID not in caplog.text
    assert _FAKE_FSID not in capsys.readouterr().err
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
