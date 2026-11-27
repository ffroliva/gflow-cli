r"""Spike (#1371): can video go over direct HTTP with the browser kept only to mint reCAPTCHA?

The direct POSTs reuse the production helpers of ``direct_http`` (live-browser cookies,
``flow_wire`` shell tokens, the project-page mint, the request builder), so a result
describes the request that would ship, not a hand replay.

Phases (each is its own flag; run the cheapest first):

  --capture   $0. Route-aborted captures of Flow's OWN t2v and i2v submits, driven through
              the UI transport (model omni_1_2_flash, 4 s, 16:9). Records the rpcid, the
              decoded frame (token redacted) and the reCAPTCHA action the page minted.
              Nothing leaves the browser, so nothing is billed. The i2v arm uploads its
              start frame (``maseQ``, $0, one asset left in the spike project).
  --reads     $0. ``as29s`` for an existing clip, sent two ways: the bare workflow id, and
              the app's ``[id, null, [1]]``. Records which URL hosts each reply carries.
  --upscale   $0 (1 observation: 2026-09-30-migrated-upscale-wire.md). One direct
              ``p0UkFb`` 1080p upscale of an existing clip with a ``VIDEO_GENERATION``
              token, then ``jwpduf`` polling, ``as29s`` and the mp4 download. Credits are
              read before and after.
  --spend     CREDITS. One direct t2v and one direct i2v submit (``YhhmEf`` / ``eb1hJf``),
              built from the frames --capture recorded, then poll, ``as29s`` and download.
              Defaults to the cheapest keys (Omni 1.2 Flash, 4 s, 360p: 4 credits each
              per HTrJv). Credits are read before and after each submit.

Pre-registered readings (written before any run):
  | Observation                                         | Reading                                 |
  |-----------------------------------------------------|-----------------------------------------|
  | capture: t2v on YhhmEf, i2v on eb1hJf, action        | hypothesis frame confirmed on this host |
  |   VIDEO_GENERATION                                   |                                         |
  | capture: a different rpcid / action / frame slot     | record verbatim; hypothesis frame wrong |
  | capture: no submit observed                          | UNMEASURED (drive failed), not "none" |
  | reads: bare id -> flow-content.google/video/<wf>     | direct mp4 URL without the page route   |
  | reads: both forms carry only lh3 /asb/ URLs          | bare-id claim does not reproduce here   |
  | upscale: record <wf>_upsampled, 1080p mp4, credits   | the whole direct video chain (mint,     |
  |   unchanged                                          | submit, poll, read, download) works, $0 |
  | upscale: HTTP 401/403 or UNUSUAL_ACTIVITY            | the direct VIDEO_GENERATION token is    |
  |                                                      | refused: record reasons; NO-GO signal   |
  | upscale: credits dropped                             | 1080p is billed on this account: STOP   |
  | spend: N/N submitted, rendered and downloaded        | GO for a hybrid video transport         |
  | spend: refused (code 3 / 7)                          | shape or token rejected: not billed;    |
  |                                                      | record and compare with the capture     |
  | spend: timeout / dropped connection after send       | may be billed: NEVER resent; reconcile  |
  |                                                      | in the project by hand                  |

Added 2026-11-27, before the --cold / --soak / second-account runs:
  | cold: headless ok = headed ok, 0 refusals            | a fresh headless browser's FIRST token  |
  |                                                      | is accepted: headless default holds     |
  | cold: headless refused, headed not                   | cold headless is scored lower: a warm   |
  |                                                      | page (or headed first mint) is required |
  | cold: both refused at similar rates                  | cold start, not headlessness, is the    |
  |                                                      | risk: warm page required in either mode |
  | soak: every iteration GENERATED for the full window  | a long-lived headless page keeps minting|
  |                                                      | and cookie rotation is absorbed         |
  | soak: failures begin after some age                  | record the age: the page/session needs  |
  |                                                      | a refresh interval                      |
  | any arm: a quota/cap reason, not UNUSUAL_ACTIVITY    | UNMEASURED for that arm, not a refusal  |
  | second account matches the first                     | not account-specific (2 of 2 accounts)  |

Added 2026-11-27 ~18:45, before the A/B, --concurrency / --idle / --image-models / portrait runs:
  | video A/B: headless refusals ~= headed refusals      | headless default holds for video too    |
  | video A/B: headless refused clearly more often       | video stays headed by default; headless |
  |                                                      | is the image default + opt-in for video |
  | concurrency N: N/N generated, no refusal             | one warm page can serve N in flight     |
  | concurrency N: refusals or 429 appear                | record the N where they start: the cap  |
  | idle 20 min (awake), next image generated            | idle alone needs no re-arm              |
  | idle 20 min, next image refused                      | re-arm after idle, not only after sleep |
  | image model REJECTED code 5 / 3                      | wire name or frame wrong for it: stays  |
  |                                                      | on the UI transport                     |
  | portrait t2v (aspect 1) renders 360x640              | portrait can go direct                  |
Every submit is sent at most once. Evidence -> scripts/dev/_spike_out/ (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import struct
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from gflow_cli.api import flow_wire  # noqa: E402
from gflow_cli.api.transports import direct_http as dh  # noqa: E402
from gflow_cli.api.transports.batchexecute import (  # noqa: E402
    find_record,
    parse_frames,
    rpc_errors,
)
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel  # noqa: E402

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

_ORIGIN = "https://flow.google.com"
_SUBMITS = ("YhhmEf", "eb1hJf", "MZZa6b", "nprQif")
_PROMPT = "a red paper boat drifting on a calm pond, morning light, static camera"
_START = Path(__file__).resolve().parents[2] / "docs/assets/social/og-image.png"
_records: list[dict[str, Any]] = []
_OUT: Path | None = None

# Records every grecaptcha.enterprise.execute action in the main world, across navigations.
_ACTION_HOOK = """(() => {
  window.__gflowActions = [];
  const t = setInterval(() => {
    const ent = window.grecaptcha && window.grecaptcha.enterprise;
    if (!ent || !ent.execute || ent.__gflowHooked) return;
    const orig = ent.execute.bind(ent);
    ent.execute = (k, o) => { window.__gflowActions.push(o && o.action); return orig(k, o); };
    ent.__gflowHooked = true;
  }, 50);
  setTimeout(() => clearInterval(t), 600000);
})();"""


def _log(tag: str, **data: Any) -> None:
    rec = {"ts": time.strftime("%H:%M:%S"), "tag": tag, **data}
    _records.append(rec)
    if _OUT is not None:  # appended as it happens: a killed run keeps its evidence
        with _OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    step(tag, json.dumps(data, default=str)[:600])


def _redact(node: Any) -> Any:
    if isinstance(node, str) and len(node) > 300:
        return f"<redacted {len(node)} chars>"
    if isinstance(node, list):
        return [_redact(x) for x in node]
    return node


def _hosts(text: str) -> list[str]:
    """Every distinct https host+path-prefix in a reply (no query: signatures stay out)."""

    out = set()
    for m in re.finditer(r"https://[^\"\\\s]+", text.encode().decode("unicode_escape", "ignore")):
        u = urlsplit(m.group(0))
        out.add(f"{u.hostname}{'/'.join(u.path.split('/')[:3])}")
    return sorted(out)


def _mp4_dims(data: bytes) -> tuple[int, int] | None:
    """Largest (w, h) among the mp4's tkhd boxes (16.16 fixed point at the box end)."""
    best: tuple[int, int] | None = None
    i = data.find(b"tkhd")
    while i > 0:
        size = struct.unpack_from(">I", data, i - 4)[0]
        end = i - 4 + size
        if end <= len(data) and size >= 92:
            w, h = struct.unpack_from(">II", data, end - 8)
            dims = (w >> 16, h >> 16)
            if best is None or dims[0] * dims[1] > best[0] * best[1]:
                best = dims
        i = data.find(b"tkhd", i + 4)
    return best


