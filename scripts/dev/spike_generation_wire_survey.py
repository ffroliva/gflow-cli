r"""Does a PUSH channel appear during an ACTIVE generation? (zero credits, image quota)

    A PROTOCOL YOU NEVER LOOKED FOR IS NOT ABSENT.

Survey #1 (2026-09-14, `spike_two_domain_protocol_survey.py`) found no WebSocket, no
SSE, no gRPC and no raw protobuf on either host — but it only ever loaded ROOT and
IDLE pages, and said so in its own "NOT measured" section:

    "A WebSocket opened only during an active generation would not have been seen."

This closes exactly that gap, and nothing wider. The detector is the same one survey
#1 used, and it is not assumed to work: an audit A/B-validated it by pointing it at a
page that DOES open a socket (it fired), then isolated the one blind-spot class —
pages sending `COOP: same-origin` — and confirmed neither Flow host is in it
(flow.google.com sends `same-origin-allow-popups`, labs.google sends none).

WHY IMAGE AND NOT VIDEO. `gflow image t2i` spends **zero Veo credits** (AGENTS.md
cost table: `e2e_image` = "zero credits, daily cap"); video spends real credits for
the same answer to this question. If a push channel exists for generation progress it
is overwhelmingly likely to be shared by both — and if this spike finds one, THAT is
the moment to spend credits confirming it on the video path, not before.

WHAT IT MEASURES, across the whole submit->poll->download lifecycle:

  * WebSockets: created, handshake, frames sent/received (CDP `Network.webSocket*`)
  * streaming: `text/event-stream`, `application/grpc*`, `x-protobuf`, NDJSON,
    multipart, and any 200 that arrives without a `content-length`
  * the POLL CADENCE: every `batchexecute` rpcid, with wall-clock offsets. If the
    client polls on a fixed timer, that is a client-side loop; if the gaps collapse
    around a state change, something told it — and that something is worth finding.
  * negotiated HTTP version per request (h2/h3), which only CDP reports
  * request/response SIZES, never bodies: a capture carrying prompts and Bearer
    tokens must not become a habit (skills/spike/SKILL.md output rules)

PRE-REGISTERED READING — written before the run, and this file is committed BEFORE
the data exists, because survey #1 could not prove that ordering and said so:

  * Zero WS + zero stream across the full generation
    -> the polling in `migrated_composer.py` is the real mechanism, not a fallback.
       Stop speculating about push for this surface; optimising the poll is the only
       lever, and any future "we could subscribe instead" is refuted.
  * A WebSocket opens at any point
    -> gflow has never modelled a live channel. Record when it opens relative to
       submit, and whether the poll rpcids continue anyway (a channel the app opens
       but ignores is not a lever). This is a NEW capability lead.
  * A streaming content-type on a generation route
    -> progress may be readable without polling at all.
  * Poll gaps are uniform
    -> a fixed client timer; latency is ours to tune.
  * Poll gaps collapse near completion
    -> something signalled the client. Find it before claiming push is absent.
  * Submit fails / quota exhausted / the composer never loads
    -> **UNMEASURED.** Not evidence of absence. Say so, name what would settle it,
       and do NOT fold the cell into a zero-WebSocket count. Survey #1 folded four
       `/about` cells into a "12/12" claim and its audit caught it; that mistake is
       not available twice.

COST: one image generation. Zero Veo credits, one unit of the daily image quota.
No video. Nothing is created server-side beyond the image itself, which lands in the
project like any other.

USAGE:
    python scripts/dev/spike_generation_wire_survey.py --profile ffroliva \
        --project c5550ed7-7b6e-43db-8cd3-4d56a74b1244 --runs 2

Chrome starts through `FlowApiClient`, which takes the profile lease first. A
`ProfileLockedError` means the lease is working — wait, or use another profile;
never kill Chrome to clear it (skills/spike/SKILL.md, profile etiquette).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
)

BINARY_WIRE_MARKERS = (
    "application/grpc",
    "application/x-protobuf",
    "application/protobuf",
    "+proto",
)
STREAM_MARKERS = ("text/event-stream", "application/x-ndjson", "multipart/mixed")

#: A neutral prompt. Nothing about the subject matters here — only the wire does.
PROMPT = "a plain grey ceramic mug on a white table, soft daylight"


def _classify(ctype: str) -> str:
    low = ctype.lower()
    if any(m in low for m in BINARY_WIRE_MARKERS):
        return "BINARY_WIRE"
    if any(m in low for m in STREAM_MARKERS):
        return "STREAM"
    if "json" in low:
        return "json"
    return low.split(";")[0] or "unknown"


async def run_once(client: Any, project_id: str, run: int) -> dict[str, Any]:
    """Drive one real generation with the wire fully instrumented."""
    from gflow_cli.api.transports import migrated_composer as mc

    page = await client._checkout_page()  # type: ignore[attr-defined]  # noqa: SLF001
    t0 = time.monotonic()
    events: list[dict[str, Any]] = []
    websockets: list[dict[str, Any]] = []
    protocols: dict[str, str] = {}

    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Network.enable")

    def _proto(evt: dict[str, Any]) -> None:
        resp = evt.get("response", {})
        protocols[str(resp.get("url", ""))[:300]] = str(resp.get("protocol", ""))

    cdp.on("Network.responseReceived", _proto)
    for name in (
        "Network.webSocketCreated",
        "Network.webSocketFrameSent",
        "Network.webSocketFrameReceived",
        "Network.webSocketHandshakeResponseReceived",
        "Network.eventSourceMessageReceived",
    ):
        cdp.on(
            name,
            lambda e, _n=name: websockets.append(
                {"t": round(time.monotonic() - t0, 3), "event": _n}
            ),
        )

    def _on_response(resp: Any) -> None:
        try:
            url = resp.url
            headers = resp.headers
            ctype = headers.get("content-type", "")
            rpcid = ""
            if "batchexecute" in url:
                # rpcids ride in the query string; the body carries the prompt, so
                # only the id is recorded.
                for part in url.split("?", 1)[-1].split("&"):
                    if part.startswith("rpcids="):
                        rpcid = part.split("=", 1)[1]
            events.append(
                {
                    "t": round(time.monotonic() - t0, 3),
                    "status": resp.status,
                    "host": url.split("/")[2] if "//" in url else "",
                    "rpcid": rpcid,
                    "kind": _classify(ctype),
                    "content_type": ctype[:80],
                    "no_content_length": headers.get("content-length", "") == "",
                    "req_bytes": len(resp.request.post_data or ""),
                    "url": url[:200],
                }
            )
        except Exception:  # noqa: BLE001 — a torn-down response is not a finding
            return

    def _on_websocket(ws: Any) -> None:
        websockets.append(
            {"t": round(time.monotonic() - t0, 3), "event": "pw.websocket", "url": ws.url}
        )

    page.on("response", _on_response)
    page.on("websocket", _on_websocket)

    error = None
    try:
        composer = mc.MigratedComposer()
        await composer.ensure_editor(page, project_id)
        events.append({"t": round(time.monotonic() - t0, 3), "marker": "editor_ready"})
        from gflow_cli.api.image import GenerateImageRequest

        request = GenerateImageRequest(prompt=PROMPT)
        events.append({"t": round(time.monotonic() - t0, 3), "marker": "submit_begin"})
        await composer.apply_image_settings(page, request)
        result = await composer.submit_images_and_observe(page, request)
        events.append(
            {
                "t": round(time.monotonic() - t0, 3),
                "marker": "submit_done",
                "media": str(result)[:120],
            }
        )
    except Exception as exc:  # noqa: BLE001 — a failed submit is an OUTCOME, not a crash
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
        events.append({"t": round(time.monotonic() - t0, 3), "marker": "error", "detail": error})

    # Detach BEFORE snapshotting: the page is pooled, and a listener left attached
    # appends into THIS run's arrays (survey #1's evidence was corrupted exactly so).
    page.remove_listener("response", _on_response)
    page.remove_listener("websocket", _on_websocket)
    try:
        await cdp.detach()
    except Exception:  # noqa: BLE001
        pass
    client._checkin_page(page)  # type: ignore[attr-defined]  # noqa: SLF001

    events = list(events)
    for e in events:
        if "url" in e:
            e["protocol"] = protocols.get(e["url"], "")

    polls = [e for e in events if e.get("rpcid")]
    gaps = [round(b["t"] - a["t"], 3) for a, b in zip(polls, polls[1:], strict=False)]
    return {
        "run": run,
        "error": error,
        "measured": error is None,
        "duration_s": round(time.monotonic() - t0, 2),
        "websocket_events": len(websockets),
        "websocket_detail": websockets[:20],
        "stream_hits": [e for e in events if e.get("kind") == "STREAM"],
        "binary_wire_hits": [e for e in events if e.get("kind") == "BINARY_WIRE"],
        "no_content_length_200s": [
            e for e in events if e.get("no_content_length") and e.get("status") == 200
        ],
        "rpcid_sequence": [(e["t"], e["rpcid"]) for e in polls],
        "poll_gaps_s": gaps,
        "markers": [e for e in events if "marker" in e],
        "events": events,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()

    report: dict[str, Any] = {"profile": args.profile, "project": args.project, "runs": []}
    async with build_client(resolve_profile_dir(args.profile)) as client:
        for run in range(1, args.runs + 1):
            print(f"[run {run}] generating…", flush=True)
            obs = await run_once(client, args.project, run)
            report["runs"].append(obs)
            print(
                f"    measured={obs['measured']} ws={obs['websocket_events']} "
                f"stream={len(obs['stream_hits'])} polls={len(obs['rpcid_sequence'])} "
                f"gaps={obs['poll_gaps_s'][:6]} err={obs['error']}",
                flush=True,
            )

    dest = default_out_path(f"generation_wire_{args.profile}", ".json")
    dest.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {dest}")
    return 0


raise SystemExit(asyncio.run(main()))
