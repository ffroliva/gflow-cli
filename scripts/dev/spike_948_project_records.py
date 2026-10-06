r"""#948 follow-up: what does a FINISHED record look like now? ($0 — navigation only)

A fixed t2v (slot-3 null accepted) parsed the submit, then never saw a terminal record
in 600 s. This opens the project (and, if found, the clip's route), captures every
``batchexecute`` frame for a while, and prints each list that contains the target
workflow id as a skeleton (UUIDs labelled consistently, so equal ids are visible).

PRE-REGISTERED READING:
  * the target's record shows status 3 + a URL, shape matching ``_is_record``
    -> the record exists; the run missed it because nothing POLLED it (driver issue)
  * the target appears in a list ``_is_record`` rejects
    -> the done-record shape changed too; the skeleton is the fixture
  * the target never appears
    -> unmeasured; open its clip route by media id instead

    python scripts/dev/spike_948_project_records.py --profile P --project ID --workflow W
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, resolve_profile_dir, step  # noqa: E402, isort: skip

from gflow_cli.api.transports.batchexecute import (  # noqa: E402
    _UUID_RE,  # pyright: ignore[reportPrivateUsage]
    _is_record,  # pyright: ignore[reportPrivateUsage]
    _walk_lists,  # pyright: ignore[reportPrivateUsage]
    parse_frames,
)

_UUID = _UUID_RE


def skeleton(node: Any, labels: dict[str, str]) -> Any:
    if isinstance(node, list):
        return [skeleton(c, labels) for c in node]  # type: ignore[misc]
    if isinstance(node, str):
        if _UUID.match(node):
            return labels.setdefault(node.lower(), f"<u{len(labels)}>")
        if node == "CAE":
            return node
        return "<url>" if node.startswith("https://") else "<str>"
    return node


async def _main(profile: str, project: str, workflow: str, wait_s: float) -> int:
    hits: list[tuple[str, bool, Any]] = []
    labels: dict[str, str] = {workflow.lower(): "<TARGET_WF>"}
    seen: list[str] = []
    media: dict[str, str] = {}

    async def on_response(response: Any) -> None:
        if "batchexecute" not in str(getattr(response, "url", "")):
            return
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001
            return
        for rid, payload in parse_frames(text):
            seen.append(rid)
            for node in _walk_lists(payload):
                if workflow.lower() in [x.lower() for x in node if isinstance(x, str)]:
                    if _is_record(node):
                        media["id"] = node[2]
                    hits.append((rid, _is_record(node), skeleton(node, labels)))

    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None
        page.context.on("response", on_response)
        await page.goto(f"https://flow.google.com/project/{project}")
        await asyncio.sleep(wait_s)
        if "id" in media:
            step("clip", "opening the clip route")
            await page.goto(f"https://flow.google.com/project/{project}/edit/{media['id']}")
            await asyncio.sleep(wait_s)

    step("rpcs", ", ".join(sorted(set(seen))) or "none")
    for rid, is_rec, sk in hits:
        step(f"{rid} is_record={is_rec}", json.dumps(sk)[:900])
    if not hits:
        step("result", "target workflow never appeared")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--wait", type=float, default=25.0)
    a = ap.parse_args()
    sys.exit(asyncio.run(_main(a.profile, a.project, a.workflow, a.wait)))