class Wire:
    """One live session: browser cookies + shell tokens; one send per call, never resent."""

    def __init__(self, page: Any, profile_dir: Path) -> None:
        self.page, self.profile_dir = page, profile_dir
        self.headers: dict[str, str] = {}
        self.tokens: Any = None
        self.hx = httpx.AsyncClient(
            follow_redirects=False, trust_env=False, timeout=httpx.Timeout(120.0, connect=10.0)
        )

    async def open(self) -> None:
        self.headers = await dh._session_headers(self.page, self.profile_dir)  # noqa: SLF001
        self.tokens = await dh._shell(self.hx, self.profile_dir, self.headers)  # noqa: SLF001
        _log(
            "session",
            cookies=self.headers["cookie"].count("="),
            at_len=len(self.tokens.at),
            bl=self.tokens.bl,
            ua=self.headers.get("user-agent"),
        )

    async def mint(self, project_id: str, action: str) -> tuple[str, int]:
        t0 = time.perf_counter()
        token = await dh._mint(self.page, project_id, action)  # noqa: SLF001
        return token, round((time.perf_counter() - t0) * 1000)

    async def rpc(
        self, rpcid: str, payload: Any, project_id: str, source_path: str | None = None
    ) -> tuple[int | None, str, int]:
        body = urlencode(
            {
                "f.req": json.dumps(
                    [[[rpcid, json.dumps(payload, separators=(",", ":")), None, "generic"]]]
                ),
                "at": self.tokens.at,
            }
        )
        req = dh._post_request(rpcid, project_id, self.tokens, self.headers, body)  # noqa: SLF001
        if source_path:
            req.url = req.url.copy_set_param("source-path", source_path)
        t0 = time.perf_counter()
        try:
            resp = await self.hx.send(req)
        except httpx.HTTPError as exc:
            return None, f"{type(exc).__name__}: {exc}", round((time.perf_counter() - t0) * 1000)
        if resp.status_code in (400, 401, 403):  # a rejected POST may mean a stale `at`
            flow_wire.forget_tokens(self.profile_dir)
        return resp.status_code, resp.text, round((time.perf_counter() - t0) * 1000)

    async def send_body(
        self, rpcid: str, body: str, project_id: str
    ) -> tuple[int | None, str, int]:
        req = dh._post_request(rpcid, project_id, self.tokens, self.headers, body)  # noqa: SLF001
        t0 = time.perf_counter()
        try:
            resp = await self.hx.send(req)
        except httpx.HTTPError as exc:
            return None, f"{type(exc).__name__}: {exc}", round((time.perf_counter() - t0) * 1000)
        if resp.status_code in (400, 401, 403):  # a rejected POST may mean a stale `at`
            flow_wire.forget_tokens(self.profile_dir)
        return resp.status_code, resp.text, round((time.perf_counter() - t0) * 1000)

    async def credits(self, project_id: str) -> int | None:
        status, text, _ = await self.rpc("nzlxg", [], project_id)
        for rid, data in parse_frames(text) if status == 200 else []:
            if rid == "nzlxg" and isinstance(data, list) and data:
                return int(data[0])
        return None


