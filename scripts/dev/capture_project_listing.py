#!/usr/bin/env python3
r"""Recon — dump join stats for one project's ``flow.projectInitialData`` listing (#543).

Formalizes the 2026-08-16 spike behind `gflow data sync --names`: fetches the
listing through the production primitive (``FlowApiClient.fetch_project_listing``
— context APIRequestContext GET, no navigation, credit-free) and runs the
production parser over it, printing:

- media count / workflows count / named-pair count
- pagination-marker keys found anywhere in the payload (none expected — a hit
  means the complete-listing assumption is broken and ghost-marking must stop)
- dropped count (harvested ids failing strict UUID validation)
- the first 3 (uuid -> displayName) pairs

Usage:
    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\capture_project_listing.py \
        --profile denon82 --project <project-uuid> [--json-out out.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts" / "dev"))

from _spike_common import build_client, resolve_profile_dir, step  # noqa: E402

from gflow_cli.services.catalog_sync import (  # noqa: E402
    PAGINATION_MARKER_KEYS,
    parse_project_listing,
)


def _marker_keys_found(node: Any, depth: int = 0) -> set[str]:
    """Collect which pagination-marker keys hold truthy values, recursively."""
    found: set[str] = set()
    if depth > 16:
        return found
    if isinstance(node, dict):
        for key, value in node.items():
            if key in PAGINATION_MARKER_KEYS and value:
                found.add(key)
            found |= _marker_keys_found(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            found |= _marker_keys_found(item, depth + 1)
    return found


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="gflow auth profile name")
    parser.add_argument("--project", required=True, help="Flow project UUID")
    parser.add_argument("--json-out", type=Path, default=None, help="save raw payload here")
    args = parser.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    async with build_client(profile_dir) as client:
        step("fetch", f"flow.projectInitialData for project {args.project}")
        payload = await client.fetch_project_listing(args.project)

    # Parse FIRST: a bad envelope dies with the parser's clear ValueError,
    # not a bare KeyError from the raw indexing below.
    parsed = parse_project_listing(payload)
    contents = payload["result"]["data"]["json"]["projectContents"]
    markers = sorted(_marker_keys_found(payload))

    step("media", f"{len(contents.get('media') or [])} items")
    step("workflows", f"{len(contents.get('workflows') or [])} items")
    step("named-pairs", str(len(parsed.names)))
    step("markers", ", ".join(markers) if markers else "none (complete listing)")
    step("dropped", str(parsed.dropped))
    for media_id, name in list(parsed.names.items())[:3]:
        step("pair", f"{media_id} -> {name!r}")

    if args.json_out is not None:
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        step("saved", str(args.json_out))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
