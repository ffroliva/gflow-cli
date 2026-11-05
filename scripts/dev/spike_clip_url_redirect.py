r"""Which URL does a finished clip's record carry, and where does it lead? $0.

    python scripts/dev/spike_clip_url_redirect.py --profile <p> --project <uuid>

The lost-reply e2e for #1141 reached status 3, and then the download got HTTP 302 from
`lh3.googleusercontent.com`, which `fetch_mp4` refuses by design (`max_redirects=0`). This
reads the project listing and walks each done record's URL one hop at a time. Query strings
are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any
from urllib.parse import urljoin, urlsplit

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports import batchexecute as be
from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.auth import profile_dir


def records(node: Any) -> list[list[Any]]:
    if not isinstance(node, list):
        return []
    if be._is_record(node):  # noqa: SLF001
        return [node]
    return [r for child in node for r in records(child)]


def short(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.hostname}{parts.path[:24]}"


async def walk(page: Any, url: str) -> None:
    hop = url
    for _ in range(6):
        resp = await page.request.get(hop, max_redirects=0, timeout=60_000)
        kind = resp.headers.get("content-type")
        next_hop = resp.headers.get("location")
        print(f"    {resp.status} {short(hop)} type={kind}")
        if not next_hop:
            body = await resp.body()
            print(f"    end ftyp={body[4:8] == b'ftyp'} bytes={len(body)}")
            return
        hop = urljoin(hop, next_hop)
    print("    still redirecting after 6 hops")


async def run(profile: str, project: str) -> None:
    async with FlowApiClient(profile_dir=profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001
        assert page is not None
        await page.goto(mc.MIGRATED_PROJECT_URL.format(project_id=project))
        listing = await mc.MigratedComposer()._read_listing(page, project, budget_s=30)  # noqa: SLF001
        rows = records(listing)
        done = [r for r in rows if be._url(be._at(r, 7, 0, 8))]  # noqa: SLF001
        print(f"records: {len(rows)}, with a video url: {len(done)}")
        for rec in done[:2]:
            print(f"status={be._at(rec, 5, 8, 0)}")
            await walk(page, be._url(be._at(rec, 7, 0, 8)) or "")  # noqa: SLF001


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.profile, args.project))
