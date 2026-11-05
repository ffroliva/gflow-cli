r"""What did Flow record for the runs of ``spike_t2v_two_runs.py``? $0, read-only.

Prints each listed record whose prompt carries the spike's marker, and whether any client
UUID from the newest report appears in it, ignoring case.

    python scripts/dev/spike_t2v_two_runs_read.py <profile> <project>
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.transports import migrated_composer as mc  # noqa: E402
from gflow_cli.api.transports.batchexecute import _at, _is_record, parse_frames  # noqa: E402

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip

MARKER = "spike 1141 two runs"


def records(node: Any) -> list[list[Any]]:
    if not isinstance(node, list):
        return []
    if _is_record(node):
        return [node]
    return [r for child in node for r in records(child)]


async def run(profile: str, project: str) -> None:
    newest = max(
        glob.glob("scripts/dev/_spike_out/spike_t2v_two_runs_*.json"), key=os.path.getmtime
    )
    sent = json.load(open(newest, encoding="utf8"))["requests"]
    got: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

    async def on_response(resp: Any) -> None:
        if "batchexecute" not in resp.url or got.done():
            return
        try:
            text = await resp.text()
        except Exception:  # noqa: BLE001 - a lost body just means wait for the next reply
            return
        for rid, payload in parse_frames(text):
            if rid == mc.LISTING_RPC and not got.done():
                got.set_result(payload)

    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None
        page.on("response", on_response)
        await page.goto(mc.MIGRATED_PROJECT_URL.format(project_id=project))
        payload = await asyncio.wait_for(got, 45)
    for rec in records(payload):
        prompt = _at(rec, 5, 1)
        if not isinstance(prompt, str) or MARKER not in prompt:
            continue
        body = json.dumps(rec).lower()
        echoed = [
            [i[:8] for i in r["ids"] if i.lower() in body and not i.startswith(project[:8])]
            for r in sent
        ]
        print(
            json.dumps(
                {
                    "wf": rec[0][:8],
                    "status": _at(rec, 5, 8, 0),
                    "created": _at(rec, 5, 0, 0),
                    "prompt_len": len(prompt),
                    "markers": prompt.count(MARKER),
                    "echo_by_request": echoed,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1], sys.argv[2]))
