"""E4 (#895): does Playwright's ``APIRequestContext.get(max_retries=…)`` actually retry an
ECONNRESET, and is its backoff charged to the *same* call's timeout budget?

Both questions were answered by reading the shipped driver (``coreBundle.js``,
``_sendRequestWithRetries``) and never measured. The whole #895 design rests on them:

* if it does **not** retry, we need the Python wrap after all;
* if the backoff is **not** inside the budget, ``max_retries=2`` on a 180 s call is a
  9-minute worst case holding ``_generate_lock`` — the failure mode the council flagged
  Critical.

Method: a local socket server that answers the first *N* connections with a TCP RST
(``SO_LINGER`` 0 + close) and serves a valid response afterwards. Three arms, so the
retry is proven by a **control** that fails, not by one green run
(`[[ab-control-before-shipping-a-fix]]`):

  A  fail once,  max_retries=2  -> expect SUCCESS   (the retry is doing something)
  B  fail once,  max_retries=0  -> expect FAILURE   (control: without it, arm A's pass is meaningless)
  C  fail always, max_retries=2, timeout=5s -> expect FAILURE, elapsed ~5 s not ~15 s

Writes nothing outside ``scripts/dev/_spike_out/`` (gitignored). No network, no account,
no credits — it never leaves localhost.

Run:  uv run python scripts/dev/spike_playwright_max_retries.py
"""

from __future__ import annotations

import asyncio
import json
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).parent / "_spike_out"
_BODY = b"gflow-spike-ok"
_OK = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: application/octet-stream\r\n"
    b"Content-Length: " + str(len(_BODY)).encode() + b"\r\n"
    b"Connection: close\r\n\r\n" + _BODY
)


class ResetServer:
    """Answers the first ``fail_times`` connections with a TCP RST, then serves 200 OK."""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.connections = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.port: int = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        self._sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except (TimeoutError, OSError):
                continue
            self.connections += 1
            n = self.connections
            try:
                conn.settimeout(2.0)
                try:
                    conn.recv(65536)  # let the client finish sending the request line
                except OSError:
                    pass
                if n <= self.fail_times:
                    # SO_LINGER with timeout 0 makes close() emit RST, not FIN.
                    # That is what surfaces as ECONNRESET on the peer.
                    conn.setsockopt(
                        socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                    )
                else:
                    conn.sendall(_OK)
            finally:
                conn.close()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        self._sock.close()


async def _arm(
    name: str, *, fail_times: int, max_retries: int, timeout_ms: float, expect: str
) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    server = ResetServer(fail_times=fail_times)
    server.start()
    url = f"http://127.0.0.1:{server.port}/clip.mp4"
    started = time.monotonic()
    outcome: str
    detail = ""
    status: int | None = None
    try:
        async with async_playwright() as p:
            ctx = await p.request.new_context()
            try:
                resp = await ctx.get(
                    url, max_retries=max_retries, timeout=timeout_ms
                )
                status = resp.status
                body = await resp.body()
                outcome = "SUCCESS" if body == _BODY else "WRONG_BODY"
            finally:
                await ctx.dispose()
    except Exception as exc:  # noqa: BLE001 - the spike records whatever surfaces
        outcome = "FAILURE"
        detail = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
    elapsed = time.monotonic() - started
    server.stop()
    return {
        "arm": name,
        "fail_times": fail_times,
        "max_retries": max_retries,
        "timeout_s": timeout_ms / 1000,
        "outcome": outcome,
        "expected": expect,
        "matches_expectation": outcome == expect,
        "status": status,
        "elapsed_s": round(elapsed, 2),
        "server_connections": server.connections,
        "detail": detail,
    }


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = [
        await _arm("A_retry_recovers", fail_times=1, max_retries=2, timeout_ms=10_000, expect="SUCCESS"),
        await _arm("B_control_no_retry", fail_times=1, max_retries=0, timeout_ms=10_000, expect="FAILURE"),
        await _arm("C_budget_shared", fail_times=99, max_retries=2, timeout_ms=5_000, expect="FAILURE"),
    ]

    a, b, c = results
    # Arm C is the budget question: 3 attempts at a 5 s timeout each would be ~15 s if the
    # timeout were per-attempt; ~5 s means the whole retry sequence shares one budget.
    c["verdict_budget"] = (
        "SHARED (retry stays inside one timeout)"
        if c["elapsed_s"] < 8
        else "PER-ATTEMPT (retry MULTIPLIES the timeout — do not use max_retries on a 180s call)"
    )
    conclusive = a["matches_expectation"] and b["matches_expectation"]
    summary = {
        "question": "does APIRequestContext max_retries retry ECONNRESET, inside one timeout budget?",
        "retry_works": a["outcome"] == "SUCCESS",
        "control_proves_it": b["outcome"] == "FAILURE",
        "conclusive": conclusive,
        "budget": c["verdict_budget"],
        "server_connections_arm_A": a["server_connections"],
        "results": results,
    }
    out = OUT_DIR / "e4_playwright_max_retries.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    for r in results:
        mark = "OK " if r["matches_expectation"] else "!! "
        print(
            f"{mark}{r['arm']:22} outcome={r['outcome']:8} expected={r['expected']:8} "
            f"elapsed={r['elapsed_s']:6.2f}s conns={r['server_connections']} {r['detail']}"
        )
    print()
    print(f"retry works ......... {summary['retry_works']}")
    print(f"control proves it ... {summary['control_proves_it']}")
    print(f"CONCLUSIVE .......... {conclusive}")
    print(f"budget .............. {c['verdict_budget']}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
