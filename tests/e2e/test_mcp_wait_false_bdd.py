"""E2E for #1097: an MCP ``wait=false`` image job runs, reads back, and dies cleanly.

Binds ``tests/features/mcp_wait_false.feature`` (``@e2e @e2e_image``), so it is selected by
``-m e2e_image`` — see ``docs/E2E_TESTING.md`` § BDD-bound e2e.

**Why an e2e.** The MCP queued path is its own surface (AGENTS.md, Iron Law). Offline
tests fake ``FlowApiClient`` and call the tool in-process. Only a real ``gflow mcp run``
over stdio shows that the job outlives its tool call, that ``tasks/get`` answers over
the wire with the finished result inline (SEP-2663, #1102), and that closing stdin
cancels a job holding a real Chrome.

**Cost.** Up to two images of daily quota, zero Veo credits.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from gflow_cli.config import get_settings, reset_settings
from gflow_cli.data.store import DataStore
from gflow_cli.worker.queue import QueueRepository
from tests.e2e._redacted_env import RedactedEnv

scenarios("../features/mcp_wait_false.feature")

_GEN_TIMEOUT_S = 300


class _StdioClient:
    """A minimal JSON-RPC client on a real ``gflow mcp run`` subprocess."""

    def __init__(self, stderr_log: Path) -> None:
        self.stderr_log = stderr_log
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "from gflow_cli.cli import main; main()", "mcp", "run"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_log.open("wb"),  # the server's events, for a failed run
            env=RedactedEnv({**os.environ, "PYTHONUTF8": "1"}),
        )
        self._lines: queue.Queue[dict[str, Any]] = queue.Queue()
        self._next_id = 0
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            try:
                self._lines.put(json.loads(raw))
            except json.JSONDecodeError:
                continue

    def _send(self, body: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(body) + "\n").encode())
        self.proc.stdin.flush()

    def request(self, method: str, params: dict[str, Any], timeout: float = 60) -> dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                msg = self._lines.get(timeout=max(0.1, end - time.monotonic()))
            except queue.Empty:
                break
            if msg.get("id") == msg_id:
                assert "result" in msg, msg  # a JSON-RPC error, shown whole
                return msg
        raise AssertionError(f"no reply to {method} within {timeout}s")

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "e2e-1097", "version": "0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def close(self) -> None:
        """Close stdin first so a job still running shuts its Chrome; kill only if stuck."""
        if self.proc.poll() is None:
            if self.proc.stdin is not None and not self.proc.stdin.closed:
                self.proc.stdin.close()
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _row_status(task_id: str) -> str:
    with DataStore.open(get_settings().resolved_db_path()) as store:
        task = QueueRepository(store).get_task(task_id)
    assert task is not None
    return task.status


def _browsers_on(profile_dir: Path) -> list[str]:
    """Command lines of running processes that hold ``profile_dir`` as Chrome's user data."""
    needle = str(profile_dir).lower()
    if sys.platform == "win32":
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }",
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    else:
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines() if "user-data-dir" in ln and needle in ln.lower()]


@pytest.fixture
def ctx(e2e_profile_dir: Path, tmp_path: Path) -> Any:
    reset_settings()
    state: dict[str, Any] = {
        "dir": e2e_profile_dir,
        "profile": e2e_profile_dir.name.removeprefix("profile_"),
        "tmp": tmp_path,
    }
    yield state
    client = state.get("client")
    if client is not None:
        client.close()


@given("a gflow mcp run server over stdio on the e2e profile")
def _server(ctx: dict[str, Any]) -> None:
    assert not _browsers_on(ctx["dir"]), "a browser already holds the e2e profile"
    ctx["client"] = client = _StdioClient(ctx["tmp"] / "server.stderr.log")
    client.initialize()


@when("the agent calls gflow_generate_image with wait false")
def _call(ctx: dict[str, Any]) -> None:
    reply = ctx["client"].request(
        "tools/call",
        {
            "name": "gflow_generate_image",
            "arguments": {
                "prompt": "a red lighthouse on a cliff at dusk, watercolor",
                "profile": ctx["profile"],
                "wait": False,
            },
        },
    )
    ctx["reply"] = reply["result"]["structuredContent"]


@then("the reply is pending with a task id")
def _pending(ctx: dict[str, Any]) -> None:
    assert ctx["reply"]["status"] == "pending", ctx["reply"]
    ctx["task_id"] = ctx["reply"]["task_id"]


@then("tasks/get eventually reports the task completed")
def _completed(ctx: dict[str, Any]) -> None:
    end = time.monotonic() + _GEN_TIMEOUT_S
    status = None
    got: dict[str, Any] = {}
    while time.monotonic() < end:
        got = ctx["client"].request("tasks/get", {"taskId": ctx["task_id"]})["result"]
        status = got["status"]
        if status != "working":
            break
        time.sleep(5)
    assert status == "completed", (
        f"last tasks/get status {status!r}; row {_row_status(ctx['task_id'])}"
    )
    assert "ttl" not in got and got["ttlMs"] is None, got  # SEP-2663 keys (#1102)
    ctx["task"] = got


@then("the completed task carries an image file that exists on disk")
def _result(ctx: dict[str, Any]) -> None:
    result = ctx["task"]["result"]
    assert result["isError"] is False, result
    payload = result["structuredContent"]
    assert payload["status"] == "completed", payload
    assert payload["files"], payload
    for path in payload["files"]:
        assert Path(path).is_file(), path
        assert Path(path).stat().st_size > 0, path


@when("the client closes the server once the job is running")
def _close_mid_job(ctx: dict[str, Any]) -> None:
    task_id = ctx["reply"]["task_id"]
    ctx["task_id"] = task_id
    end = time.monotonic() + 60
    while time.monotonic() < end and _row_status(task_id) != "processing":
        time.sleep(0.5)
    assert _row_status(task_id) == "processing", "the job never started"
    time.sleep(10)  # let the browser come up and get into the generation
    proc = ctx["client"].proc
    assert proc.stdin is not None
    ctx["closed_at"] = time.monotonic()
    proc.stdin.close()


@then("the server exits within 60 seconds")
def _exits(ctx: dict[str, Any]) -> None:
    ctx["client"].proc.wait(timeout=60)
    print(f"server exited {time.monotonic() - ctx['closed_at']:.1f}s after stdin closed")


@then("the job's row is no longer processing")
def _row_settled(ctx: dict[str, Any]) -> None:
    status = _row_status(ctx["task_id"])
    print(f"mid-job close left the row {status!r}")  # the evidence line, run with -s
    # "completed" would mean the job beat the close and the cancel path never ran.
    assert status in {"failed", "indeterminate"}, status


@then("no browser on the e2e profile is left running")
def _no_browser(ctx: dict[str, Any]) -> None:
    end = time.monotonic() + 15
    left = _browsers_on(ctx["dir"])
    while left and time.monotonic() < end:
        time.sleep(1)
        left = _browsers_on(ctx["dir"])
    assert not left, f"{len(left)} browser process(es) still hold the profile"
