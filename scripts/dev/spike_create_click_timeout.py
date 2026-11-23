r"""Does a Create click that times out escape ``rpc_reply`` unwrapped? ($0: no browser, offline)

Context: the largest crash group in the first telemetry week is
``TimeoutError <- <playwright> <- rpc_reply <- create_project``. ``rpc_reply`` wraps only the
``asyncio.wait_for`` timeout into ``TransportTimeoutError``; ``create_project`` passes
``button.click(timeout=15_000)`` as its action, and catches only ``RpcResponseUnavailableError``
and ``TransportTimeoutError``. This spike drives ``rpc_reply`` with a page stand-in whose action
raises Playwright's own ``TimeoutError``, the exception a real click timeout raises.

Pre-registered reading:

* escapes as ``playwright...TimeoutError``: the click timeout reaches the unhandled path
  (exit 1).
* escapes as ``TransportTimeoutError``: it is wrapped; the crash group needs another cause.
* Playwright's class subclasses the builtin ``TimeoutError``: ``wait_for``'s handler could
  catch it; re-read the frame.

This drives ``rpc_reply`` alone, so it reports the escape even after #1434: since then
``create_project`` catches the click timeout in ``_click_create`` before ``rpc_reply`` sees it.

What this does not show: why the button was not clickable, and whether a ``jHPbke`` request
left the page before the timeout. Those need a live capture at the moment of the timeout.

    uv run python scripts/dev/spike_create_click_timeout.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.transports._mc_common import rpc_reply


class _Page:
    url = "https://flow.google.com/"

    def on(self, event: str, handler: Any) -> None:
        del event, handler

    def remove_listener(self, event: str, handler: Any) -> None:
        del event, handler


async def _click_times_out() -> None:
    raise PlaywrightTimeoutError("Timeout 15000ms exceeded (locator.click)")


async def main() -> None:
    print(
        "Playwright TimeoutError subclasses builtin:",
        issubclass(PlaywrightTimeoutError, TimeoutError),
    )
    try:
        await rpc_reply(_Page(), "jHPbke", _click_times_out, timeout_s=5, write=True)  # pyright: ignore[reportArgumentType]
    except Exception as exc:  # noqa: BLE001 - the class that escapes is the observation
        print("escaped as:", f"{type(exc).__module__}.{type(exc).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
