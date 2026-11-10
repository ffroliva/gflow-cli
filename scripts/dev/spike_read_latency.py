r"""#1225 (S5): wall time of a direct batchexecute read vs the browser read.

Cost: $0. Read-only. Three reads, each the app's own:
    as29s   ["<workflow_id>"]                       status of one finished clip
    Zzl0ze  ["projects/<pid>",null,null,null,[1]]   the project's listing
    nzlxg   []                                      the balance
Same profile, same project, same clip (the newest finished video in the local catalog).
No generation, upload, create or delete. Nothing is clicked.

Arms, N >= 10 each, per read:
- browser_cold : a new FlowApiClient (Chrome start, profile lease) + page.goto + the page's own
                 reply, read with gflow's `_rpc_reply` (nzlxg: `client.get_credits()`, which is
                 exactly what `gflow credits` runs). Clock stops when the reply arrives; client
                 entry and Chrome teardown are also timed apart.
- browser_warm : one FlowApiClient, its page parked on about:blank (untimed), then goto + the
                 page's own reply. Samples under 100 ms are counted as suspect.
- direct_fresh : a new httpx client, cookie jar already in memory, one GET to obtain `at`, then
                 the POST. What a one-shot CLI call would pay.
- direct_cached: one long-lived httpx client and a cached `at`; the POST only. What a poller
                 would pay.
The disk cookie read (browser_cookie3) is timed once per run and reported apart.

Pre-registered reading (from #1225, written before the first run), per read, on p50:
| direct_cached p50 <= browser_warm p50 / 5         | the direct tier is worth its maintenance  |
| browser_warm p50 / direct_cached p50 < 2          | not worth it for speed alone              |
| in between                                         | worth it only with another reason (no     |
|                                                    |   Chrome, no profile lease, headless)     |
The same ratio is also reported fresh-vs-cold (one-shot CLI call against a cold Chrome).
If no `at` can be obtained without a browser, the direct arms are UNMEASURED and say why.
Every direct call must decode to a frame of its rpc, or it is not counted as a read.

Prints no token, cookie, id or URL.

Usage: uv run python scripts/dev/spike_read_latency.py [profile] [N]   (default denon82 10)
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "dev"))

from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: E402
from gflow_cli.auth.cookies import get_cookies_path  # noqa: E402
from gflow_cli.paths import default_home, profile_subdir  # noqa: E402

ORIGIN = "https://flow.google.com"
BE = f"{ORIGIN}/_/AiSandboxAngularFrontend/data/batchexecute"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
OUT = ROOT / "scripts" / "dev" / "_spike_out" / "read_latency.json"


def wiz(text: str, key: str) -> str | None:
    m = re.search(rf'"{key}":"([^"]*)"', text)
    return m.group(1) if m else None


def catalog_clip(profile: str) -> tuple[str, str, str]:
    db = default_home() / "gflow.db"
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    return con.execute(
        "select flow_project_id, flow_media_id, flow_workflow_id from assets where profile_name=?"
        " and kind='video' and status='MEDIA_GENERATION_STATUS_SUCCESSFUL'"
        " and flow_workflow_id is not null order by created_at desc limit 1",
        (profile,),
    ).fetchone()


def reads(pid: str, mid: str, wid: str) -> dict[str, dict[str, Any]]:
    return {
        "as29s": {"arg": [wid], "path": f"/project/{pid}/edit/{mid}"},
        "Zzl0ze": {"arg": [f"projects/{pid}", None, None, None, [1]], "path": f"/project/{pid}"},
        "nzlxg": {"arg": [], "path": "/"},
    }


def stats(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    p95 = s[min(len(s) - 1, round(0.95 * (len(s) - 1)))]
    return {
        "n": len(s),
        "p50": round(statistics.median(s), 3),
        "p95": round(p95, 3),
        "min": round(s[0], 3),
        "max": round(s[-1], 3),
    }


# --------------------------------------------------------------------------- direct arms


def jar_for(profile: str) -> httpx.Cookies:
    import browser_cookie3

    pdir = profile_subdir(default_home(), profile)
    raw = browser_cookie3.chrome(
        cookie_file=get_cookies_path(pdir), key_file=pdir / "Local State", domain_name="google.com"
    )
    jar = httpx.Cookies()
    for c in raw:
        jar.set(c.name, c.value or "", domain=c.domain, path=c.path)
    return jar


def fetch_at(hx: httpx.Client) -> tuple[str | None, str | None, str | None]:
    """(at, f.sid, bl) from one cookie-only GET of the shell."""
    html = hx.get(f"{ORIGIN}/").text
    return wiz(html, "SNlM0e"), wiz(html, "FdrFJe"), wiz(html, "cfb2h")


def post(
    hx: httpx.Client, rpc: str, spec: dict[str, Any], at: str, fsid: str | None, bl: str | None
) -> bool:
    q = {
        "rpcids": rpc,
        "source-path": spec["path"],
        "bl": bl,
        "f.sid": fsid,
        "hl": "en",
        "_reqid": 100001,
        "rt": "c",
    }
    body = urlencode(
        {"f.req": json.dumps([[[rpc, json.dumps(spec["arg"]), None, "generic"]]]), "at": at}
    )
    r = hx.post(
        f"{BE}?{urlencode({k: v for k, v in q.items() if v is not None})}",
        content=body,
        headers={
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "x-same-domain": "1",
            "origin": ORIGIN,
            "referer": f"{ORIGIN}/",
        },
    )
    return r.status_code == 200 and any(rid == rpc for rid, _ in parse_frames(r.text))


def direct_arms(profile: str, specs: dict[str, dict[str, Any]], n: int) -> dict[str, Any]:
    t = time.perf_counter()
    jar = jar_for(profile)
    out: dict[str, Any] = {"cookie_read_s": round(time.perf_counter() - t, 3)}
    with httpx.Client(cookies=jar, timeout=30, headers={"user-agent": UA}) as hx:
        t = time.perf_counter()
        at, fsid, bl = fetch_at(hx)
        out["token_fetch_s"] = round(time.perf_counter() - t, 3)
    out["at_obtained"] = bool(at)
    if not at:
        out["unmeasured"] = "the shell carried no SNlM0e, so no at without a browser"
        return out
    fresh: dict[str, list[float]] = {k: [] for k in specs}
    cached: dict[str, list[float]] = {k: [] for k in specs}
    fails = {"fresh": 0, "cached": 0}
    for _ in range(n):
        for rpc, spec in specs.items():
            t = time.perf_counter()
            with httpx.Client(cookies=jar, timeout=30, headers={"user-agent": UA}) as hx:
                a, f, b = fetch_at(hx)
                ok = bool(a) and post(hx, rpc, spec, a or "", f, b)
            (fresh[rpc].append(time.perf_counter() - t) if ok else None)
            fails["fresh"] += not ok
    with httpx.Client(cookies=jar, timeout=30, headers={"user-agent": UA}) as hx:
        post(hx, "nzlxg", specs["nzlxg"], at, fsid, bl)  # open the connection once, untimed
        for _ in range(n):
            for rpc, spec in specs.items():
                t = time.perf_counter()
                ok = post(hx, rpc, spec, at, fsid, bl)
                (cached[rpc].append(time.perf_counter() - t) if ok else None)
                fails["cached"] += not ok
    out["failed_reads"] = fails
    out["direct_fresh"] = {k: stats(v) for k, v in fresh.items()}
    out["direct_cached"] = {k: stats(v) for k, v in cached.items()}
    return out


# --------------------------------------------------------------------------- browser arms


async def browser_read(page: Any, rpc: str, spec: dict[str, Any]) -> None:
    from gflow_cli.api.transports.migrated_composer import _rpc_reply, read_credits  # noqa: PLC0415

    if rpc == "nzlxg":
        await read_credits(page, timeout_s=60)
        return

    async def load() -> None:
        await page.goto(f"{ORIGIN}{spec['path']}", wait_until="domcontentloaded", timeout=60_000)

    await _rpc_reply(page, rpc, load, timeout_s=60)


async def browser_arms(profile: str, specs: dict[str, dict[str, Any]], n: int) -> dict[str, Any]:
    from _spike_common import build_client, resolve_profile_dir  # noqa: PLC0415

    pdir = resolve_profile_dir(profile)
    cold: dict[str, list[float]] = {k: [] for k in specs}
    enter: list[float] = []
    teardown: list[float] = []
    warm: dict[str, list[float]] = {k: [] for k in specs}
    errors: dict[str, int] = {"cold": 0, "warm": 0}
    for _ in range(n):
        for rpc, spec in specs.items():
            t = time.perf_counter()
            try:
                async with build_client(pdir) as client:
                    enter.append(time.perf_counter() - t)
                    if rpc == "nzlxg":
                        await client.get_credits()  # exactly what `gflow credits` runs
                    else:
                        await browser_read(client._page, rpc, spec)  # noqa: SLF001
                    cold[rpc].append(time.perf_counter() - t)
                    t = time.perf_counter()
                teardown.append(time.perf_counter() - t)
            except Exception as exc:  # noqa: BLE001 - counted, the run goes on
                errors["cold"] += 1
                print(f"cold {rpc} error {type(exc).__name__}", file=sys.stderr)
    async with build_client(pdir) as client:
        page = client._page  # noqa: SLF001
        await browser_read(page, "nzlxg", specs["nzlxg"])  # warm the page once, untimed
        for rpc, spec in specs.items():  # blocks per read: no reply from another read's load
            for _ in range(n):
                # Park off the app, untimed, so no late frame of the previous load is caught.
                await page.goto("about:blank")
                t = time.perf_counter()
                try:
                    await browser_read(page, rpc, spec)
                    warm[rpc].append(time.perf_counter() - t)
                except Exception as exc:  # noqa: BLE001
                    errors["warm"] += 1
                    print(f"warm {rpc} error {type(exc).__name__}", file=sys.stderr)
    return {
        "browser_cold": {k: stats(v) for k, v in cold.items()},
        "browser_cold_client_enter": stats(enter),
        "browser_cold_teardown": stats(teardown),
        "warm_samples_under_100ms": sum(1 for v in warm.values() for x in v if x < 0.1),
        "browser_warm": {k: stats(v) for k, v in warm.items()},
        "errors": errors,
    }


def ratios(res: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    d, b = res.get("direct", {}), res.get("browser", {})
    for rpc in ("as29s", "Zzl0ze", "nzlxg"):
        try:
            out[rpc] = {
                "warm_over_cached": round(
                    b["browser_warm"][rpc]["p50"] / d["direct_cached"][rpc]["p50"], 1
                ),
                "cold_over_fresh": round(
                    b["browser_cold"][rpc]["p50"] / d["direct_fresh"][rpc]["p50"], 1
                ),
            }
        except (KeyError, ZeroDivisionError):
            out[rpc] = "unmeasured"
    return out


def main(profile: str, n: int) -> int:
    specs = reads(*catalog_clip(profile))
    res: dict[str, Any] = {"n": n, "direct": direct_arms(profile, specs, n)}
    res["browser"] = asyncio.run(browser_arms(profile, specs, n))
    res["ratios_p50"] = ratios(res)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    raise SystemExit(main(args[0] if args else "denon82", int(args[1]) if len(args) > 1 else 10))
