r"""Gate 0 for the direct HTTP image transport: WAF escalation, latency, aspect and count slots.

Plan: docs/superpowers/plans/2026-11-22-direct-http-image-transport/PLAN.md (Gate 0).

The POST is the shape that would ship, not a replay:
  - frame built from scratch (no captured template; ``inner[4] = [uuid]``)
  - cookies from the profile's on-disk store, read WHILE Chrome holds the profile
  - ``at``/``f.sid``/``bl`` from flow_wire's own shell GET (``_shell_tokens``)
  - UA from ``flow_wire._user_agent`` (the profile's Chrome major version)
  - a fresh ``IMAGE_GENERATION`` reCAPTCHA token minted on the page for every POST
  - one POST per generation, 120 s budget, never retried (a sent ogiZ0b may be billed)
``flow_wire._execute_direct_rpc`` is NOT used: its 5 s total budget is shorter than an
image generation (16-20 s measured), and it maps a timeout to a fallback reason.

Phases (one warm FlowApiClient, one fresh project):
  G0.1  N consecutive direct POSTs. On the first non-generation, ONE UI generation runs as
        a control (separates WAF escalation from the daily image cap / a profile-wide
        refusal) and the direct loop stops.
  G0.2  N UI-transport generations, same prompt and model (NARWHAL -> wire BELUGA), timed
        on the same span as the direct arm: call -> image records. Download is timed
        separately for direct and not at all for UI (generate_image does not download).
  G0.3/4 route-aborted captures ($0) driven through the real UI transport: default,
        16:9, count=2, then default again to reset the sticky settings pane. Every ogiZ0b
        POST is kept, so "count=2" can be read as one POST with N entries, N POSTs, or a
        scalar slot.

Cost: up to 2N+1 images (daily image quota, zero credits). Captures are $0.

Pre-registered readings (written before the run):
  | Outcome                                   | Reading                                      |
  |-------------------------------------------|----------------------------------------------|
  | 10/10 direct generated                    | no escalation at N=10; Gate 0 WAF passes     |
  | first refusal at i>=5, UI control OK      | escalation threshold ~i on the direct path;  |
  |                                           | passes (>=5) but the threshold is a design   |
  |                                           | input (pacing / fallback)                    |
  | first refusal at i<5, UI control OK       | direct path is escalated early: STOP Gate 0  |
  | refusal and the UI control also refused   | profile-wide (cap / account), not the direct |
  |                                           | path: WAF question UNMEASURED, rerun later   |
  | disk cookie read fails with Chrome open   | design finding: production cannot read the   |
  |                                           | store while the browser holds it             |
  | HTTP 429 / 403 / UNUSUAL_ACTIVITY         | WAF signal; record status + reasons verbatim |
  | capture: aspect slot differs vs default   | that slot carries aspect; record its values  |
  | capture: no ogiZ0b for a setting          | unmeasured, not "no slot"                    |
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from gflow_cli.api.flow_wire import _shell_tokens, _user_agent  # noqa: E402
from gflow_cli.api.image import Aspect, GenerateImageRequest, ImageRef  # noqa: E402
from gflow_cli.api.recaptcha import TokenMinter, discover_site_key  # noqa: E402
from gflow_cli.api.transports.batchexecute import (  # noqa: E402
    image_records,
    parse_frames,
    rpc_errors,
)
from gflow_cli.auth.cookies import read_cookies_for  # noqa: E402

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

_ORIGIN = "https://flow.google.com"
_BATCHEXECUTE = f"{_ORIGIN}/_/AiSandboxAngularFrontend/data/batchexecute"
_RPCID = "ogiZ0b"
_MODEL = "BELUGA"
_ACTION = "IMAGE_GENERATION"
_PROMPT = "a lighthouse on a rocky coast under a stormy sky, ink and watercolor"

_records: list[dict[str, Any]] = []


def _log(tag: str, **data: Any) -> None:
    rec = {"ts": time.strftime("%H:%M:%S"), "tag": tag, **data}
    _records.append(rec)
    step(tag, json.dumps(data, default=str))


def _ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)


# Phase 2 probes (--direct-entries / --direct-aspect): the Gate 0 captures showed count is N
# entries in one POST (each with its own entry[3]) and aspect is entry[4] (2 = 9:16, 3 = 16:9).
_FRAME = {"entries": 1, "aspect": 2}


def build_frame(prompt: str, model: str, project_id: str, token: str) -> list[Any]:
    """The proven from-scratch ogiZ0b payload (spike ee7fa2d7)."""
    ctx: list[Any] = [None, 22, None, None, None, project_id, None, None, None, None, [token, 1]]
    entries = [
        [None, None, None, 1 + secrets.randbelow(2**31 - 1), _FRAME["aspect"], model, None,
         list(ctx), [[[prompt]]], None, None, None]
        for _ in range(_FRAME["entries"])
    ]
    return [None, entries, 1, ctx, [str(uuid.uuid4()).upper()]]


def _redact(node: Any) -> Any:
    """Replace long strings (reCAPTCHA tokens) with their length, recursively."""
    if isinstance(node, str) and len(node) > 300:
        return f"<redacted {len(node)} chars>"
    if isinstance(node, list):
        return [_redact(x) for x in node]
    return node


async def direct_once(
    i: int,
    *,
    minter: TokenMinter,
    project_id: str,
    tokens: Any,
    headers: dict[str, str],
) -> bool:
    t0 = time.perf_counter()
    token = await minter.mint(_ACTION)
    mint_ms = _ms(t0)
    payload = build_frame(_PROMPT, _MODEL, project_id, token)
    body = urlencode({
        "f.req": json.dumps([[[_RPCID, json.dumps(payload, separators=(",", ":")), None,
                              "generic"]]]),
        "at": tokens.at,
    })
    query = {"rpcids": _RPCID, "source-path": f"/project/{project_id}", "bl": tokens.bl,
             "f.sid": tokens.fsid, "hl": "en", "_reqid": str(1000 + secrets.randbelow(9000)),
             "rt": "c"}
    url = f"{_BATCHEXECUTE}?{urlencode({k: v for k, v in query.items() if v})}"
    post_headers = {
        **headers,
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
        "x-same-domain": "1", "origin": _ORIGIN, "referer": f"{_ORIGIN}/project/{project_id}",
        "sec-fetch-site": "same-origin", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty",
    }
    t1 = time.perf_counter()
    try:
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False,
                                     timeout=httpx.Timeout(120.0, connect=10.0)) as hx:
            resp = await hx.post(url, content=body, headers=post_headers)
    except httpx.HTTPError as exc:
        _log("direct", i=i, verdict="TRANSPORT_ERROR", error=type(exc).__name__,
             mint_ms=mint_ms, post_ms=_ms(t1), total_ms=_ms(t0))
        return False
    post_ms = _ms(t1)
    errors = rpc_errors(resp.text) if resp.status_code == 200 else []
    recs = []
    if resp.status_code == 200:
        for rid, data in parse_frames(resp.text):
            if rid == _RPCID:
                with contextlib.suppress(Exception):
                    recs.extend(image_records(rid, data))
    total_ms = _ms(t0)
    dl: dict[str, Any] = {}
    if recs and recs[0].image_url:
        t2 = time.perf_counter()
        try:  # a failed download must not lose the record of a generation that happened
            async with httpx.AsyncClient(timeout=30.0, trust_env=False) as hx:
                img = await hx.get(recs[0].image_url)
            dl = {"dl_ms": _ms(t2), "dl_status": img.status_code,
                  "dl_bytes": len(img.content), "magic": img.content[:4].hex()}
        except httpx.HTTPError as exc:
            dl = {"dl_ms": _ms(t2), "dl_error": type(exc).__name__}
    _log("direct", i=i, status=resp.status_code,
         verdict="GENERATED" if recs else ("REJECTED" if errors else "EMPTY"),
         images=len(recs), dims=[f"{r.dimensions[0]}x{r.dimensions[1]}" for r in recs],
         errors=[{"code": e.code, "reasons": list(e.reasons)} for e in errors],
         head=None if recs or resp.status_code == 200 else resp.text[:300],
         mint_ms=mint_ms, post_ms=post_ms, total_ms=total_ms, **dl)
    return bool(recs)


async def ui_once(tag: str, i: int, client: Any, project_id: str, **req_kw: Any) -> bool:
    t0 = time.perf_counter()
    try:
        img = await asyncio.wait_for(
            client.generate_image(project_id=project_id,
                                  req=GenerateImageRequest(prompt=_PROMPT, **req_kw)),
            timeout=300,
        )
    except Exception as exc:  # noqa: BLE001 - a spike records every failure class
        _log(tag, i=i, verdict="FAILED", error=type(exc).__name__, detail=str(exc)[:200],
             total_ms=_ms(t0))
        return False
    _log(tag, i=i, verdict="GENERATED", total_ms=_ms(t0),
         dims=f"{img.dimensions[0]}x{img.dimensions[1]}", aspect=img.aspect_ratio)
    return True


async def capture(label: str, client: Any, project_id: str, **req_kw: Any) -> None:
    page = client._page  # noqa: SLF001 - spike-only
    bodies: list[dict[str, Any]] = []

    async def on_route(route: Any) -> None:
        req = route.request
        # Run 2 measured: post_data is a strict utf-8 decode and raises on binary bodies.
        data = (req.post_data_buffer or b"").decode("utf-8", errors="replace")
        if _RPCID in req.url or _RPCID in data:
            # The request event fires before interception, so on_request may hold it already.
            if not any(b["data"] == data for b in bodies):
                bodies.append({"url_rpcids": parse_qs(req.url.split("?", 1)[-1]).get("rpcids"),
                               "data": data})
            await route.abort()
        else:
            await route.continue_()

    def on_request(req: Any) -> None:
        # Passive copy: when the UI transport installs its own submit route (the I2I guard),
        # it runs before ours and our abort never sees the request (measured 2026-11-22).
        if "batchexecute" in req.url and _RPCID in req.url:
            data = (req.post_data_buffer or b"").decode("utf-8", errors="replace")
            if not any(b["data"] == data for b in bodies):
                bodies.append({"url_rpcids": [_RPCID], "data": data, "observed": True})

    count = req_kw.pop("count", 1)
    page.on("request", on_request)
    await page.route("**/data/batchexecute**", on_route)
    try:
        with contextlib.suppress(Exception):
            # generate_image forces count=1 (_flow_generation.py:604); the batch entry
            # point is the one that reaches Flow's x{N} tab.
            await asyncio.wait_for(
                client.generate_images_batch(
                    project_id=project_id,
                    req=req_kw.pop("request", None) or GenerateImageRequest(prompt=_PROMPT, **req_kw),
                    count=count,
                ),
                timeout=90,
            )
    finally:
        await page.unroute("**/data/batchexecute**", on_route)
        page.remove_listener("request", on_request)
    posts = []
    for b in bodies:
        freq = json.loads(parse_qs(b["data"]).get("f.req", ["null"])[0] or "null")
        for call in (freq or [[]])[0]:
            if call and call[0] == _RPCID:
                inner = json.loads(call[1])
                posts.append(_redact(inner))
    _log("capture", label=label, count=count, request={k: str(v) for k, v in req_kw.items()},
         posts=len(posts), entries_per_post=[len(p[1]) for p in posts if isinstance(p[1], list)],
         frames=posts)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="denon82")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--ui-n", type=int, default=10)
    ap.add_argument("--skip-captures", action="store_true")
    ap.add_argument("--captures-only", action="store_true", help="skip G0.1/G0.2 (route-aborted captures cost $0; --capture-ref/--capture-mention spend image quota)")
    ap.add_argument("--project", default=None, help="reuse a project instead of creating one")
    ap.add_argument("--direct-entries", type=int, default=1, help="Phase 2: entries per POST")
    ap.add_argument("--direct-aspect", type=int, default=2, help="Phase 2: entry[4] value")
    ap.add_argument("--capture-aspects", default="", help="Phase 2: Aspect names, comma-separated")
    ap.add_argument("--capture-ref", action="store_true", help="Phase 2: capture an in-project I2I (spends 2 images of daily quota: a source image, and the referenced submit reaches Flow)")
    ap.add_argument("--capture-entity", default="", help="ID:NAME; capture an attached character (spends 1 image)")
    ap.add_argument("--capture-mention", default="", help="Phase 2: capture an @Name mention (spends 1 image: the referenced submit reaches Flow)")
    args = ap.parse_args()
    _FRAME.update(entries=args.direct_entries, aspect=args.direct_aspect)

    profile_dir = resolve_profile_dir(args.profile)
    out = default_out_path("spike_direct_image_gate0", ".jsonl")
    step("OUTPUT", str(out))
    try:
        # The UI arms and captures must drive the UI transport whatever GFLOW_CLI_TRANSPORT says.
        os.environ["GFLOW_CLI_TRANSPORT"] = "ui_automation"
        async with build_client(profile_dir, headless=False) as client:
            pid = args.project or (
                await client.create_project(title="gflow gate0 direct-image spike")
            ).project_id
            _log("project", project_id=pid)
            page = client._page  # noqa: SLF001
            await page.goto(f"{_ORIGIN}/project/{pid}", wait_until="networkidle", timeout=60_000)
            await asyncio.sleep(2)

            # Disk cookies read WHILE Chrome holds the profile (the production condition).
            # Run 1 (22:57Z) measured CookieStoreUnreadable here: Chrome holds the jar
            # exclusively (cookies.py:333). The live context is the only source then.
            try:
                cookies = read_cookies_for(profile_dir)
                _log("cookies", source="disk", count=len(cookies), with_chrome_open=True)
            except Exception as exc:  # noqa: BLE001
                ctx_cookies = await page.context.cookies([_ORIGIN])
                cookies = [(c["name"], c["value"]) for c in ctx_cookies]
                _log("cookies", source="browser_context", disk_error=type(exc).__name__,
                     count=len(cookies), with_chrome_open=True)
            ua = _user_agent(profile_dir)
            headers = {"user-agent": ua or "", "accept-language": "en-US,en;q=0.9",
                       "cookie": "; ".join(f"{n}={v}" for n, v in cookies)}
            async with httpx.AsyncClient(follow_redirects=False, trust_env=False,
                                         timeout=httpx.Timeout(15.0, connect=5.0)) as hx:
                tokens = await _shell_tokens(hx, profile_dir, headers)
            _log("shell", at_len=len(tokens.at), fsid=bool(tokens.fsid), bl=tokens.bl, ua=ua)

            await discover_site_key(page)
            minter = TokenMinter(page)

            if args.captures_only:
                args.n = args.ui_n = 0
            # G0.1 — N consecutive direct POSTs, UI control on the first refusal.
            for i in range(1, args.n + 1):
                if not await direct_once(i, minter=minter, project_id=pid, tokens=tokens,
                                         headers=headers):
                    await ui_once("ui_control", i, client, pid)
                    break

            # G0.2 — UI arm, same prompt/model/project.
            for i in range(1, args.ui_n + 1):
                await ui_once("ui", i, client, pid)

            # G0.3 / G0.4 — $0 captures through the real UI transport.
            if args.capture_entity:  # Flow's own frame for an attached character + plain prompt
                eid, name = args.capture_entity.split(":", 1)
                req = GenerateImageRequest(
                    prompt=_PROMPT, reference_entities=(eid,), reference_entity_names=(name,)
                )
                await capture("entity", client, pid, request=req)
                await capture("reset_default", client, pid)
            elif args.capture_mention:  # Phase 2 T2.2: Flow's own character-mention submit
                from gflow_cli.services.mentions import resolve_and_apply

                req = await resolve_and_apply(
                    client,
                    GenerateImageRequest(prompt=f"@{args.capture_mention} on a pier at sunset"),
                    path="image", project_id=pid, tool_specs=(), quiet=True,
                )
                _log("mention_resolved", entities=list(req.reference_entities),
                     names=list(req.reference_entity_names), prompt=req.prompt)
                await capture("mention", client, pid, request=req)
                await capture("reset_default", client, pid)
            elif args.capture_ref:  # Phase 2 T2.1: Flow's own in-project I2I submit frame
                made = await client.generate_image(
                    project_id=pid, req=GenerateImageRequest(prompt=_PROMPT)
                )
                _log("ref_source", media_id=made.media_name, has_display=bool(made.display_name))
                ref = ImageRef(
                    name=made.media_name, display_name=made.display_name or "", in_project=True
                )
                await capture("i2i_in_project", client, pid, refs=(ref,))
                await capture("reset_default", client, pid)
            elif args.capture_aspects:  # Phase 2: the entry[4] value of each remaining aspect
                for name in args.capture_aspects.split(","):
                    await capture(f"aspect_{name}", client, pid, aspect=Aspect[name])
                await capture("reset_default", client, pid)
            elif not args.skip_captures:
                await capture("default", client, pid)
                await capture("aspect_16_9", client, pid, aspect=Aspect.LANDSCAPE)
                await capture("count_2", client, pid, count=2)
                await capture("reset_default", client, pid)
    finally:
        with open(out, "w", encoding="utf-8") as f:
            for r in _records:
                f.write(json.dumps(r, default=str) + "\n")
        step("SAVED", str(out))


if __name__ == "__main__":
    asyncio.run(main())