def _ctx(project_id: str, token: str) -> list[Any]:
    return [None, 22, None, None, None, project_id, None, None, None, None, [token, 1]]


def _summarise(
    rpcid: str, status: int | None, text: str, want_wf: str | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status}
    if status != 200:
        out["head"] = text[:300]
        return out
    out["errors"] = [{"code": e.code, "reasons": list(e.reasons)} for e in rpc_errors(text)]
    for rid, data in parse_frames(text):
        if rid != rpcid:
            continue
        rec = _find_wf(data, want_wf) if want_wf else find_record(data)
        if rec is None and not want_wf:
            # gflow's _UUID_RE is anchored, so a derived id (``<wf>_upsampled``) is not
            # record-shaped to it: look for any list whose slot 0 is a uuid plus a suffix.
            rec = _find_wf(data, None)
        if rec is not None:
            url = _video_url(rec)
            out.update(
                workflow_id=rec[0],
                media_id=rec[2],
                flow_status=_at(rec, 5, 8, 0),
                size=_at(rec, 5, 13),
                video_url_host=url and urlsplit(url).hostname,
            )
        else:
            out["skeleton"] = _skeleton(data)
    out["hosts"] = _hosts(text)
    return out


_DERIVED_RE = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}(_[a-z]+)?$")


def _at(node: Any, *path: int) -> Any:
    for i in path:
        if not isinstance(node, list) or len(node) <= i:
            return None
        node = node[i]
    return node


def _find_wf(node: Any, wf: str | None) -> list[Any] | None:
    """A record list: slot 0 is ``wf`` (or, with None, any uuid[_suffix]) and slot 5 a list."""
    if not isinstance(node, list):
        return None
    if (
        len(node) > 5
        and isinstance(node[0], str)
        and (node[0] == wf if wf else bool(_DERIVED_RE.match(node[0])))
        and isinstance(node[5], list)
    ):
        return node
    for child in node:
        if (found := _find_wf(child, wf)) is not None:
            return found
    return None


def _video_url(rec: list[Any]) -> str | None:
    m = re.search(r"https://flow-content\.google/video/[^\"\\\s]+", json.dumps(rec))
    return m.group(0).encode().decode("unicode_escape") if m else None


def _skeleton(node: Any, depth: int = 0) -> Any:
    """The reply's shape with URLs cut to their host and long strings to their length."""
    if isinstance(node, str):
        if node.startswith("https://"):
            return f"<url {urlsplit(node).hostname}>"
        return node if len(node) <= 80 else f"<str {len(node)}>"
    if isinstance(node, list):
        return [_skeleton(x, depth + 1) for x in node[:40]] if depth < 12 else "<deep>"
    return node


async def _poll(wire: Wire, project_id: str, wf: str, timeout_s: float = 600) -> dict[str, Any]:
    t0 = time.monotonic()
    last: dict[str, Any] = {}
    while time.monotonic() - t0 < timeout_s:
        await asyncio.sleep(5)
        status, text, _ = await wire.rpc("jwpduf", [None, None, [[wf]]], project_id)
        last = _summarise("jwpduf", status, text, wf)
        if last.get("flow_status") not in (None, 2, 6):
            break
    last["poll_s"] = round(time.monotonic() - t0, 1)
    return last


