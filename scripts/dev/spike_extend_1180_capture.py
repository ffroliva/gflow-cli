"""#1180 spike ($0): what does the extend page send between the click and ``fZytfe``?

Logs every ``batchexecute`` the production ``run_extend`` lets through: its URL
``rpcids``, the id of every ``f.req`` frame, and every snake_case model-key-shaped
token in the body. A context route aborts ANY request that carries ``fZytfe``
(URL or body), so nothing is billed. Positive control: the run must log one
``fZytfe`` (the e2e's own capture saw it); if it does not, the capture missed and
the result says nothing.

Usage:
    python scripts/dev/spike_extend_1180_capture.py --profile ffroliva \
        --project <uuid> --media <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any

from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.api.transports import migrated_extend as mx
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import get_settings
from gflow_cli.errors import GFlowError

KEYISH = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}\b")
LOG: list[dict[str, Any]] = []


async def capture(route: Any, request: Any) -> None:
    body = mc.request_body(request)
    ids = mx.batch_rpcids(mx.url_rpcids(str(request.url)), body)
    LOG.append(
        {
            "url_rpcids": mx.url_rpcids(str(request.url)),
            "frames": [str(mx._at(f, 0)) for f in mx._frames(body)],  # noqa: SLF001
            "keys": sorted(set(KEYISH.findall(body))),
        }
    )
    if mx.EXTEND_SUBMIT_RPC in ids:
        await route.abort()
        return
    await route.fallback()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", required=True)
    a = ap.parse_args()
    transport = UiAutomationTransport()
    await transport.setup(get_settings().profile_subdir(a.profile))
    page = transport._page  # noqa: SLF001
    assert page is not None
    await page.context.route("**/batchexecute**", capture)
    try:
        await mx.run_extend(
            page,
            project_id=a.project,
            media_id=a.media,
            prompt="the camera keeps drifting forward, same light",
            aspect=None,
            poll_timeout_s=60.0,
        )
    except GFlowError as exc:
        print("run_extend ended:", type(exc).__name__)
    finally:
        await transport.teardown()
    for i, entry in enumerate(LOG):
        print(i, entry)
    hit = [e for e in LOG if mx.EXTEND_SUBMIT_RPC in e["url_rpcids"] + e["frames"]]
    print("positive control (fZytfe seen):", bool(hit))


if __name__ == "__main__":
    asyncio.run(main())
