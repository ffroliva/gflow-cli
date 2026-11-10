r"""#1221 (S1): where does flow.google.com's page get the batchexecute `at` token?

Cost: $0. Read-only. One headed browser on the profile (through FlowApiClient, which takes the
profile lease) loads the app root and one project page; nothing is clicked or submitted. Then a
plain httpx client makes GETs of the same two routes (with and without the profile's cookies) and
`nzlxg` (balance) reads with several `at` values, including the two controls. No generation,
upload, create or delete.

Pre-registered reading (written before the first run):

- The page's `at` equals a value in a response it received, httpx gets a working value from the
  same route, and nzlxg answers 200 -> found and reachable without a browser: direct reads never
  need the browser fallback.
- The page's `at` equals a value only in runtime JS state (no response body, cookie or served
  HTML) -> found only in running JS state: the browser fallback stays.
- The page's `at` matches no candidate -> not found: unmeasured; record what was looked at.
- The page sent no `at` at all -> `at` is not required on this path; the cookie authenticates.
- Every shell this run sees carries `SNlM0e` -> the #1219 condition did NOT reproduce, so where
  the page gets `at` when the shell has none is still unmeasured.

Candidates compared with each value the page sent: every `WIZ_global_data` key (snapshot after
each load), every cookie, and every text response body the page received (with arrival order, so
"came from response X" needs X to arrive before the first request that sent the value).

Controls for the direct read: a junk `at` (expect 400) and no cookies (expect 401). If the junk
`at` is accepted, the direct read does not prove the token is needed. The browser-minted `at` is
also sent from httpx (does a token cross clients?), and an empty `at` (is it needed at all?).

Discriminator for a future "absent" sighting: the WIZ key names a cookie-carrying shell has and an
anonymous one lacks. A shell without `SNlM0e` that also lacks those keys is an anonymous shell
(the cookies were not accepted), not a signed-in shell that moved the token.

The httpx arm runs twice: before the browser opens (the disk jar as a page-free reader finds it)
and after it closes (Chrome rotates cookies on load, so the two can differ).

Prints no token, cookie, id or URL. An `at` is reported only as booleans, its length and a
redacted shape (alphanumeric runs collapsed to their length). WIZ key NAMES are printed; values
never are.

Usage: uv run python scripts/dev/spike_at_token_source.py [profile]   (default denon82)
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
from itertools import count
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "dev"))

from gflow_cli.api.dto import CreditsInfo  # noqa: E402
from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: E402
from gflow_cli.auth.cookies import get_cookies_path  # noqa: E402
from gflow_cli.paths import default_home, profile_subdir  # noqa: E402

ORIGIN = "https://flow.google.com"
BE = f"{ORIGIN}/_/AiSandboxAngularFrontend/data/batchexecute"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
# What a Chrome top-level navigation sends, minus cookies (httpx adds the jar).
NAV_HEADERS = {
    "user-agent": UA,
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "upgrade-insecure-requests": "1",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
}
OUT = ROOT / "scripts" / "dev" / "_spike_out" / "at_token_source.json"
TEXT_TYPES = ("document", "xhr", "fetch", "script", "other")
WIZ_RE = re.compile(r'"([A-Za-z0-9_]{4,8})":')


def shape(value: str) -> str:
    """Redacted shape: alphanumeric runs become their length, separators stay ('w28:d13')."""
    return re.sub(
        r"[A-Za-z0-9_-]+",
        lambda m: ("d" if m.group().isdigit() else "w") + str(len(m.group())),
        value,
    )


def wiz(text: str, key: str) -> str | None:
    m = re.search(rf'"{key}":"([^"]*)"', text)
    return m.group(1) if m else None


def wiz_keys(html: str) -> set[str]:
    """Key names of the inline WIZ_global_data object (names only, never values)."""
    m = re.search(r"WIZ_global_data\s*=\s*\{(.*?)\};", html, flags=re.S)
    return set(WIZ_RE.findall(m.group(1))) if m else set()


def route(url: str) -> str:
    """Host + path with every long id-like segment masked; never the query."""
    p = urlsplit(url)
    segs = ["<id>" if len(s) > 20 else s for s in p.path.split("/")]
    return p.netloc + "/".join(segs)


def catalog_project(profile: str) -> str:
    db = default_home() / "gflow.db"
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    return con.execute(
        "select flow_project_id from assets where profile_name=? and kind='video'"
        " and flow_project_id is not null order by created_at desc limit 1",
        (profile,),
    ).fetchone()[0]


async def page_arm(profile: str, pid: str, wait_s: float = 15.0) -> tuple[dict[str, Any], set[str]]:
    """Return (report, distinct at values). The values stay in memory and are never written."""
    from _spike_common import build_client, resolve_profile_dir  # noqa: PLC0415

    seq = count()
    sent: list[dict[str, Any]] = []
    bodies: list[dict[str, Any]] = []
    tasks: list[asyncio.Task[None]] = []
    wiz_snaps: list[dict[str, str]] = []

    def on_request(req: Any) -> None:
        url = str(req.url)
        if "batchexecute" not in url:
            return
        try:
            form = parse_qs(req.post_data or "")
        except Exception:  # noqa: BLE001 - a binary body is not ours
            form = {}
        q = parse_qs(urlsplit(url).query)
        sent.append(
            {
                "seq": next(seq),
                "rpcids": q.get("rpcids", [""])[0],
                "at": form.get("at", [None])[0] or q.get("at", [None])[0],
            }
        )

    async def grab(entry: dict[str, Any], resp: Any) -> None:
        try:
            entry["text"] = await resp.text()
        except Exception:  # noqa: BLE001 - redirects and aborted bodies have none
            entry["text"] = ""

    def on_response(resp: Any) -> None:
        if resp.request.resource_type not in TEXT_TYPES:
            return
        entry = {
            "seq": next(seq),
            "method": resp.request.method,
            "type": resp.request.resource_type,
            "route": route(resp.url),
            "text": "",
        }
        bodies.append(entry)
        tasks.append(asyncio.ensure_future(grab(entry, resp)))

    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument, same as spike_rpc_ids_in_bundle
        page.context.on("request", on_request)
        page.context.on("response", on_response)
        for path in ("/", f"/project/{pid}"):
            await page.goto(f"{ORIGIN}{path}", wait_until="domcontentloaded", timeout=60_000)
            await asyncio.sleep(wait_s)
            wiz_snaps.append(
                await page.evaluate(
                    """() => {
                        const w = window.WIZ_global_data || {};
                        const out = {};
                        for (const [k, v] of Object.entries(w))
                            out[k] = typeof v === 'string' ? v : JSON.stringify(v);
                        return out;
                    }"""
                )
            )
        cookies = await page.context.cookies()
        await asyncio.gather(*tasks, return_exceptions=True)

    distinct = {s["at"] for s in sent if s["at"]}
    docs = [b for b in bodies if b["type"] == "document" and "flow.google.com" in b["route"]]
    per_value = []
    for v in sorted(distinct):  # sorted only for a stable order; values never printed
        first_sent = min(s["seq"] for s in sent if s["at"] == v)
        carriers = [b for b in bodies if v in b["text"]]
        per_value.append(
            {
                "len": len(v),
                "shape": shape(v),
                "calls": sum(1 for s in sent if s["at"] == v),
                "wiz_keys_equal": sorted(
                    {k for snap in wiz_snaps for k, x in snap.items() if x == v}
                ),
                "wiz_keys_containing": sorted(
                    {k for snap in wiz_snaps for k, x in snap.items() if v in x and x != v}
                ),
                "cookies_equal_or_containing": sorted(
                    c["name"] for c in cookies if v in c["value"]
                ),
                "carrier_responses": [
                    {
                        "type": b["type"],
                        "route": b["route"],
                        "arrived_before_first_use": b["seq"] < first_sent,
                        "as_SNlM0e": wiz(b["text"], "SNlM0e") == v,
                    }
                    for b in carriers
                ],
            }
        )
    # Each call vs the SNlM0e of the newest flow.google.com document that arrived before it.
    matched = 0
    for s in sent:
        prior = [d for d in docs if d["seq"] < s["seq"]]
        if prior and s["at"] and wiz(prior[-1]["text"], "SNlM0e") == s["at"]:
            matched += 1
    report = {
        "batchexecute_calls": len(sent),
        "rpcids": sorted({s["rpcids"] for s in sent}),
        "calls_with_at": sum(1 for s in sent if s["at"]),
        "calls_matching_latest_prior_document_SNlM0e": matched,
        "distinct_at_values": len(distinct),
        "per_value": per_value,
        "flow_documents": [
            {
                "route": d["route"],
                "has_SNlM0e": bool(wiz(d["text"], "SNlM0e")),
                "wiz_key_count": len(wiz_keys(d["text"])),
            }
            for d in docs
        ],
        "other_documents": sorted(
            {b["route"] for b in bodies if b["type"] == "document" and b not in docs}
        ),
        "responses_captured": len(bodies),
        "wiz_runtime_SNlM0e_present": [("SNlM0e" in s) for s in wiz_snaps],
    }
    return report, distinct


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


def be_call(
    jar: httpx.Cookies | None, rpc: str, arg: list[Any], at: str, fsid: str | None, bl: str | None
) -> httpx.Response:
    q = {
        "rpcids": rpc,
        "source-path": "/",
        "bl": bl,
        "f.sid": fsid,
        "hl": "en",
        "_reqid": 100001,
        "rt": "c",
    }
    body = urlencode({"f.req": json.dumps([[[rpc, json.dumps(arg), None, "generic"]]]), "at": at})
    hdr = {
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
        "x-same-domain": "1",
        "origin": ORIGIN,
        "referer": f"{ORIGIN}/",
        "user-agent": UA,
    }
    with httpx.Client(cookies=jar, timeout=30) as c:
        return c.post(
            f"{BE}?{urlencode({k: v for k, v in q.items() if v is not None})}",
            content=body,
            headers=hdr,
        )


def nzlxg(jar: httpx.Cookies | None, at: str, fsid: str | None, bl: str | None) -> dict[str, Any]:
    r = be_call(jar, "nzlxg", [], at, fsid, bl)
    frames = (
        [p for rid, p in parse_frames(r.text) if rid == "nzlxg"] if r.status_code == 200 else []
    )
    parsed = False
    for p in frames:
        try:
            CreditsInfo.from_nzlxg(p)
            parsed = True
        except ValueError:
            pass
    return {"status": r.status_code, "frames": len(frames), "balance_parsed": parsed}


def direct_arm(profile: str, pid: str, page_ats: set[str]) -> dict[str, Any]:
    jar = jar_for(profile)
    out: dict[str, Any] = {"gets": {}}
    found: str | None = None
    fsid = bl = None
    keys: dict[str, set[str]] = {}
    arms = (
        ("no_cookies", None, NAV_HEADERS),
        ("cookies_minimal_ua", jar, {"user-agent": UA}),
        ("cookies_chrome_nav", jar, NAV_HEADERS),
    )
    for arm, cj, headers in arms:
        for label, path in (("root", "/"), ("project", f"/project/{pid}")):
            with httpx.Client(cookies=cj, timeout=30, follow_redirects=True, headers=headers) as hx:
                r = hx.get(f"{ORIGIN}{path}")
            at = wiz(r.text, "SNlM0e")
            keys[f"{arm}/{label}"] = wiz_keys(r.text)
            out["gets"][f"{arm}/{label}"] = {
                "status": r.status_code,
                "final_route": route(str(r.url)),
                "has_SNlM0e": bool(at),
                "at_shape": shape(at) if at else None,
                "equals_a_page_at": bool(at and at in page_ats),
                "wiz_key_count": len(keys[f"{arm}/{label}"]),
            }
            if cj is not None:
                fsid, bl = fsid or wiz(r.text, "FdrFJe"), bl or wiz(r.text, "cfb2h")
                found = found or at
    out["wiz_keys_only_with_cookies"] = sorted(
        keys["cookies_chrome_nav/root"] - keys["no_cookies/root"]
    )
    out["direct_at_obtained"] = bool(found)
    if found:
        out["nzlxg_with_httpx_at"] = nzlxg(jar, found, fsid, bl)
        out["control_junk_at"] = nzlxg(jar, "AJunk:0", fsid, bl)["status"]
        out["control_no_cookies"] = nzlxg(None, found, fsid, bl)["status"]
    else:
        out["nzlxg_with_httpx_at"] = "unmeasured: no at obtained without a browser"
    if page_ats:
        out["nzlxg_with_browser_minted_at"] = nzlxg(jar, sorted(page_ats)[0], fsid, bl)
    out["nzlxg_with_empty_at"] = nzlxg(jar, "", fsid, bl)["status"]
    return out


def main(profile: str) -> int:
    pid = catalog_project(profile)
    # Before the browser runs: the disk jar as a page-free reader would find it (Chrome rotates
    # cookies on load, so the pass after it is not the same state).
    before = direct_arm(profile, pid, set())
    report, page_ats = asyncio.run(page_arm(profile, pid))
    out = {
        "direct_before_browser": before,
        "page": report,
        "direct_after_browser": direct_arm(profile, pid, page_ats),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "denon82"))