async def _read_and_download(wire: Wire, project_id: str, wf: str, label: str) -> None:
    status, text, ms = await wire.rpc("as29s", [wf], project_id)
    s = _summarise("as29s", status, text, wf)
    _log(f"{label}_as29s", ms=ms, **s)
    rec = None
    for rid, data in parse_frames(text) if status == 200 else []:
        if rid == "as29s":
            rec = rec or _find_wf(data, wf)
    url = _video_url(rec) if rec else None
    if not url:
        _log(f"{label}_download", outcome="no_url")
        return
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=60) as hx:
        r = await hx.get(url)
    _log(
        f"{label}_download",
        http=r.status_code,
        bytes=len(r.content),
        is_mp4=r.content[4:8] == b"ftyp",
        size_matches=_at(rec, 5, 13) == len(r.content),
        dims=_mp4_dims(r.content),
    )


# -- phases -------------------------------------------------------------------------------


def _abort_submits(seen: list[dict[str, Any]]) -> Any:
    """A route handler that records and ABORTS every video submit. Playwright passes
    ``(route, request)`` to a two-parameter handler: never give it a default argument
    (that bug let two "aborted" submits bill 8 credits on 2026-11-27)."""

    async def on_route(route: Any, _request: Any = None) -> None:
        r = route.request
        data = (r.post_data_buffer or b"").decode("utf-8", errors="replace")
        rid = (parse_qs(urlsplit(r.url).query).get("rpcids") or [""])[0]
        if rid in _SUBMITS or any(f'"{s}"' in data for s in _SUBMITS):
            await route.abort()  # first: nothing may stand between a submit and its abort
            seen.append(
                {"rpcids": rid, "query_keys": sorted(parse_qs(urlsplit(r.url).query)), "data": data}
            )
        else:
            await route.fallback()

    return on_route


async def phase_capture(client: Any, project_id: str) -> None:
    page = client._page  # noqa: SLF001 - spike-only
    for label, req in (
        (
            "t2v",
            GenerateVideoRequest(
                prompt=_PROMPT,
                mode=Mode.T2V,
                aspect=Aspect.LANDSCAPE,
                model=VideoModel.OMNI_1_2_FLASH,
                duration=4,
            ),
        ),
        (
            "i2v",
            GenerateVideoRequest(
                prompt=_PROMPT,
                mode=Mode.I2V,
                aspect=Aspect.LANDSCAPE,
                model=VideoModel.OMNI_1_2_FLASH,
                duration=4,
                start_image=_START,
            ),
        ),
    ):
        seen: list[dict[str, Any]] = []

        on_route = _abort_submits(seen)

        await page.evaluate("() => { window.__gflowActions = []; }")
        await page.route("**/data/batchexecute**", on_route)
        t0 = time.perf_counter()
        try:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    client.generate_video(
                        req=req, project_id=project_id, download=False, poll_timeout_s=20
                    ),
                    timeout=180,
                )
        finally:
            await page.unroute("**/data/batchexecute**", on_route)
        frames = []
        for b in seen:
            freq = json.loads(parse_qs(b["data"]).get("f.req", ["null"])[0] or "null")
            for call in (freq or [[]])[0]:
                if call and call[0] in _SUBMITS:
                    frames.append({"rpcid": call[0], "frame": _redact(json.loads(call[1]))})
        actions = await page.evaluate("() => window.__gflowActions || null")
        _log(
            f"capture_{label}",
            submits=len(seen),
            rpcids=[b["rpcids"] for b in seen],
            query_keys=seen[0]["query_keys"] if seen else None,
            actions=actions,
            frames=frames,
            s=round(time.perf_counter() - t0, 1),
        )


async def phase_reads(wire: Wire, project_id: str, wf: str) -> None:
    for label, payload in (("bare", [wf]), ("app_form", [wf, None, [1]])):
        status, text, ms = await wire.rpc("as29s", payload, project_id)
        _log(f"reads_{label}", ms=ms, **_summarise("as29s", status, text, wf))


async def phase_upscale(
    wire: Wire, project_id: str, wf: str, media_id: str, aspect: int, *, poll_only: bool
) -> None:
    before = await wire.credits(project_id)
    if poll_only:  # an earlier run already submitted it: read it, never submit again
        up = f"{wf}_upsampled"
        _log("upscale_poll", target=up, credits_now=before, **await _poll(wire, project_id, up))
        await _read_and_download(wire, project_id, up, "upscale")
        return
    # Control: the derived record must NOT exist before the submit, or a pass proves nothing.
    status, text, _ = await wire.rpc("jwpduf", [None, None, [[f"{wf}_upsampled"]]], project_id)
    pre = _summarise("jwpduf", status, text, f"{wf}_upsampled")
    # Measured 2026-11-27: an unknown derived id answers a stub (status 4, no media id).
    absent_stub = (
        status == 200
        and not pre.get("errors")
        and pre.get("flow_status") == 4
        and pre.get("media_id") is None
    )
    _log("upscale_precheck", absent_stub=absent_stub, **pre)
    if not absent_stub:  # an existing export, or an inconclusive reply: never submit on it
        _log("upscale_submit", outcome="skipped: precheck did not prove the record absent")
        return
    token, mint_ms = await wire.mint(project_id, "VIDEO_GENERATION")
    item: list[Any] = [None] * 32
    item[0] = [None, wf]
    item[2] = aspect
    item[4] = [None, media_id, None, None, str(uuid.uuid4()).upper()]
    item[6] = 2
    item[31] = "veo_3_1_upsampler_1080p"
    payload = [[item], _ctx(project_id, token), [str(uuid.uuid4()).upper()]]
    status, text, ms = await wire.rpc(
        "p0UkFb", payload, project_id, source_path=f"/project/{project_id}/edit/{media_id}"
    )
    s = _summarise("p0UkFb", status, text)
    _log("upscale_submit", mint_ms=mint_ms, post_ms=ms, credits_before=before, **s)
    up = s.get("workflow_id")
    if status != 200 or not up:  # refused or unknown: never resent, but record the balance
        _log("upscale_credits", before=before, after=await wire.credits(project_id))
        return
    _log("upscale_poll", **await _poll(wire, project_id, up))
    await _read_and_download(wire, project_id, up, "upscale")
    _log("upscale_credits", before=before, after=await wire.credits(project_id))


