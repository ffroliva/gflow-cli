"""No unit-test process may reach the internet (#1164 hardening).

On 2026-11-07 a unit test's child process ran the production uploader and POSTed a fixture report
to the live telemetry endpoint. ``tests/conftest.py::_no_outbound_network`` points every proxy
variable at a closed local port for non-e2e tests, so the test process and every child it spawns
fail fast on any outbound request while loopback servers stay reachable.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_CHILD = (
    "import sys, urllib.request\n"
    "try:\n"
    "    urllib.request.urlopen(sys.argv[1], timeout=10)\n"
    "except Exception as exc:\n"
    "    print(type(exc).__name__, exc)\n"
    "    sys.exit(3)\n"
    "print('reached')\n"
)


def _child(url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _CHILD, url],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_a_child_process_cannot_reach_the_internet() -> None:
    result = _child("https://pypi.org/simple/")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "reached" not in result.stdout


def test_a_child_process_still_reaches_a_loopback_server() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _child(f"http://127.0.0.1:{server.server_port}/")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "reached" in result.stdout
