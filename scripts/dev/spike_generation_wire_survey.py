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

WHY IMAGE FIRST, AND WHY VIDEO IS A SEPARATE QUESTION (`--mode video`). The image
arm ran first because `gflow image t2i` spends **zero Veo credits** (AGENTS.md cost
table: `e2e_image` = "zero credits, daily cap"). Its pre-registration justified that
with "if a push channel exists for progress it is overwhelmingly likely to be shared
by both" — and the result **weakened that reasoning rather than confirming it**.
Images turned out to use a single held response with no poll at all; video uses a
**poll loop** (`jwpduf`, `as29s`; `migrated_composer.py:169`). Different mechanisms,
so an absence on one is NOT an absence on the other. The video arm exists because the
image answer does not transfer, and it is the only thing here that costs credits.

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
    -> a fixed client timer, and it is FLOW'S page that owns it, not us. There is no
       push to find; the interval is a floor we observe, not a knob we hold.
  * Poll gaps collapse near completion
    -> something signalled the client. Find it before claiming push is absent.
  * Submit fails / quota exhausted / the composer never loads
    -> **UNMEASURED.** Not evidence of absence. Say so, name what would settle it,
       and do NOT fold the cell into a zero-WebSocket count. Survey #1 folded four
       `/about` cells into a "12/12" claim and its audit caught it; that mistake is
       not available twice.

PRE-REGISTERED READING, VIDEO ARM (`--mode video`) — written before any video data
existed, and committed before the run. The question is narrow on purpose: *does
anything arrive between `jwpduf` polls, and are the polls a client timer or a
reaction?* The image answer cannot settle it (see above), so only these readings
apply to the video path:

  * Poll gaps between consecutive `jwpduf` calls are UNIFORM
    -> a client-side timer, owned by FLOW'S page — `migrated_composer.py` only
       observes it and adds no traffic. Nothing signals the page, and the interval is
       a floor we read rather than a latency knob we hold. Measure the gaps between
       request DISPATCHES, not arrivals: arrival spacing carries network jitter and
       cannot separate a timer from a cadence that reacts to each reply.
  * Poll gaps COLLAPSE near completion (or one gap is far shorter than the rest)
    -> something told the client. Push is NOT absent on this path until that
       something is identified. Record what arrived immediately before the short gap.
  * ANY non-`batchexecute` traffic, WebSocket event, or streaming content-type lands
    BETWEEN two polls
    -> that is the candidate signal; report it with its offset from the neighbouring
       polls. An `ogads-pa.clients6.google.com` hit is the OneGoogle account bar and
       is NOT Flow (measured twice already) — name it and exclude it explicitly.
  * A `jwpduf` (or `as29s`) request is HELD IN FLIGHT for many seconds
    -> long-poll, i.e. server push by another name, and the same mechanism the image
       path uses. `in_flight_s` is what distinguishes this from a late reply; without
       dispatch timing the two are indistinguishable, which is why the image arm's
       instrument was rebuilt mid-spike.
  * Submit refused, credits short, composer never loads, or the run times out
    -> **UNMEASURED.** Not evidence of absence, and it must NOT be folded into a
       zero-WebSocket count. A re-run costs credits: ASK before spending a second
       generation rather than looping.

COST:
  * `--mode image` (default): zero Veo credits, one unit of the daily image quota.
  * `--mode video`: **REAL VEO CREDITS**, ~10 per run. `--runs` therefore defaults to
    1 in video mode and 2 in image mode; the model is pinned to `veo_3_1_lite` and
    the count to x1 (see the inline comment at the pin). Nothing is downloaded.
Nothing is created server-side beyond the media itself, which lands in the project
like any other.

WHAT THE EVIDENCE FILE CONTAINS. Never bodies — only sizes, content-types, rpcids
and timings. It DOES keep each URL's first 200 characters, and a Google URL can
carry live query material there (a `play.google.com/log?...auth=SAPISIDHASH...` was
captured on the video run). `scripts/dev/_spike_out/` is gitignored for exactly this
reason: the finding is shareable, the bytes that produced it are not.

USAGE — `--runs` defaults to 2 for image and 1 for video, so neither line needs it:
    python scripts/dev/spike_generation_wire_survey.py --profile ffroliva \
        --project c5550ed7-7b6e-43db-8cd3-4d56a74b1244
    python scripts/dev/spike_generation_wire_survey.py --profile ffroliva \
        --project c5550ed7-7b6e-43db-8cd3-4d56a74b1244 --mode video

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


