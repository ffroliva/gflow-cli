"""#1184 spike ($0, read-only): after an Omni extend, what does the project listing hold?

Opens the project page, captures every ``Zzl0ze`` listing payload, and prints each
record that names the source media id or its workflow id: the record's ids, its
timestamps, and every number in it. A second version that names neither id would NOT be
printed: the full payload is saved to ``_spike_out/extend_1184_listing.json`` for that
check (the 2026-11-08 conclusion enumerated all of its records). Nothing is clicked or
typed.

Positive control: the source media id must appear in the listing at least once; if
it does not, the capture missed and the result says nothing.

Usage:
    python scripts/dev/spike_extend_1184_listing.py --profile ffroliva \
        --project <uuid> --media <uuid> --workflow <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from gflow_cli.api.transports.batchexecute import parse_frames
from gflow_cli.api.transports.migrated_composer import LISTING_RPC, MIGRATED_PROJECT_URL
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import get_settings

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
OUT = Path("scripts/dev/_spike_out/extend_1184_listing.json")


def records_naming(node: Any, ids: set[str], path: str = "") -> list[tuple[str, Any]]:
    """Smallest lists whose flattened text names one of ``ids``."""
    found: list[tuple[str, Any]] = []
    if isinstance(node, list):
        for i, child in enumerate(node):
            found += records_naming(child, ids, f"{path}/{i}")
        text = json.dumps(node)
        if not found and any(i in text for i in ids):
            found.append((path, node))
    return found


def summary(rec: Any) -> dict[str, Any]:
    text = json.dumps(rec)
    nums = [n for n in re.findall(r"(?<![\w.-])\d+(?:\.\d+)?(?![\w-])", text) if len(n) < 14]
    return {
        "uuids": sorted(set(UUID.findall(text))),
        "timestamps": re.findall(r"\b1[67]\d{8}\b", text),
        "numbers": nums[:40],
        "len": len(text),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", required=True)
    ap.add_argument("--workflow", required=True)
    a = ap.parse_args()
    payloads: list[Any] = []

    async def on_response(response: Any) -> None:
        if LISTING_RPC not in str(response.url):
            return
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001 - a lost body is just a missed sample
            return
        payloads.extend(p for rid, p in parse_frames(text) if rid == LISTING_RPC)

    transport = UiAutomationTransport()
    await transport.setup(get_settings().profile_subdir(a.profile))
    page = transport._page  # noqa: SLF001
    assert page is not None
    page.on("response", on_response)
    try:
        await page.goto(
            MIGRATED_PROJECT_URL.format(project_id=a.project),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        await page.wait_for_timeout(15_000)
    finally:
        await transport.teardown()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payloads), encoding="utf-8")
    ids = {a.media, a.workflow}
    hits = [h for p in payloads for h in records_naming(p, ids)]
    print("listing payloads:", len(payloads), "records naming the clip:", len(hits))
    for path, rec in hits:
        print(path, json.dumps(summary(rec)))
    print("positive control (source id seen):", any(a.media in json.dumps(p) for p in payloads))


if __name__ == "__main__":
    asyncio.run(main())
