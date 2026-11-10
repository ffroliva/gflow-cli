"""#1228 spike ($0): can a nightly run route-abort each submit and fingerprint its body shape?

Each run opens a fresh browser on the profile (``UiAutomationTransport.setup`` takes the
profile lease), drives ONE production migrated-host path straight on the page
(``run_images`` / ``run_video`` / ``run_extend`` / ``upscale_*_migrated``, not
``FlowApiClient``, so there are no retries and no catalog rows), lets the page mint its own
reCAPTCHA token, and aborts the billed request before it leaves the browser. What it keeps
is a structural fingerprint of the request: array nesting and arity, value classes
(null/bool/int/float/uuid/other string), UPPER_ENUM literals and the model key with its
digits masked, plus the query-param and form-field names and where the rpc id rode (URL,
body or both). Never a value, an id, a token or a prompt. Raw evidence stays in the
gitignored ``scripts/dev/_spike_out/spike_1228/``.

Billing guard (belt and braces):

* a context route on every ``batchexecute`` aborts a request when any billed id
  (``BILLED``) appears in the URL ``rpcids``, in any ``f.req`` frame, or as a substring of
  the URL or decoded body; it also aborts, fail-closed, any POST whose id does not resolve
  or whose body cannot be decoded, and counts those;
* ``Route.continue_`` is wrapped with the same predicate, because a production page route
  that ends in ``continue_()`` (``_guard_image_submit``, i2i only) bypasses a context route;
* a tripwire: any response to a request carrying a billed id is a LEAK, exit 3;
* the caller reads ``gflow credits user`` before, between paths and after, with no
  browser open, and stops on any drop.

Cost: navigation, uploads (``maseQ``, i2v only) and reCAPTCHA mints are free. Nothing
billed is sent. Side effect: each i2v run leaves one uploaded frame in the project.

PRE-REGISTERED READING (written before the first live run, 2026-11-10):

* every run of a path captures its expected rpc, N>=3 fingerprints are identical and
  credits are unchanged: stable, the path can join the canary;
* a run captures nothing: that run is UNMEASURED, not a pass; the path is not
  canary-ready until it captures N/N;
* fingerprints differ across runs: not canary-ready; name the varying slot, do not mask
  it after the fact;
* a run captures a different rpc than expected (e.g. ``nprQif`` on t2v): a state leak,
  reported as a finding, not as shape drift;
* any fail-closed abort of an unresolved request: a finding, name the URL ids it carried;
* any LEAK or any credit drop: STOP at once, report, run nothing else.

Usage:
    python scripts/dev/spike_1228_route_abort_drift.py --selftest
    python scripts/dev/spike_1228_route_abort_drift.py --profile denon82 \
        --config scripts/dev/_spike_out/spike_1228/ids.json --path t2i --runs 3

``ids.json`` holds ``project``, ``video_media`` (a finished clip in ``project``),
``image_project`` and ``image_media``. It stays local: it carries full ids.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import gzip
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlsplit

OUT = Path(__file__).parent / "_spike_out" / "spike_1228"

BILLED = frozenset({"YhhmEf", "eb1hJf", "MZZa6b", "nprQif", "ogiZ0b", "fZytfe", "SPrCad", "p0UkFb"})
EXPECTED = {
    "t2i": {"ogiZ0b"},
    "t2v": {"YhhmEf"},
    "i2v": {"eb1hJf"},
    "extend": {"fZytfe"},
    "upscale-image": {"SPrCad"},
    "upscale-video": {"p0UkFb"},
}
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
KEY = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
ENUM = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
FRAME_ID = re.compile(r'\[\s*"([A-Za-z0-9]{5,8})"\s*,')

# --- pure helpers (self-tested offline) ----------------------------------------------------


def decode_body(raw: bytes | None) -> str | None:
    """Form-decoded body text; ``""`` for no body; ``None`` when it cannot be decoded."""
    if not raw:
        return ""
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError:
            return None
    try:
        return unquote_plus(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def frames(body: str) -> list[Any]:
    start = body.find("f.req=")
    if start < 0:
        return []
    try:
        outer, _ = json.JSONDecoder().raw_decode(body, start + len("f.req="))
    except ValueError:
        return []
    got = outer[0] if isinstance(outer, list) and outer and isinstance(outer[0], list) else []
    return [f for f in got if isinstance(f, list) and f]


def request_ids(url: str, body: str) -> tuple[list[str], list[str]]:
    """(ids in the URL ``rpcids``, ids heading each ``f.req`` frame)."""
    q = parse_qs(urlsplit(url).query).get("rpcids", [])
    in_url = [r for v in q for r in v.split(",") if r]
    in_body = [str(f[0]) for f in frames(body)]
    return in_url, in_body


def verdict(url: str, method: str, body: str | None) -> str:
    """``billed`` | ``unresolved`` | ``ok`` for one batchexecute request."""
    if body is None:
        return "unresolved"  # undecodable: an id may hide in it
    in_url, in_body = request_ids(url, body)
    ids = set(in_url) | set(in_body)
    if ids & BILLED or any(b in url or b in body for b in BILLED):
        return "billed"
    if method == "POST" and not ids:
        return "unresolved"
    return "ok"


def skeleton(node: Any) -> Any:
    """Structure only: classes, never values (except enum literals and masked keys)."""
    if node is None:
        return "N"
    if isinstance(node, bool):
        return "B"
    if isinstance(node, int):
        return "I"
    if isinstance(node, float):
        return "F"
    if isinstance(node, list):
        return [skeleton(x) for x in node]
    if isinstance(node, dict):
        return {k: skeleton(v) for k, v in sorted(node.items())}
    s = str(node)
    if s[:1] in "[{":
        with contextlib.suppress(ValueError):
            return {"json": skeleton(json.loads(s))}
    if UUID.match(s):
        return "U"
    if KEY.match(s):
        return "K:" + re.sub(r"\d+", "9", s)
    if ENUM.match(s):
        return "E:" + s
    return "S"


def fingerprint(url: str, body: str, rpcid: str) -> dict[str, Any]:
    in_url, in_body = request_ids(url, body)
    inner: Any = None
    for f in frames(body):
        if str(f[0]) == rpcid and len(f) > 1:
            with contextlib.suppress(ValueError, TypeError):
                inner = json.loads(str(f[1]))
    frame_tail = [skeleton(x) for f in frames(body) if str(f[0]) == rpcid for x in f[2:]]
    query = parse_qs(urlsplit(url).query)
    shape = {
        "rpc": rpcid,
        "carrier": ("url" if rpcid in in_url else "") + ("body" if rpcid in in_body else ""),
        "frames": len(in_body),
        "query_keys": sorted(query),
        "form_keys": sorted(parse_qs(body, keep_blank_values=True)),
        "frame_tail": frame_tail,
        "inner": skeleton(inner),
    }
    digest = hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16]
    return {**shape, "hash": digest, "bl": (query.get("bl") or [""])[0]}


def selftest() -> None:
    url = "https://flow.google.com/_/X/data/batchexecute?rpcids=YhhmEf&bl=b1&_reqid=1"

    def body(rpc: str, inner: Any, *more: str) -> str:
        fr = [[rpc, json.dumps(inner), None, "generic"], *([m, "[]", None, "1"] for m in more)]
        return "f.req=" + json.dumps([fr]) + "&at=TOKEN&"

    uid = "123e4567-e89b-12d3-a456-426614174000"
    a = body("YhhmEf", [["prompt one", "veo_3_1_t2v_lite_8s", [uid], 1, None, True]])
    assert verdict(url, "POST", a) == "billed"  # URL and body
    bare = "https://flow.google.com/_/X/data/batchexecute?bl=b1"
    assert verdict(bare, "POST", body("nprQif", [1])) == "billed"  # body only (Omni 1.2)
    assert verdict(bare, "POST", body("jwpduf", [1], "ogiZ0b")) == "billed"  # second frame
    assert verdict(bare, "POST", "") == "unresolved"  # empty body, no id
    assert verdict(bare, "POST", None) == "unresolved"  # undecodable
    free = "https://flow.google.com/_/X/data/batchexecute?rpcids=jwpduf&bl=b1"
    assert verdict(free, "POST", body("jwpduf", [1])) == "ok"
    assert decode_body(gzip.compress(b"f.req=%5B%5D")) == "f.req=[]"
    # Detector control: values change -> same hash; a slot added/removed -> new hash.
    uid2 = "9f0e8d7c-6b5a-4c3d-8e2f-1a0b9c8d7e6f"
    b = body("YhhmEf", [["another prompt", "veo_3_1_t2v_lite_4s", [uid2], 7, None, False]])
    assert fingerprint(url, a, "YhhmEf")["hash"] == fingerprint(url, b, "YhhmEf")["hash"]
    c = body("YhhmEf", [["prompt one", "veo_3_1_t2v_lite_8s", [uid], 1, None, True, 0]])
    d = body("YhhmEf", [["prompt one", "veo_3_1_t2v_lite_8s", [uid], 1, None]])
    e = body("YhhmEf", [["prompt one", "omni_flash_t2v_8s", [uid], 1, None, True]])
    for other in (c, d, e):
        assert fingerprint(url, a, "YhhmEf")["hash"] != fingerprint(url, other, "YhhmEf")["hash"]
    assert "TOKEN" not in json.dumps(fingerprint(url, a, "YhhmEf"))
    print("selftest: ok")


# --- live run ------------------------------------------------------------------------------


class Guard:
    def __init__(self) -> None:
        self.captured: list[dict[str, Any]] = []
        self.unresolved: list[list[str]] = []
        self.leaks: list[list[str]] = []
        self.got = asyncio.Event()
        self.sent: list[str] = []  # ids of the free requests let through, in order

    def check(self, request: Any) -> tuple[str, str | None]:
        url = str(request.url)
        try:
            raw = request.post_data_buffer
        except Exception:  # noqa: BLE001 - Playwright raises on some bodies; fail closed
            return "unresolved", None
        body = decode_body(raw)
        return verdict(url, request.method, body), body

    async def on_route(self, route: Any, request: Any) -> None:
        kind, body = self.check(request)
        if kind == "ok":
            self.sent.extend(request_ids(str(request.url), body or "")[0])
            await route.fallback()
            return
        url = str(request.url)
        if kind == "billed" and body is not None:
            in_url, in_body = request_ids(url, body)
            ids = [i for i in dict.fromkeys(in_url + in_body) if i in BILLED] or ["?"]
            self.captured.append({**fingerprint(url, body, ids[0]), "billed_ids": ids})
            self.got.set()
        else:
            self.unresolved.append(request_ids(url, body or "")[0] or ["<none>"])
        await route.abort()

    def on_response(self, response: Any) -> None:
        if "batchexecute" not in response.url:
            return
        kind, body = self.check(response.request)
        if kind == "billed":
            self.leaks.append(request_ids(response.url, body or "")[0])


def wrap_continue() -> None:
    """Once per process: a production ``continue_()`` cannot carry a billed id out."""
    from playwright.async_api import Route

    real = Route.continue_
    check = Guard().check  # stateless predicate

    async def safe_continue(self: Any, **kw: Any) -> None:
        if "batchexecute" in str(self.request.url) and check(self.request)[0] != "ok":
            await self.abort()  # a production continue_() must not carry a billed id out
            return
        await real(self, **kw)

    Route.continue_ = safe_continue  # type: ignore[method-assign]


def frame_png(path: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (720, 1280), (40, 90, 220)).save(path)
    return path


async def drive(path: str, page: Any, ids: dict[str, str], tmp: Path) -> None:
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.image_upscale import TargetResolution
    from gflow_cli.api.transports import migrated_composer as mc
    from gflow_cli.api.transports import migrated_extend as mx
    from gflow_cli.api.transports.migrated_upscale import upscale_image_migrated
    from gflow_cli.api.transports.migrated_video_upscale import upscale_video_migrated
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel

    prompt = "a slow dolly across a quiet harbour at dawn"

    async def save(_name: str, _data: bytes) -> Path:
        raise AssertionError("nothing should be downloaded")

    if path == "t2i":
        await mc.run_images(page, GenerateImageRequest(prompt=prompt), project_id=ids["project"])
    elif path in ("t2v", "i2v"):
        req = GenerateVideoRequest(
            prompt=prompt,
            mode=Mode.T2V if path == "t2v" else Mode.I2V,
            aspect=Aspect.PORTRAIT,
            model=VideoModel.VEO_3_1_LITE,
            start_image=frame_png(tmp / "start.png") if path == "i2v" else None,
        )
        await mc.run_video(
            page,
            req,
            project_id=ids["project"],
            save=save,
            poll_timeout_s=30.0,
            download=False,
            on_started=None,
        )
    elif path == "extend":
        await mx.run_extend(
            page,
            project_id=ids["project"],
            media_id=ids["video_media"],
            prompt=prompt,
            aspect=None,
            poll_timeout_s=60.0,  # the e2e budget: fZytfe follows its own mint
        )
    elif path == "upscale-image":
        await upscale_image_migrated(
            page,
            project_id=ids["image_project"],
            media_id=ids["image_media"],
            target_resolution=TargetResolution.RES_2K,
            timeout_s=30.0,
        )
    elif path == "upscale-video":
        await upscale_video_migrated(
            page, project_id=ids["project"], media_id=ids["video_media"], timeout_s=30.0
        )
    elif path == "navigate":
        await page.goto(mc.MIGRATED_PROJECT_URL.format(project_id=ids["project"]))
        await page.wait_for_timeout(20_000)
    else:
        raise SystemExit(f"unknown path {path}")


async def one_run(path: str, profile_dir: Path, ids: dict[str, str], n: int) -> dict[str, Any]:
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport

    guard = Guard()
    transport = UiAutomationTransport()
    started = time.monotonic()
    outcome = "captured"
    try:
        await transport.setup(profile_dir)
        page = transport._page  # noqa: SLF001 - the spike drives the live page
        assert page is not None
        await page.context.route("**/batchexecute**", guard.on_route)
        page.context.on("response", guard.on_response)
        tmp = OUT / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        task = asyncio.create_task(drive(path, page, ids, tmp))
        waiter = asyncio.create_task(guard.got.wait())
        done, _ = await asyncio.wait({task, waiter}, timeout=150, return_when="FIRST_COMPLETED")
        if waiter in done:
            await asyncio.sleep(3)  # let a second billed request (if any) show itself
        if task.done() and task.exception() is not None:
            exc = task.exception()
            outcome = f"driver ended: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
        for t in (task, waiter):
            t.cancel()
            with contextlib.suppress(BaseException):
                await t
        if not guard.captured and path != "navigate":
            outcome = f"UNMEASURED ({outcome})"
    finally:
        await transport.teardown()
    return {
        "path": path,
        "run": n,
        "seconds": round(time.monotonic() - started, 1),
        "outcome": outcome,
        "captured": guard.captured,
        "unresolved_aborts": guard.unresolved,
        "leaks": guard.leaks,
        "sent": list(dict.fromkeys(guard.sent)),
    }


def summarise(rec: dict[str, Any]) -> str:
    caps = [f"{c['rpc']}@{c['carrier']}#{c['hash']}" for c in rec["captured"]]
    return (
        f"{rec['path']} run {rec['run']}: {rec['seconds']}s {rec['outcome']} "
        f"captured={caps} unresolved={rec['unresolved_aborts']} leaks={rec['leaks']} "
        f"sent={rec['sent']}"
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--profile")
    ap.add_argument("--config", type=Path)
    ap.add_argument("--path", choices=[*EXPECTED, "navigate"])
    ap.add_argument("--runs", type=int, default=3)
    a = ap.parse_args()
    selftest()
    if a.selftest:
        return 0
    from gflow_cli.config import get_settings

    ids = json.loads(a.config.read_text())
    profile_dir = get_settings().profile_subdir(a.profile)
    OUT.mkdir(parents=True, exist_ok=True)
    wrap_continue()
    for n in range(1, a.runs + 1):
        rec = await one_run(a.path, profile_dir, ids, n)
        with (OUT / f"{a.path}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(summarise(rec), flush=True)
        if rec["leaks"]:
            print("LEAK: a billed request got a response. STOP.", flush=True)
            return 3
        wrong = {c["rpc"] for c in rec["captured"]} - EXPECTED.get(a.path, set())
        if wrong:
            print(f"UNEXPECTED rpc {sorted(wrong)} on {a.path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
