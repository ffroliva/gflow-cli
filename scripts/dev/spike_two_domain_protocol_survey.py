r"""What ACTUALLY differs between labs.google and flow.google.com, protocol by protocol? ($0)

    A SELECTOR THAT DOES NOT MATCH IS EVIDENCE ABOUT THE SELECTOR.
    A PROTOCOL YOU NEVER LOOKED FOR IS NOT ABSENT.

Every capability statement this repo makes about the two hosts rests on
`batchexecute` (9 spikes) and tRPC/REST. A keyword sweep of all 31 spike notes on
2026-09-14 found **zero** mentions of gRPC, gRPC-Web, raw protobuf bodies,
WebSockets, server-sent events, or the negotiated HTTP version. Those were never
ruled out; they were never looked for. This looks.

It also tests the standing mental model. The repo has been saying "migrated
cohort" as if an account were on one host or the other. Measured 2026-09-14, both
maintainer accounts hold a labs NextAuth session AND a flow.google.com session at
the same time, so that framing is already known to be wrong. The better hypothesis
is Google's own: labs is the incubator, flow.google.com is the graduated product,
and an uplift moves SURFACES independently rather than accounts wholesale.

Two observations already point at one backend behind both frontends, and neither
has been stated as a claim anyone could falsify:

  1. aisandbox-pa returns protobuf **Duration strings** ("8s", "3.226666870s")
     inside JSON — `api/scene.py:3`. That is what a JSON transcoding of a protobuf
     service looks like, not a hand-written JSON API.
  2. `application/json+protobuf` is **rejected 400** on the agentInfo PATCH
     (`api/client.py:1852`), which says the endpoint knows that content-type well
     enough to refuse it.

WHAT THIS SPIKE MEASURES, per (account x entry-point x run):

  * every request: host, path, method, status, **negotiated protocol** (h2/h3/
    http1.1), request+response content-type, body sizes, timing
  * WebSockets: opened at all? frames? (CDP `Network.webSocket*`)
  * streaming: any `text/event-stream`, `application/grpc*`, `application/x-proto*`,
    or chunked body without content-length
  * the handoff: does entering labs land on labs, or hand off to flow.google.com,
    and which host served the HTML
  * DOM fingerprint: framework markers and custom-element tag names, so "different
    frontend" is a measurement and not an inference

PRE-REGISTERED READING — written before the run, so the result cannot be respun:

* Both hosts: zero WS, zero grpc/proto content-types
    -> the wire is JSON-over-HTTPS on both. gRPC/websocket are ruled OUT for this
       surface and future work stops speculating about them.
  * Either host opens a WebSocket
    -> a live channel gflow has never modelled. Polling may be replaceable; this is
       a NEW capability lead, not a curiosity.
  * grpc-web or x-protobuf content-type on either host
    -> a binary wire exists beside the JSON one, the JSON path may be a fallback,
       and rpcid archaeology is the wrong abstraction.
  * Same rpcids/routes served to BOTH hosts
    -> one backend, two frontends: the uplift model. Capability differences are
       FRONTEND-only, so every "labs-only" claim is about selectors, not a service.
  * Disjoint routes per host
    -> two services. Per-host capability tables are legitimate and must stay.
  * labs entry hands off to flow.google.com
    -> that SURFACE is uplifted for this account. Record which surface, never "the
       account".
  * labs entry stays on labs
    -> labs is still served to this account; any "labs is gone" claim is false for it.
  * Run 1 != Run 2
    -> it flaps. A single-run capability claim is worthless; the matrix needs N>2.
  * A host is unreachable, or redirects to /about
    -> **UNMEASURED for that cell.** Not evidence of absence. Say so, and name what
       would settle it.

COST: navigation and DOM reads only. No generation, no entity creation, no submit.
$0 and zero quota. Nothing is created, so nothing needs deleting.

USAGE:
    python scripts/dev/spike_two_domain_protocol_survey.py --profile ffroliva --runs 2
    python scripts/dev/spike_two_domain_protocol_survey.py --profile denon82 --runs 2

Chrome is launched through `FlowApiClient`, which acquires the profile lease before
Chrome starts — never kill Chrome to clear a `ProfileLockedError`, wait or use
another profile (skills/spike/SKILL.md, profile etiquette).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
)

#: Entry points, one per host. The labs one is the pre-uplift route a user would
#: still have bookmarked; whether it survives is part of the question.
ENTRY_POINTS: dict[str, str] = {
    "labs_tools_flow": "https://labs.google/fx/tools/flow",
    "flow_root": "https://flow.google.com/",
}

#: Content-types that would mean a wire we have never modelled.
BINARY_WIRE_MARKERS = (
    "application/grpc",
    "application/x-protobuf",
    "application/protobuf",
    "+proto",
    "application/octet-stream",
)
STREAM_MARKERS = ("text/event-stream", "application/x-ndjson", "multipart/mixed")

#: Framework fingerprints. Presence is structural, never a display label.
DOM_FINGERPRINT_JS = r"""() => {
  const tags = {};
  for (const el of document.querySelectorAll('*')) {
    const t = el.tagName.toLowerCase();
    if (t.includes('-')) tags[t] = (tags[t] || 0) + 1;   // custom elements only
  }
  const top = Object.entries(tags).sort((a, b) => b[1] - a[1]).slice(0, 25);
  return {
    url: location.href,
    host: location.host,
    title_len: (document.title || '').length,
    total_elements: document.querySelectorAll('*').length,
    custom_elements: top,
    // Framework markers, structural only.
    angular: !!document.querySelector('[ng-version], .mat-mdc-button, mat-icon'),
    ng_version: (document.querySelector('[ng-version]') || {}).getAttribute
        ? document.querySelector('[ng-version]').getAttribute('ng-version') : null,
    react_root: !!document.querySelector('#__next, [data-reactroot]'),
    slate: !!document.querySelector("[data-slate-editor='true']"),
    prosemirror: !!document.querySelector('.ProseMirror'),
    ligature_i: document.querySelectorAll('i.google-symbols').length,
    ligature_mat: document.querySelectorAll('mat-icon').length,
    contenteditable: document.querySelectorAll("[contenteditable='true']").length,
    // Does the page hold a live channel open?
    has_ws_ctor_patched: typeof window.WebSocket === 'function',
  };
}"""


def _classify(ctype: str) -> str:
    low = ctype.lower()
    if any(m in low for m in BINARY_WIRE_MARKERS):
        return "BINARY_WIRE"
    if any(m in low for m in STREAM_MARKERS):
        return "STREAM"
    if "json" in low:
        return "json"
    if "html" in low:
        return "html"
    if "javascript" in low:
        return "js"
    return low.split(";")[0] or "unknown"


async def survey(client: Any, label: str, url: str) -> dict[str, Any]:
    """Navigate one entry point and record everything the wire showed."""
    page = await client._checkout_page()  # type: ignore[attr-defined]  # noqa: SLF001
    requests: list[dict[str, Any]] = []
    websockets: list[dict[str, Any]] = []
    cdp_protocols: dict[str, str] = {}

    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Network.enable")

    def _on_response_received(evt: dict[str, Any]) -> None:
        resp = evt.get("response", {})
        u = str(resp.get("url", ""))[:300]
        # `protocol` is the ONLY place the negotiated h2/h3 shows up; Playwright
        # does not expose it.
        cdp_protocols[u] = str(resp.get("protocol", ""))

    cdp.on("Network.responseReceived", _on_response_received)
    for ws_evt in (
        "Network.webSocketCreated",
        "Network.webSocketFrameSent",
        "Network.webSocketFrameReceived",
        "Network.webSocketHandshakeResponseReceived",
    ):
        cdp.on(ws_evt, lambda e, _n=ws_evt: websockets.append({"event": _n, "detail": e}))

    def _on_response(resp: Any) -> None:
        try:
            headers = resp.headers
            ctype = headers.get("content-type", "")
            requests.append(
                {
                    "method": resp.request.method,
                    "status": resp.status,
                    "url": resp.url[:300],
                    "host": resp.url.split("/")[2] if "//" in resp.url else "",
                    "resource_type": resp.request.resource_type,
                    "content_type": ctype,
                    "kind": _classify(ctype),
                    "content_length": headers.get("content-length", ""),
                    "req_content_type": resp.request.headers.get("content-type", ""),
                    "req_body_bytes": len(resp.request.post_data or ""),
                }
            )
        except Exception:  # noqa: BLE001 — a torn-down response is not a finding
            return

    page.on("response", _on_response)

    def _on_websocket(ws: Any) -> None:
        websockets.append({"event": "pw.websocket", "url": ws.url})

    page.on("websocket", _on_websocket)

    nav_error = None
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    except Exception as exc:  # noqa: BLE001 — an unreachable host is an outcome
        nav_error = f"{type(exc).__name__}: {str(exc)[:200]}"
    # Let the SPA mount and issue its own traffic; the handoff is client-side
    # (goto returns before it — memory: goto-returns-before-client-side-redirect).
    await asyncio.sleep(12)

    try:
        dom = await page.evaluate(DOM_FINGERPRINT_JS)
    except Exception as exc:  # noqa: BLE001
        dom = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}

    # Detach the listeners BEFORE snapshotting. The page comes from a pool and is
    # reused by the next observation, so a listener left attached keeps appending into
    # THIS observation's array -- which is how a labs.google 308 turned up in rows
    # whose navigation never touched labs.google, and how the survey's own findings
    # table came to report it. The derived aggregates were computed pre-leak and were
    # always clean; the raw list was not, and the raw list is what outlives us.
    page.remove_listener("response", _on_response)
    page.remove_listener("websocket", _on_websocket)

    landed = str(getattr(page, "url", ""))
    # Snapshot after detaching: an in-flight response can still land between the
    # remove_listener call and here, and a row with no protocol key breaks readers.
    requests = list(requests)
    for r in requests:
        r["protocol"] = cdp_protocols.get(r["url"], "")

    try:
        await cdp.detach()
    except Exception:  # noqa: BLE001
        pass
    client._checkin_page(page)  # type: ignore[attr-defined]  # noqa: SLF001

    hosts: dict[str, int] = {}
    kinds: dict[str, int] = {}
    protos: dict[str, int] = {}
    for r in requests:
        hosts[r["host"]] = hosts.get(r["host"], 0) + 1
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        protos[r.get("protocol") or "?"] = protos.get(r.get("protocol") or "?", 0) + 1

    return {
        "entry": label,
        "url_requested": url,
        "url_landed": landed,
        "handed_off": "flow.google.com" in landed and "labs.google" in url,
        "nav_error": nav_error,
        "request_count": len(requests),
        "hosts": dict(sorted(hosts.items(), key=lambda kv: -kv[1])),
        "content_kinds": kinds,
        "http_protocols": protos,
        "websocket_events": len(websockets),
        "websocket_detail": websockets[:10],
        "binary_wire_hits": [r for r in requests if r["kind"] == "BINARY_WIRE"],
        "stream_hits": [r for r in requests if r["kind"] == "STREAM"],
        "rpc_like": sorted(
            {
                r["url"].split("?")[0].rsplit("/", 1)[-1]
                for r in requests
                if "batchexecute" in r["url"] or "/trpc/" in r["url"] or "/v1/" in r["url"]
            }
        )[:40],
        "dom": dom,
        "requests": requests,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--runs", type=int, default=2, help="replication count (>=2)")
    args = ap.parse_args()

    report: dict[str, Any] = {"profile": args.profile, "runs": args.runs, "observations": []}
    profile_dir = resolve_profile_dir(args.profile)

    async with build_client(profile_dir) as client:
        for run in range(1, args.runs + 1):
            for label, url in ENTRY_POINTS.items():
                print(f"[run {run}] {label} -> {url}", flush=True)
                obs = await survey(client, label, url)
                obs["run"] = run
                report["observations"].append(obs)
                print(
                    f"    landed={obs['url_landed'][:70]} reqs={obs['request_count']} "
                    f"ws={obs['websocket_events']} binary={len(obs['binary_wire_hits'])} "
                    f"stream={len(obs['stream_hits'])} proto={obs['http_protocols']}",
                    flush=True,
                )

    dest = default_out_path(f"two_domain_protocol_{args.profile}", ".json")
    dest.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {dest}")
    return 0


raise SystemExit(asyncio.run(main()))