async def phase_spend(
    wire: Wire,
    project_id: str,
    mode: str,
    key: str,
    start: str | None,
    crop: list[Any] | None,
    aspect: int = 2,
) -> None:
    before = await wire.credits(project_id)
    token, mint_ms = await wire.mint(project_id, "VIDEO_GENERATION")
    text_seg = [None, None, [[[_PROMPT]]]]
    ids = [None, None, None, None, str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()]
    if mode == "t2v":
        rpcid, item = "YhhmEf", [text_seg, key, aspect, None, ids]
    else:
        rpcid = "eb1hJf"
        item = [text_seg, key, aspect, None, [None, start, None, None, None, crop], ids]
    payload = [[item], _ctx(project_id, token), [str(uuid.uuid4()).upper(), 2]]
    status, text, ms = await wire.rpc(rpcid, payload, project_id)
    s = _summarise(rpcid, status, text)
    _log(
        f"spend_{mode}_submit",
        key=key,
        aspect=aspect,
        mint_ms=mint_ms,
        post_ms=ms,
        credits_before=before,
        **s,
    )
    if status != 200 or not s.get("workflow_id"):  # refused or unknown: never resent
        _log(f"spend_{mode}_credits", before=before, after=await wire.credits(project_id))
        return
    _log(f"spend_{mode}_poll", **await _poll(wire, project_id, s["workflow_id"]))
    await _read_and_download(wire, project_id, s["workflow_id"], f"spend_{mode}")
    _log(f"spend_{mode}_credits", before=before, after=await wire.credits(project_id))


def _crop(size: tuple[int, int], aspect_w: int = 16, aspect_h: int = 9) -> list[Any]:
    """Centred crop ``[top, left, bottom, right]`` (fractions; 0 as null, 1 as 1)."""
    w, h = size
    target, src = aspect_w / aspect_h, w / h
    if src > target:
        f = target / src
        box = [0.0, (1 - f) / 2, 1.0, (1 + f) / 2]
    else:
        f = src / target
        box = [(1 - f) / 2, 0.0, (1 + f) / 2, 1.0]
    return [None if v == 0 else (1 if v == 1 else round(v, 6)) for v in box]


async def phase_images(client: Any, profile_dir: Path, project_id: str, n: int) -> None:
    """N direct image generations through the production ``run_direct_images`` (quota, $0).

    The token-quality probe: run once headed and once headless on the same profile."""
    from gflow_cli.api.image import GenerateImageRequest, Model

    req = GenerateImageRequest(
        prompt="a lighthouse on a rocky coast, ink and watercolor", model=Model.NARWHAL
    )
    ok = 0
    for i in range(1, n + 1):
        t0 = time.perf_counter()
        try:
            imgs = await dh.run_direct_images(
                page=client._page,
                profile_dir=profile_dir,  # noqa: SLF001
                project_id=project_id,
                request=req,
            )
            ok += 1
            _log(
                "image",
                i=i,
                verdict="GENERATED",
                images=len(imgs),
                s=round(time.perf_counter() - t0, 1),
            )
        except Exception as exc:  # noqa: BLE001 - a spike records every failure class
            _log(
                "image",
                i=i,
                verdict="FAILED",
                error=type(exc).__name__,
                detail=str(exc)[:300],
                s=round(time.perf_counter() - t0, 1),
            )
    _log("images_summary", ok=ok, n=n)


