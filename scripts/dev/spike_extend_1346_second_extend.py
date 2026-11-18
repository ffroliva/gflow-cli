"""#1346 spike ($0): which workflow does the page continue when a clip is extended again?

Drives the production ``run_extend`` click path with its route guard replaced by a
recorder: every ``batchexecute`` the page sends is logged (rpc ids, and every UUID in
each frame's inner payload, never the body itself, which carries a page-minted token),
and any request carrying ``fZytfe`` is aborted, so nothing is billed. Every ``Zzl0ze``
reply is kept; afterwards each UUID the ``fZytfe`` request names is looked up in those
listings and the record holding it is summarised (its slot-0 workflow id, slot-2 media
id, and created/modified timestamps). The full listings go to the gitignored
``_spike_out/extend_1346_listing.json``.

Positive control: the run must log one ``fZytfe`` and one listing holding the media id.

Usage:
    python scripts/dev/spike_extend_1346_second_extend.py --profile <name> \
        --project <uuid> --media <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.api.transports import migrated_extend as mx
from gflow_cli.api.transports.batchexecute import find_record, parse_frames
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import get_settings
from gflow_cli.errors import GFlowError

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
OUT = Path("scripts/dev/_spike_out/extend_1346_listing.json")
SENT: list[dict[str, Any]] = []
LISTINGS: list[Any] = []


def _record(rpcids: list[str], body: str, **_: Any) -> None:
    frames = {
        str(mx._at(f, 0)): sorted(set(UUID.findall(str(mx._at(f, 1)))))  # noqa: SLF001
        for f in mx._frames(body)  # noqa: SLF001
    }
    entry = mx._at(mx._inner(mx.EXTEND_SUBMIT_RPC, body), 0, 0)  # noqa: SLF001
    SENT.append(
        {
            "rpcids": rpcids,
            "frame_uuids": frames,
            "fZytfe_slot0": mx._at(entry, 0) if entry is not None else None,  # noqa: SLF001
        }
    )


def recording_verdict(rpcids: list[str], body: str, **kw: Any) -> None:
    _record(rpcids, body, **kw)
    return None


def no_problem(*_: Any, **__: Any) -> None:
    return None


async def abort_submit(route: Any, rpcid: str | None) -> None:
    if rpcid == mx.EXTEND_SUBMIT_RPC:
        await route.abort()
        return
    await route.fallback()


def holders(node: Any, uid: str) -> list[Any]:
    """Records (lists whose slot 0 is a UUID) whose text names ``uid``, smallest first."""
    found: list[Any] = []
    if isinstance(node, list):
        for child in node:
            found += holders(child, uid)
        if not found and node and isinstance(node[0], str) and UUID.fullmatch(node[0]):
            if uid in json.dumps(node):
                found.append(node)
    return found


def summary(rec: Any, uid: str) -> dict[str, Any]:
    text = json.dumps(rec)
    return {
        "slot0": rec[0],
        "slot2": rec[2] if len(rec) > 2 else None,
        "uid_is_slot0": rec[0] == uid,
        "uuids": sorted(set(UUID.findall(text))),
        "timestamps": re.findall(r"\b1[67]\d{8}\b", text),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--media", required=True)
    a = ap.parse_args()
    mx._request_verdict = recording_verdict  # type: ignore[assignment]  # noqa: SLF001
    mx.batch_request_problem = no_problem  # type: ignore[assignment]
    mx._release = abort_submit  # type: ignore[assignment]  # noqa: SLF001
    transport = UiAutomationTransport()
    await transport.setup(get_settings().profile_subdir(a.profile))
    page = transport._page  # noqa: SLF001
    assert page is not None

    async def on_response(response: Any) -> None:
        if mc.LISTING_RPC in str(response.url):
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001
                return
            LISTINGS.extend(p for rid, p in parse_frames(text) if rid == mc.LISTING_RPC)

    page.on("response", on_response)
    try:
        await asyncio.wait_for(
            mx.run_extend(
                page,
                project_id=a.project,
                media_id=a.media,
                prompt="the camera keeps drifting forward, same light",
                aspect=None,
                poll_timeout_s=30.0,
            ),
            90,
        )
    except (GFlowError, TimeoutError) as exc:
        print("run_extend ended:", type(exc).__name__)
    finally:
        await transport.teardown()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(LISTINGS), encoding="utf-8")
    source = [mx.source_record(p, a.media) for p in LISTINGS]
    first = [r[0] for p in LISTINGS if (r := find_record(p, (2, a.media)))]
    print("Zzl0ze listings:", len(LISTINGS), "source records:", [s for s in source if s])
    print("first-listed workflow per listing (the pre-#1346 expectation):", first)
    for entry in SENT:
        print("sent:", entry["rpcids"], entry["frame_uuids"], "slot0:", entry["fZytfe_slot0"])
    submits = [e for e in SENT if mx.EXTEND_SUBMIT_RPC in e["rpcids"]]
    print("positive control (fZytfe seen):", bool(submits))
    for e in submits:
        for uid in e["frame_uuids"].get(mx.EXTEND_SUBMIT_RPC, []):
            recs = [r for p in LISTINGS for r in holders(p, uid)]
            print(f"uuid {uid}: {len(recs)} listing record(s)")
            for r in recs[:4]:
                print("   ", summary(r, uid))


if __name__ == "__main__":
    asyncio.run(main())
