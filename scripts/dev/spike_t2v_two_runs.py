r"""Is a t2v clip still identifiable with a same-prompt run in flight? (#1141) PAID.

Cost: TWO veo-lite clips. Predict required this before any fix: a lost t2v reply has no
uploaded id, so the plan matches prompt plus model on a non-terminal record.

Submits run A, waits, submits run B with the same prompt in the same project and page.
Bodies are read normally and nothing is aborted, so the replies are ground truth. Every
status poll is logged with the records it names. The submit request bodies are scanned
for UUIDs in any case, to see whether a client id is echoed by the record.

    python scripts/dev/spike_t2v_two_runs.py <profile> <project>

Pre-registered readings (2026-11-04, before the run):

| Observation | Reading |
|---|---|
| a client uuid from B's request appears only in B's record | exact key: use it, drop prompt matching |
| polls after B name A and B together, always | more than one match refuses: prompt match is safe, exit 9 |
| a poll after B names A alone, with B absent | prompt match can adopt the WRONG clip: needs a time window or stays exit 9 |
| created differs by the gap between submits | a two-sided window can separate them, sized by this gap |
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gflow_cli.api.transports import migrated_composer as mc  # noqa: E402
from gflow_cli.api.transports.batchexecute import _at, _is_record, parse_frames  # noqa: E402
from gflow_cli.api.video import GenerateVideoRequest, VideoModel  # noqa: E402

from _spike_common import build_client, default_out_path, resolve_profile_dir  # noqa: E402, isort: skip

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
GAP_S = 20
WATCH_S = 90
T0 = time.monotonic()
report: dict[str, Any] = {"events": [], "requests": [], "records": {}}


def ev(kind: str, **kw: Any) -> None:
    row = {"t": round(time.monotonic() - T0, 1), "kind": kind, **kw}
    report["events"].append(row)
    print(json.dumps(row)[:240], flush=True)


def records(node: Any) -> list[list[Any]]:
    if not isinstance(node, list):
        return []
    if _is_record(node):
        return [node]
    return [r for child in node for r in records(child)]


def row(rec: list[Any]) -> dict[str, Any]:
    return {
        "wf": rec[0][:8],
        "media": rec[2][:8],
        "status": _at(rec, 5, 8, 0),
        "created": _at(rec, 5, 0, 0),
    }


async def run(profile: str, project: str) -> None:
    prompt = f"A paper boat turning slowly on a still pond, spike 1141 two runs {int(time.time())}"
    composer = mc.MigratedComposer()
    request = GenerateVideoRequest(prompt=prompt, model=VideoModel[sys.argv[3]])

    async def on_response(resp: Any) -> None:
        if "batchexecute" not in resp.url:
            return
        try:
            text = await resp.text()
        except Exception as exc:  # noqa: BLE001 - a real loss is data here
            ev("body_unreadable", rpc=mc.url_rpcid(resp.url), error=type(exc).__name__)
            return
        for rid, payload in parse_frames(text):
            if rid not in {*mc.SUBMIT_RPCS, *mc.STATUS_RPCS}:
                continue
            found = records(payload)
            ours = [r for r in found if _at(r, 5, 1) == prompt]
            for r in ours:
                report["records"].setdefault(r[0], json.dumps(r))
            ev("rpc", rid=rid, all=len(found), ours=[row(r) for r in ours])

    def on_request(req: Any) -> None:
        if mc.url_rpcid(req.url) in mc.SUBMIT_RPCS:
            ids = sorted(set(UUID.findall(unquote(req.post_data or ""))))
            report["requests"].append({"rpc": mc.url_rpcid(req.url), "wall": time.time(), "ids": ids})
            ev("submit_sent", rpc=mc.url_rpcid(req.url), ids=len(ids))

    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument
        assert page is not None
        await composer.ensure_editor(page, project)
        await composer.apply_video_settings(page, request)
        page.on("response", on_response)
        page.on("request", on_request)
        for label in ("A", "B"):
            # The composer keeps the last prompt after a submit: without this B doubles it.
            await composer.clear_composer(page)
            await composer.send_prompt(page, prompt)
            submit = await composer._pre_submit_gate(page)  # noqa: SLF001 - dev instrument
            deadline = time.monotonic() + 30
            while not await submit.is_enabled() and time.monotonic() < deadline:
                await asyncio.sleep(0.5)
            await submit.click(timeout=5000)
            ev("clicked", run=label)
            await asyncio.sleep(GAP_S if label == "A" else WATCH_S)
    report["echo"] = [
        {"id": i[:8], "in_records": [wf[:8] for wf, body in report["records"].items() if i in body]}
        for r in report["requests"]
        for i in r["ids"]
    ]
    path = default_out_path("spike_t2v_two_runs", ".json")
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("ECHO " + json.dumps(report["echo"]), flush=True)
    print(f"report -> {path}", flush=True)


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1], sys.argv[2]))