async def _one_cold(profile_dir: Path, project_id: str | None, headless: bool, i: int) -> str:
    """A fresh browser whose FIRST token is used at once: launch, one direct image, close."""
    t0 = time.perf_counter()
    async with build_client(profile_dir, headless=headless) as client:
        launched = round(time.perf_counter() - t0, 1)
        if project_id is None:
            project_id = (await client.create_project(title="gflow spike cold mint")).project_id
            _log("project", project_id=project_id)
        before = len(_records)
        await phase_images(client, profile_dir, project_id, 1)
        rec = _records[before]
        _log(
            "cold",
            i=i,
            headless=headless,
            launch_s=launched,
            verdict=rec.get("verdict"),
            error=rec.get("error"),
            detail=rec.get("detail"),
            image_s=rec.get("s"),
        )
    return project_id


async def run_cold(profile_dir: Path, project_id: str | None, n: int) -> None:
    """N rounds, alternating which mode goes first (ABBA) so drift does not favour one."""
    tally = {True: 0, False: 0}
    for i in range(1, n + 1):
        order = (True, False) if i % 2 else (False, True)
        for headless in order:
            try:
                project_id = await _one_cold(profile_dir, project_id, headless, i)
                tally[headless] += _records[-1].get("verdict") == "GENERATED"
            except Exception as exc:  # noqa: BLE001 - a launch failure is a finding too
                _log(
                    "cold",
                    i=i,
                    headless=headless,
                    verdict="LAUNCH_FAILED",
                    error=type(exc).__name__,
                    detail=str(exc)[:300],
                )
    _log("cold_summary", n=n, headless_ok=tally[True], headed_ok=tally[False])


async def run_soak(
    client: Any, profile_dir: Path, project_id: str, minutes: float, every_s: float
) -> None:
    """One warm session for MINUTES: a direct image every EVERY_S seconds (session re-read
    each time, as production does), the cookie names that rotate, and the age of the page."""
    page = client._page  # noqa: SLF001
    t_end = time.monotonic() + minutes * 60
    t_start = time.monotonic()
    prev: dict[str, str] = {}
    i = ok = 0
    while time.monotonic() < t_end:
        i += 1
        jar = {c["name"]: c["value"] for c in await page.context.cookies()}
        rotated = sorted(k for k in jar if prev and prev.get(k) not in (None, jar[k]))
        prev = jar
        before = len(_records)
        await phase_images(client, profile_dir, project_id, 1)
        ok += _records[before].get("verdict") == "GENERATED"
        _log(
            "soak",
            i=i,
            age_min=round((time.monotonic() - t_start) / 60, 1),
            verdict=_records[before].get("verdict"),
            error=_records[before].get("error"),
            rotated_cookies=rotated,
            wall=time.strftime("%H:%M:%S"),
            image_s=_records[before].get("s"),
        )
        await asyncio.sleep(max(0.0, every_s - (_records[before].get("s") or 0)))
    _log("soak_summary", iterations=i, ok=ok, minutes=minutes)


async def run_concurrency(client: Any, profile_dir: Path, project_id: str, n: int) -> None:
    """N direct images in flight at once on ONE page (each request mints its own token)."""
    from gflow_cli.api.image import GenerateImageRequest, Model

    req = GenerateImageRequest(
        prompt="a lighthouse on a rocky coast, ink and watercolor", model=Model.NARWHAL
    )

    async def one(i: int) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            imgs = await dh.run_direct_images(
                page=client._page,  # noqa: SLF001
                profile_dir=profile_dir,
                project_id=project_id,
                request=req,
            )
            return {
                "i": i,
                "verdict": "GENERATED",
                "images": len(imgs),
                "s": round(time.perf_counter() - t0, 1),
            }
        except Exception as exc:  # noqa: BLE001 - a spike records every failure class
            return {
                "i": i,
                "verdict": "FAILED",
                "error": type(exc).__name__,
                "detail": str(exc)[:300],
                "s": round(time.perf_counter() - t0, 1),
            }

    t0 = time.perf_counter()
    rows = await asyncio.gather(*(one(i) for i in range(1, n + 1)))
    for r in rows:
        _log("concurrent", **r)
    ok = sum(r["verdict"] == "GENERATED" for r in rows)
    _log(
        "concurrency_summary",
        n=n,
        ok=ok,
        wall_s=round(time.perf_counter() - t0, 1),
        per_min=round(ok / (time.perf_counter() - t0) * 60, 1),
    )


async def run_idle(client: Any, profile_dir: Path, project_id: str, minutes: float) -> None:
    """One image, MINUTES of idle with the machine kept awake, then one image again."""
    await phase_images(client, profile_dir, project_id, 1)
    _log("idle_start", minutes=minutes, wall=time.strftime("%H:%M:%S"))
    await asyncio.sleep(minutes * 60)
    _log("idle_end", wall=time.strftime("%H:%M:%S"))
    await phase_images(client, profile_dir, project_id, 1)


