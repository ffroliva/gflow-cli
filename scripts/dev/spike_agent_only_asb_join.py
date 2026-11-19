"""Spike (#1354): does a finished video tile's `/asb/` token name its `Zzl0ze` record?

Opens a project, records the composer verdict, reads the video tiles and chat options the
agent-only driver reads (`_VIDEO_JS`), captures the project listing the page load fires,
and prints, per tile and option, which listing record (if any) carries the same poster
token. $0: navigation and DOM reads only.

    uv run python scripts/dev/spike_agent_only_asb_join.py --profile NAME --project UUID

Prints counts and shortened ids only; no URL, token or cookie leaves the process.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from gflow_cli.api.transports._mc_common import LISTING_RPC
from gflow_cli.api.transports.agent_only_composer import _VIDEO_JS  # pyright: ignore[reportPrivateUsage]
from gflow_cli.api.transports.batchexecute import (
    asb_token,
    generation_record_by_poster,
    parse_frames,
)
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import get_settings


def _join(listing: Any, src: str) -> str:
    token = asb_token(src)
    if token is None:
        return "not /asb/"
    found = generation_record_by_poster(LISTING_RPC, listing, token) if listing else None
    if found is None:
        return "/asb/, no record"
    record, created = found
    return f"/asb/ -> {record.media_id[:8]} status={record.status} created={created}"


async def main(profile: str, project: str) -> None:
    transport = UiAutomationTransport()
    await transport.setup(get_settings().profile_subdir(profile))
    listings: list[Any] = []

    async def on_response(response: Any) -> None:
        if LISTING_RPC not in str(getattr(response, "url", "")):
            return
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001 - a lost body is reported as no listing
            return
        listings.extend(p for rid, p in parse_frames(text) if rid == LISTING_RPC)

    try:
        page = transport._page  # noqa: SLF001 - a spike reads the live page
        assert page is not None
        page.on("response", on_response)
        kind = await MigratedComposer().ensure_editor(page, project, timeout_s=45.0)
        await page.wait_for_timeout(6000)
        state = await page.evaluate(_VIDEO_JS)
        listing = listings[0] if listings else None
        cdn = sum(
            "flow-content.google" in s for t in state["finished"] for s in (t["video"], t["img"])
        )
        print(f"composer={kind} listings={len(listings)} pending={state['pending']}")
        print(f"finished tiles={len(state['finished'])} flow-content srcs={cdn}")
        for tile in state["finished"]:
            print(f"  tile img: {_join(listing, tile['img'])}")
        for option in state["options"]:
            print(f"  chat option: {_join(listing, option)}")
    finally:
        await transport.teardown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="#1354: join /asb/ tiles to listing records")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.profile, args.project))