async def run_once(client: Any, project_id: str, run: int, mode: str) -> dict[str, Any]:
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
        # Truncate to the SAME width the events use, or the lookup below silently
        # misses every long URL. It was [:300] against events at [:200], which left
        # 62 of the video run's responses -- including all 8 jwpduf polls -- with no
        # HTTP version attributed at all. `spike_two_domain_protocol_survey.py` is
        # NOT affected: it truncates both sides at 300, so its h2/h3 counts stand.
        protocols[str(resp.get("url", ""))[:200]] = str(resp.get("protocol", ""))

    cdp.on("Network.responseReceived", _proto)
    for name in (
        "Network.webSocketCreated",
        "Network.webSocketFrameSent",
        "Network.webSocketFrameReceived",
        "Network.webSocketHandshakeResponseReceived",
        "Network.eventSourceMessageReceived",
        # WebTransport: never looked for by ANY spike in this series until now, so its
        # absence had only ever been assumed. It is a distinct carrier from WebSocket
        # and would not have raised a single event above.
        "Network.webTransportCreated",
        "Network.webTransportConnectionEstablished",
        "Network.webTransportClosed",
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
            sent = sent_at.get(resp.request)
            if sent is not None:
                events[-1]["sent_t"] = sent
                events[-1]["in_flight_s"] = round(events[-1]["t"] - sent, 3)
            page_seen.add(resp.request)
        except Exception:  # noqa: BLE001 — a torn-down response is not a finding
            return

    def _on_websocket(ws: Any) -> None:
        websockets.append(
            {"t": round(time.monotonic() - t0, 3), "event": "pw.websocket", "url": ws.url}
        )

    # --- The blind-spot A/B -------------------------------------------------------
    # Everything above binds the PAGE. A service worker's fetches are reported on the
    # BrowserContext instead, and a `page.on` count therefore cannot distinguish "no
    # such traffic" from "traffic on a surface I never bound" -- which is precisely
    # what a zero-WebSocket claim rests on. So bind the context too and DIFF them:
    # whatever the context saw and the page did not IS the blind spot, measured rather
    # than argued. Requests are matched by object identity, the same property that made
    # `sent_at` correct.
    #
    # Not `Target.setAutoAttach`: flattened child sessions arrive with a sessionId
    # Playwright's connection does not route to a CDPSession, so the events would be
    # dropped silently -- a detector that looks armed and is not, which is the one
    # failure mode this whole series keeps guarding against. The context listener is
    # first-class API and needs no such trust.
    page_seen: set[Any] = set()
    ctx_rows: list[dict[str, Any]] = []
    workers: list[dict[str, Any]] = []

    def _on_ctx_response(resp: Any) -> None:
        try:
            url = resp.url
            ctx_rows.append(
                {
                    "t": round(time.monotonic() - t0, 3),
                    "req": resp.request,  # stripped before serialising
                    "status": resp.status,
                    "host": url.split("/")[2] if "//" in url else "",
                    "kind": _classify(resp.headers.get("content-type", "")),
                    "url": url[:200],
                }
            )
        except Exception:  # noqa: BLE001
            return

    def _note_worker(w: Any, scope: str) -> None:
        workers.append({"t": round(time.monotonic() - t0, 3), "scope": scope, "url": str(w.url)})

    def _on_worker(w: Any) -> None:
        _note_worker(w, "dedicated")

    def _on_sw(w: Any) -> None:
        _note_worker(w, "service")

    # Request DISPATCH time, not just response arrival. Without it a reply that lands
    # 32 s after submit is indistinguishable between "sent late" and "held open for
    # 32 s" -- and the second is a long-poll, i.e. server push by another name. The
    # first run of this spike could not tell them apart.
    #
    # Keyed on the Request OBJECT, not the URL. The image arm fired `ogiZ0b` exactly
    # once, so a url-keyed dict was harmless there; the VIDEO arm polls `jwpduf` for
    # minutes, and any two polls sharing a URL would overwrite each other and hand
    # back an `in_flight_s` belonging to a different request. Playwright hands the
    # same Request instance to `page.on("request")` and to `response.request`.
    sent_at: dict[Any, float] = {}

    def _on_request(req: Any) -> None:
        try:
            if "batchexecute" in str(req.url):
                sent_at[req] = round(time.monotonic() - t0, 3)
        except Exception:  # noqa: BLE001
            return

    page.on("request", _on_request)
    page.on("response", _on_response)
    page.on("websocket", _on_websocket)
    page.on("worker", _on_worker)
    context = page.context
    context.on("response", _on_ctx_response)
    context.on("serviceworker", _on_sw)

    error = None
    try:
        composer = mc.MigratedComposer()
        # Reload first: a previous run that died mid-flow can leave the settings pane
        # open, and the next run then fails inside apply_image_settings with "no
        # option groups" -- a dirty starting state masquerading as a fresh
        # observation. Replication is only replication from the same start.
        await page.goto("about:blank", wait_until="domcontentloaded", timeout=30_000)
        await composer.ensure_editor(page, project_id)
        events.append({"t": round(time.monotonic() - t0, 3), "marker": "editor_ready"})
        if mode == "video":
            from gflow_cli.api.video import GenerateVideoRequest, Mode, VideoModel

            # veo_3_1_lite (10 credits) and x1, pinned: with model=None the editor
            # submits on whatever tier it last remembered, possibly the 100-credit
            # one (#125). t2v so nothing is uploaded and the attach stages stay out
            # of the measurement.
            video = GenerateVideoRequest(
                prompt=PROMPT, mode=Mode.T2V, model=VideoModel.VEO_3_1_LITE, count=1
            )
            await composer.apply_video_settings(page, video)
            await composer.send_prompt(page, video.prompt)
            events.append({"t": round(time.monotonic() - t0, 3), "marker": "submit_begin"})
            # The page polls jwpduf/as29s on its own; this driver adds no traffic
            # (migrated_composer module docstring), so the cadence recorded here is
            # Flow's, not ours. Not downloaded: the clip stays in the project.
            result: Any = await composer.submit_and_observe(
                page, poll_timeout_s=600.0, on_started=None, project_id=project_id
            )
        else:
            from gflow_cli.api.image import GenerateImageRequest

            request = GenerateImageRequest(prompt=PROMPT)
            await composer.apply_image_settings(page, request)
            # send_prompt is NOT optional, and leaving it out is not a Flow finding.
            # `run_images` types the prompt before submitting; without it the submit
            # control is correctly disabled and the run dies as "submit stayed disabled",
            # which reads exactly like selector drift. Measured on the first attempt of
            # this very spike -- a selector that does not match is evidence about the
            # selector, and here it was evidence about the harness.
            await composer.send_prompt(page, request.prompt)
            events.append({"t": round(time.monotonic() - t0, 3), "marker": "submit_begin"})
            result = await composer.submit_images_and_observe(page, request)
        # The terminal fields BY NAME, because `str(result)[:120]` truncates a video
        # GenerationRecord before `status` / `is_done` / `size_bytes` -- so the run's
        # own outcome reached stdout and never the evidence file, and the findings
        # note ended up citing numbers the capture did not hold. Named fields also
        # keep the record's signed `video_url` out, which a wider `str()` slice would
        # have dragged in. Omitted entirely on the image arm, whose result is a list
        # and carries none of them: an all-null field reads like a failed read.
        terminal = {
            k: v
            for k in ("status", "is_done", "size_bytes", "media_id", "workflow_id")
            if (v := getattr(result, k, None)) is not None
        }
        done = {"t": round(time.monotonic() - t0, 3), "marker": "submit_done"}
        done["media"] = str(result)[:120]
        if terminal:
            done["terminal"] = terminal
        events.append(done)
    except Exception as exc:  # noqa: BLE001 — a failed submit is an OUTCOME, not a crash
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
        events.append({"t": round(time.monotonic() - t0, 3), "marker": "error", "detail": error})
    finally:
        # `finally`, not fall-through: `except Exception` does not catch Ctrl-C or a
        # cancellation, and the video arm holds this page for up to poll_timeout_s.
        # Without it an interrupt mid-poll returns the page to nobody and the next run
        # blocks forever in `_checkout_page`.
        #
        # Detach BEFORE snapshotting: the page is pooled, and a listener left attached
        # appends into THIS run's arrays (survey #1's evidence was corrupted exactly so).
        # The CONTEXT ones matter more, not less: the context outlives every page in the
        # pool, so one left behind leaks across runs AND across whatever the client does
        # next.
        page.remove_listener("request", _on_request)
        page.remove_listener("response", _on_response)
        page.remove_listener("websocket", _on_websocket)
        page.remove_listener("worker", _on_worker)
        context.remove_listener("response", _on_ctx_response)
        context.remove_listener("serviceworker", _on_sw)
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
    # Gaps PER rpcid, measured between DISPATCHES. Two corrections in one:
    #   * per rpcid, because "jwpduf every 5 s" is a claim about one rpcid's cadence,
    #     and an all-traffic gap list hides it whenever anything else interleaves;
    #   * dispatch, because arrival spacing carries the network jitter this question
    #     has to see past. Measured on the video run: the same eight polls read
    #     stdev 3.5 ms by dispatch and 178 ms by arrival, so an arrival-keyed list
    #     cannot separate a fixed timer from a cadence that reacts to each reply --
    #     which is the entire question. `sent_t` is absent only for a response whose
    #     request predates the listener, and those fall back to arrival.
    per_rpcid_gaps: dict[str, list[float]] = {}
    for rpcid in {str(e["rpcid"]) for e in polls}:
        ts = [e.get("sent_t", e["t"]) for e in polls if e["rpcid"] == rpcid]
        per_rpcid_gaps[rpcid] = [round(b - a, 3) for a, b in zip(ts, ts[1:], strict=False)]
    # The blind-spot diff. `context_only` is every response the BrowserContext reported
    # that the page listener never saw -- service-worker fetches being the class that
    # motivated this. An empty list is the first POSITIVE evidence that binding the page
    # was sufficient; a non-empty one is the traffic every earlier survey missed.
    context_only = [
        {k: v for k, v in row.items() if k != "req"}
        for row in ctx_rows
        if row["req"] not in page_seen
    ]
    # Third-party census: which non-Flow hosts this run talked to, and how often. Named
    # rather than counted one-off, because `ogads-pa.clients6.google.com` has now been
    # re-derived as "not Flow" three separate times across this series.
    #
    # Counted over page AND context-only rows. Page-scoped alone reproduces the very bug
    # `context_only` exists to expose: measured here, `play.google.com/log` reached the
    # context and not the page, so a page-scoped census silently omitted the host whose
    # identity was the open question.
    third_party: dict[str, int] = {}
    for e in [*events, *context_only]:
        host = str(e.get("host", ""))
        if host and "flow.google.com" not in host:
            third_party[host] = third_party.get(host, 0) + 1
    return {
        "run": run,
        "error": error,
        "measured": error is None,
        "duration_s": round(time.monotonic() - t0, 2),
        "websocket_events": len(websockets),
        "websocket_detail": websockets[:20],
        "workers": workers,
        "page_responses": len(events),
        "context_responses": len(ctx_rows),
        "context_only": context_only,
        "third_party_hosts": dict(sorted(third_party.items(), key=lambda kv: -kv[1])),
        "stream_hits": [e for e in events if e.get("kind") == "STREAM"],
        "binary_wire_hits": [e for e in events if e.get("kind") == "BINARY_WIRE"],
        "no_content_length_200s": [
            e for e in events if e.get("no_content_length") and e.get("status") == 200
        ],
        "rpcid_sequence": [(e["t"], e["rpcid"]) for e in polls],
        # The discriminator: a reply held open for tens of seconds is a long-poll,
        # not a late request. Anything over a few seconds is the interesting case.
        "in_flight": sorted(
            (
                (e["in_flight_s"], e["rpcid"], e["t"])
                # `is not None`, not truthiness: a reply that round-trips in under
                # 0.5 ms rounds to 0.0 and a falsy test drops it from the list.
                for e in polls
                if e.get("in_flight_s") is not None
            ),
            reverse=True,
        )[:8],
        "poll_gaps_s": gaps,
        "poll_gaps_by_rpcid_s": per_rpcid_gaps,
        "markers": [e for e in events if "marker" in e],
        "events": events,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument(
        "--runs",
        type=int,
        default=None,
        help="default: 2 in image mode, 1 in video mode (each video run spends credits).",
    )
    ap.add_argument(
        "--mode",
        choices=("image", "video"),
        default="image",
        help="image: daily quota, zero Veo credits. video: REAL VEO CREDITS (~10/run).",
    )
    args = ap.parse_args()

    # The default has to follow the mode, not the other way round. It was a flat 2,
    # so appending `--mode video` to the committed image invocation silently doubled
    # a real bill -- and the banner below computed that total and printed it rather
    # than preventing it. Replication defaults to cheap; more runs are opt-in.
    runs = args.runs if args.runs is not None else (1 if args.mode == "video" else 2)

    if args.mode == "video":
        print(
            f"!! --mode video spends REAL Veo credits: ~10 per run x {runs} run(s).",
            flush=True,
        )

    report: dict[str, Any] = {
        "profile": args.profile,
        "project": args.project,
        "mode": args.mode,
        "runs": [],
    }
    async with build_client(resolve_profile_dir(args.profile)) as client:
        for run in range(1, runs + 1):
            print(f"[run {run}] generating ({args.mode})…", flush=True)
            obs = await run_once(client, args.project, run, args.mode)
            report["runs"].append(obs)
            print(
                f"    measured={obs['measured']} ws={obs['websocket_events']} "
                f"stream={len(obs['stream_hits'])} polls={len(obs['rpcid_sequence'])} "
                f"gaps={obs['poll_gaps_s'][:6]} err={obs['error']}",
                flush=True,
            )

    dest = default_out_path(f"generation_wire_{args.mode}_{args.profile}", ".json")
    dest.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {dest}")
    return 0


raise SystemExit(asyncio.run(main()))