async def run_image_models(wire: Wire, project_id: str, models: list[str]) -> None:
    """One direct ogiZ0b per raw wire model name (the frame direct_http ships, model swapped)."""
    from gflow_cli.api.transports.batchexecute import image_records
    from gflow_cli.api.transports.frame_builder import build_ogiz0b_body

    for model in models:
        token, mint_ms = await wire.mint(project_id, "IMAGE_GENERATION")
        body = build_ogiz0b_body(
            prompt="a lighthouse on a rocky coast, ink and watercolor",
            model=model,
            project_id=project_id,
            recaptcha_token=token,
            at_token=wire.tokens.at,
            batch_id=str(uuid.uuid4()).upper(),
            entry_seeds=(1 + int.from_bytes(os.urandom(4), "big") % (2**31 - 2),),
        )
        status, text, ms = await wire.send_body("ogiZ0b", body, project_id)
        recs: list[Any] = []
        for rid, data in parse_frames(text) if status == 200 else []:
            if rid == "ogiZ0b":
                with contextlib.suppress(Exception):
                    recs.extend(image_records(rid, data))
        errs = (
            [{"code": e.code, "reasons": list(e.reasons)} for e in rpc_errors(text)]
            if status == 200
            else []
        )
        _log(
            "image_model",
            model=model,
            status=status,
            mint_ms=mint_ms,
            post_ms=ms,
            verdict="GENERATED" if recs else ("REJECTED" if errs else "EMPTY"),
            images=len(recs),
            dims=[f"{r.dimensions[0]}x{r.dimensions[1]}" for r in recs],
            errors=errs,
            head=None if status == 200 else text[:200],
        )


async def run_cross_project(wire: Wire, page: Any, home: str, other: str, n: int) -> None:
    """Mint on a page parked on project HOME, submit an image to project OTHER (no navigation)."""
    from gflow_cli.api._engine import mint_evaluate_kwargs
    from gflow_cli.api.recaptcha import TokenMinter
    from gflow_cli.api.transports.batchexecute import image_records
    from gflow_cli.api.transports.frame_builder import build_ogiz0b_body
    from gflow_cli.errors import RecaptchaError

    if home == other:
        _log("cross_project", verdict="REFUSED: --cross-project must name a different project")
        return
    minter = TokenMinter(page, mint_evaluate_kwargs=mint_evaluate_kwargs())
    for i in range(1, n + 1):
        parked = page.url
        t0 = time.perf_counter()
        deadline = time.monotonic() + 10  # the enterprise script may still be loading
        while True:
            try:
                token = await minter.mint("IMAGE_GENERATION")
                break
            except RecaptchaError:
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.5)
        mint_ms = round((time.perf_counter() - t0) * 1000)
        body = build_ogiz0b_body(
            prompt="a lighthouse on a rocky coast, ink and watercolor",
            model="BELUGA",
            project_id=other,
            recaptcha_token=token,
            at_token=wire.tokens.at,
            batch_id=str(uuid.uuid4()).upper(),
            entry_seeds=(1 + int.from_bytes(os.urandom(4), "big") % (2**31 - 2),),
        )
        status, text, ms = await wire.send_body("ogiZ0b", body, other)
        recs: list[Any] = []
        for rid, data in parse_frames(text) if status == 200 else []:
            if rid == "ogiZ0b":
                with contextlib.suppress(Exception):
                    recs.extend(image_records(rid, data))
        errs = (
            [{"code": e.code, "reasons": list(e.reasons)} for e in rpc_errors(text)]
            if status == 200
            else []
        )
        _log(
            "cross_project",
            i=i,
            parked_on_home=home in parked,
            submitted_to=other,
            still_parked=page.url == parked,
            status=status,
            mint_ms=mint_ms,
            post_ms=ms,
            verdict="GENERATED" if recs else ("REJECTED" if errs else "EMPTY"),
            errors=errs,
        )


