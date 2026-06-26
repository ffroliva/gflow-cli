"""Subprocess-level smoke of the ``gflow mcp run`` stdio transport.

The in-process FastMCP client used elsewhere cannot catch stream-routing or
registration bugs: a real MCP client (Claude Desktop, Cursor) reads JSON-RPC
from the child's **stdout**. This test launches the actual CLI entry point and
asserts the protocol responses are delivered on stdout, and that tools and
prompts are both registered.

Regressions guarded:
- stdout-redirect bug — ``_redirect_stdout_to_stderr`` ran before FastMCP
  captured the protocol stream, sending all responses to stderr.
- prompts not registered — the startup path imported only tools and resources.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

_REQUESTS = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest-smoke", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "prompts/list"},
]


def test_mcp_run_serves_jsonrpc_on_stdout() -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "gflow_cli", "mcp", "run"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin and proc.stdout

    stdout_lines: list[str] = []
    reader = threading.Thread(
        target=lambda: stdout_lines.extend(proc.stdout),
        daemon=True,  # type: ignore[arg-type]
    )
    reader.start()

    def parsed_by_id() -> dict[int, dict]:
        out: dict[int, dict] = {}
        for line in list(stdout_lines):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"Non-JSON line on stdout (corrupts protocol): {line!r}"
                ) from exc
            if isinstance(msg.get("id"), int):
                out[msg["id"]] = msg
        return out

    try:
        for req in _REQUESTS:
            proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()

        # Keep stdin open and poll until every expected reply (ids 1-3) lands or
        # we hit the deadline — closing stdin too early races the server's EOF
        # shutdown against its last response.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if {1, 2, 3} <= parsed_by_id().keys():
                break
            time.sleep(0.1)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        reader.join(timeout=3)

    by_id = parsed_by_id()
    stderr_tail = (proc.stderr.read()[-300:]) if proc.stderr else ""
    ctx = f"stdout={stdout_lines!r} stderr-tail={stderr_tail!r}"

    assert 1 in by_id and "result" in by_id[1], f"No initialize response on stdout. {ctx}"
    result = by_id[1]["result"]
    assert "serverInfo" in result
    assert "capabilities" in result

    tools = by_id.get(2, {}).get("result", {}).get("tools", [])
    prompts = by_id.get(3, {}).get("result", {}).get("prompts", [])
    assert tools, f"No tools exposed over stdio. {ctx}"
    assert prompts, f"No prompts exposed over stdio (prompts module not registered). {ctx}"