def _keep_awake(on: bool) -> None:
    """Ask Windows not to sleep while the spike runs (no-op elsewhere)."""
    if sys.platform == "win32":
        import ctypes

        es_continuous, es_system_required = 0x80000000, 0x00000001
        flags = es_continuous | (es_system_required if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)  # pyright: ignore[reportAttributeAccessIssue]


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--profile", default="ffroliva")
    ap.add_argument("--project", help="project for --capture / --spend (default: a new one)")
    ap.add_argument("--capture", action="store_true")
    ap.add_argument("--reads", action="store_true")
    ap.add_argument("--upscale", action="store_true")
    ap.add_argument("--poll-upsampled", action="store_true", help="--upscale without a submit")
    ap.add_argument(
        "--clip",
        help="WORKFLOW:MEDIA:PROJECT:ASPECT of a finished clip "
        "(--reads / --upscale); ASPECT = 1 (9:16) or 2 (16:9)",
    )
    ap.add_argument("--spend", default="", help="t2v,i2v — CREDITS")
    ap.add_argument("--t2v-key", default="macaron_cookie_t2v_4s_360p")
    ap.add_argument("--i2v-key", default="macaron_cookie_i2v_4s_360p")
    ap.add_argument("--i2v-start", help="MEDIA_ID of an uploaded start frame (--spend i2v)")
    ap.add_argument(
        "--i2v-crop",
        default="[0.21875,null,0.78125,1]",
        help="JSON crop box of the start frame, as --capture recorded it",
    )
    ap.add_argument("--headless", action="store_true", help="launch Chrome headless")
    ap.add_argument("--images", type=int, default=0, help="N direct image generations (quota)")
    ap.add_argument("--cold", type=int, default=0, help="N fresh-browser rounds (headless+headed)")
    ap.add_argument("--soak", type=float, default=0, help="minutes of one warm session")
    ap.add_argument("--every", type=float, default=300, help="--soak interval, seconds")
    ap.add_argument("--concurrency", type=int, default=0, help="N direct images in flight")
    ap.add_argument("--idle", type=float, default=0, help="minutes idle between two images")
    ap.add_argument("--image-models", default="", help="raw wire names, e.g. HARBOR_SEAL,GEM_PIX_2")
    ap.add_argument("--video-aspect", type=int, default=2, help="1 = 9:16, 2 = 16:9 (--spend)")
    ap.add_argument("--cross-project", default="", help="OTHER project id: mint here, submit there")
    args = ap.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    _keep_awake(True)  # 2026-11-27: a system sleep mid-soak stalled two runs for 19 and 41 min
    global _OUT  # noqa: PLW0603 - spike-wide evidence sink
    out = _OUT = default_out_path(f"spike_direct_video_{args.profile}", ".jsonl")
    step("OUTPUT", str(out))
    os.environ["GFLOW_CLI_TRANSPORT"] = "ui_automation"  # the capture drives the real UI
    try:
        if args.cold:
            await run_cold(profile_dir, args.project, args.cold)
            return
        async with build_client(profile_dir, headless=args.headless) as client:
            page = client._page  # noqa: SLF001
            assert page is not None, "the client opened no page"  # noqa: S101 - spike guard
            await page.add_init_script(_ACTION_HOOK)
            wf = media = clip_project = None
            aspect = 2
            if args.clip:
                wf, media, clip_project, a = args.clip.split(":")
                aspect = int(a)
            project_id = args.project or clip_project
            _log(
                "browser",
                headless=args.headless,
                navigator_ua=await page.evaluate("navigator.userAgent"),
                webdriver=await page.evaluate("navigator.webdriver"),
            )
            if (
                args.capture
                or args.spend
                or args.images
                or args.soak
                or args.concurrency
                or args.idle
                or args.image_models
            ) and not args.project:
                project_id = (
                    await client.create_project(title="gflow spike direct video")
                ).project_id
            _log("project", project_id=project_id)
            if project_id is None:
                ap.error("pass --project or --clip, or a phase that creates a project")
            await page.goto(
                f"{_ORIGIN}/project/{project_id}", wait_until="domcontentloaded", timeout=60_000
            )
            wire = Wire(page, profile_dir)
            await wire.open()
            if args.reads and wf and clip_project:
                await phase_reads(wire, clip_project, wf)
            if args.upscale and wf and media and clip_project:
                await phase_upscale(
                    wire, clip_project, wf, media, aspect, poll_only=args.poll_upsampled
                )
            if args.images:
                await phase_images(client, profile_dir, project_id, args.images)
            if args.cross_project:
                await run_cross_project(wire, page, project_id, args.cross_project, 3)
            if args.image_models:
                await run_image_models(wire, project_id, args.image_models.split(","))
            if args.concurrency:
                await run_concurrency(client, profile_dir, project_id, args.concurrency)
            if args.idle:
                await run_idle(client, profile_dir, project_id, args.idle)
            if args.soak:
                await run_soak(client, profile_dir, project_id, args.soak, args.every)
            if args.capture:
                await phase_capture(client, project_id)
            for mode in filter(None, args.spend.split(",")):
                key = args.t2v_key if mode == "t2v" else args.i2v_key
                start, crop = args.i2v_start, json.loads(args.i2v_crop)
                if mode == "i2v" and not start:
                    t0 = time.perf_counter()
                    ref = await dh.run_direct_upload(
                        page=page, profile_dir=profile_dir, project_id=project_id, path=_START
                    )
                    start = ref.name
                    b = _START.read_bytes()
                    crop = _crop(
                        (int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")),
                        *((9, 16) if args.video_aspect == 1 else (16, 9)),
                    )
                    _log(
                        "i2v_upload",
                        media_id=start,
                        crop=crop,
                        s=round(time.perf_counter() - t0, 1),
                    )
                await wire.open()  # cookies may have rotated
                await phase_spend(wire, project_id, mode, key, start, crop, args.video_aspect)
            await wire.hx.aclose()
    finally:
        with open(out, "w", encoding="utf-8") as f:
            for r in _records:
                f.write(json.dumps(r, default=str) + "\n")
        step("SAVED", str(out))
        _keep_awake(False)


if __name__ == "__main__":
    asyncio.run(main())
