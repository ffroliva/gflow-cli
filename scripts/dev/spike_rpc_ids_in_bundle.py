r"""#1217: are flow.google.com's batchexecute RPC ids readable in the JavaScript it serves?

Cost: $0. Read-only. No browser is launched: one cookie-only GET of the shell, then GETs of the
scripts it references. No generation, upload, or RPC call is made.

Pre-registered reading (written before the first run):

| Outcome                                                  | Reading                                   |
|----------------------------------------------------------|-------------------------------------------|
| every known id found, random control absent              | a bundle check is a valid drift signal    |
| some known ids found                                      | lazy-loaded chunks; record which are not  |
| no known id found (and as29s absent too)                 | ids are not literals in the shell bundles |
| random control found                                     | the search is broken; the run settles nothing |

Also measured: whether the same scripts are served WITHOUT cookies (a canary that needs no
session), and the build label (`cfb2h`) the run saw.

Prints no cookie, token, or full URL. Script URLs are reduced to their host + first path segment.

Usage: uv run python scripts/dev/spike_rpc_ids_in_bundle.py [profile]   (default denon82)
"""

from __future__ import annotations

import json
import re
import secrets
import string
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from gflow_cli.auth.cookies import get_cookies_path  # noqa: E402
from gflow_cli.paths import default_home, profile_subdir  # noqa: E402

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
SHELL = "https://flow.google.com/"
# Every id gflow's code names (src/gflow_cli/api/transports) plus nzlxg from the #1192 spike.
KNOWN = (
    "YhhmEf eb1hJf MZZa6b nprQif ogiZ0b fZytfe jwpduf as29s Zzl0ze "
    "jHPbke o8DA4 rqZuUc SPrCad p0UkFb maseQ uwAyfb nzlxg"
).split()
OUT = ROOT / "scripts" / "dev" / "_spike_out" / "rpc_ids_in_bundle.json"


def wiz(text: str, key: str) -> str | None:
    m = re.search(rf'"{key}":"([^"]*)"', text)
    return m.group(1) if m else None


def short(url: str) -> str:
    p = urlsplit(url)
    return f"{p.netloc}/{p.path.strip('/').split('/')[0]}"


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


def fetch_bundle(client: httpx.Client) -> dict:
    shell = client.get(SHELL)
    html = shell.text
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    # Wiz shells also preload module bundles through <link rel="preload" as="script">.
    srcs += re.findall(r'<link[^>]+href="([^"]+)"[^>]+as="script"', html)
    urls = list(dict.fromkeys(urljoin(SHELL, s.replace("&amp;", "&")) for s in srcs))
    scripts = []
    for u in urls:
        r = client.get(u)
        scripts.append((u, r.status_code, r.text if r.status_code == 200 else ""))
    inline = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S))
    return {
        "shell_status": shell.status_code,
        "final_host": urlsplit(str(shell.url)).netloc,
        "bl": wiz(html, "cfb2h"),
        "has_at": bool(wiz(html, "SNlM0e")),
        "scripts": scripts,
        "inline": inline,
    }


def search(bundle: dict, ids: list[str]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    sources = [("inline", bundle["inline"])] + [(short(u), t) for u, _, t in bundle["scripts"]]
    for rpc in ids:
        pat = re.compile(rf"""["']{re.escape(rpc)}["']""")
        hits[rpc] = sorted({name for name, text in sources if pat.search(text)})
    return hits


def contexts(bundle: dict, rpc: str, width: int = 40) -> list[str]:
    text = bundle["inline"] + "\n".join(t for _, _, t in bundle["scripts"])
    return [
        text[max(0, m.start() - width) : m.end() + width].replace("\n", " ")
        for m in re.finditer(rf"""["']{re.escape(rpc)}["']""", text)
    ][:3]


def main(profile: str) -> int:
    control = "".join(secrets.choice(string.ascii_letters) for _ in range(6))
    ids = [*KNOWN, control]
    out: dict = {"control_id": control, "arms": {}}
    for arm, cookies in (("cookies", jar_for(profile)), ("no_cookies", None)):
        with httpx.Client(
            cookies=cookies, timeout=60, follow_redirects=True, headers={"user-agent": UA}
        ) as client:
            b = fetch_bundle(client)
        hits = search(b, ids)
        out["arms"][arm] = {
            "shell_status": b["shell_status"],
            "final_host": b["final_host"],
            "bl": b["bl"],
            "has_SNlM0e": b["has_at"],
            "scripts": [(short(u), s, len(t)) for u, s, t in b["scripts"]],
            "inline_bytes": len(b["inline"]),
            "found": {k: v for k, v in hits.items() if v},
            "missing": [k for k, v in hits.items() if not v],
            "contexts": {k: contexts(b, k) for k in ("as29s", "YhhmEf", "Zzl0ze") if hits.get(k)},
        }
        if arm == "cookies":
            # Shape inventory: every quoted 6-char token in the same call shape as a known id.
            text = b["inline"] + "\n".join(t for _, _, t in b["scripts"])
            out["arms"][arm]["shape_candidates"] = shape_inventory(text, hits)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))
    return 0


def shape_inventory(text: str, hits: dict[str, list[str]]) -> dict:
    """Find the token just before each known id's opening quote, then list every id in that shape."""
    prefixes: Counter[str] = Counter()
    for rpc in (k for k, v in hits.items() if v and k in KNOWN):
        for m in re.finditer(rf"""(.{{0,12}})["']{re.escape(rpc)}["']""", text):
            prefixes[m.group(1)] += 1
    if not prefixes:
        return {"prefixes": {}, "ids": []}
    top = prefixes.most_common(1)[0][0]
    found = re.findall(rf"""{re.escape(top)}["']([A-Za-z0-9]{{5,6}})["']""", text)
    return {
        "prefixes": dict(prefixes.most_common(5)),
        "count": len(set(found)),
        "ids": sorted(set(found)),
    }


XA_RE = re.compile(r'\{Xa:"([A-Za-z0-9]{4,7})"')


async def page_arm(profile: str, wait_s: float = 20.0) -> dict:
    """Rung 2: open a project page and one clip editor; no click, no submit ($0).

    Pre-registered reading for this arm:
    | every known id in some loaded script                      | lazy chunks hold them; replay test decides |
    | an id the page CALLS but no script names                  | ids are built at runtime; bundle grep fails |
    | lazy chunk URLs replay 200 with no cookies, same ids       | a cookie-free canary can follow the chunks |
    """
    import asyncio
    import sqlite3

    sys.path.insert(0, str(ROOT / "scripts" / "dev"))
    from _spike_common import build_client, resolve_profile_dir  # noqa: PLC0415

    db = default_home() / "gflow.db"
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    pid, mid = con.execute(
        "select flow_project_id, flow_media_id from assets where profile_name=? and kind='video'"
        " and flow_project_id is not null and flow_media_id is not null"
        " order by created_at desc limit 1",
        (profile,),
    ).fetchone()
    scripts: dict[str, str] = {}
    called: Counter[str] = Counter()

    async def on_response(resp: object) -> None:
        url = str(getattr(resp, "url", ""))
        if "/_/js/" in url or url.endswith(".js"):
            try:
                scripts[url] = await resp.text()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                scripts[url] = ""

    def on_request(req: object) -> None:
        url = str(getattr(req, "url", ""))
        if "batchexecute" in url:
            from urllib.parse import parse_qs

            for v in parse_qs(urlsplit(url).query).get("rpcids", []):
                for r in v.split(","):
                    called[r] += 1

    async with build_client(resolve_profile_dir(profile)) as client:
        page = client._page  # noqa: SLF001 - dev instrument, same as spike_948
        page.context.on("response", on_response)
        page.context.on("request", on_request)
        for path in (f"/project/{pid}", f"/project/{pid}/edit/{mid}"):
            await page.goto(f"https://flow.google.com{path}")
            await asyncio.sleep(wait_s)
    text = "\n".join(scripts.values())
    found = {k: bool(re.search(rf"""["']{k}["']""", text)) for k in KNOWN}
    xa = sorted(set(XA_RE.findall(text)))
    modules = sorted(
        {seg[2:] for u in scripts for seg in urlsplit(u).path.split("/") if seg.startswith("m=")}
    )
    # Replay every lazy chunk with NO cookies: can a canary follow them without a session?
    replay: dict[str, int] = {}
    replay_text = []
    with httpx.Client(timeout=60, follow_redirects=True, headers={"user-agent": UA}) as anon:
        for u in scripts:
            if "/_/js/" in u:
                r = anon.get(u)
                replay[short(u) + "#" + str(len(replay))] = r.status_code
                replay_text.append(r.text if r.status_code == 200 else "")
    rt = "\n".join(replay_text)
    return {
        "scripts_loaded": len(scripts),
        "module_lists": modules,
        "rpcids_called": dict(called),
        "known_found": found,
        "called_but_not_in_js": sorted(
            k for k in called if not re.search(rf"""["']{k}["']""", text)
        ),
        "xa_descriptor_count": len(xa),
        "xa_descriptors": xa,
        "replay_no_cookies": replay,
        "replay_known_found": {k: bool(re.search(rf"""["']{k}["']""", rt)) for k in KNOWN},
    }


def construct_arm(module_lists: list[str]) -> dict:
    """Rung 1 again, no browser, no cookies: the public shell's script URL with `m=` swapped
    for the feature modules the page arm saw. If this finds every id, a canary needs neither.

    The base (`_b`) and the shared `q*`/`hfcr` lists are left out: they are already loaded.
    """
    mods = [m for m in module_lists if m not in ("_b", "hfcr") and not m.startswith("qabr")]
    with httpx.Client(timeout=60, follow_redirects=True, headers={"user-agent": UA}) as anon:
        html = anon.get(SHELL).text
        src = next(s for s in re.findall(r'<script[^>]+src="([^"]+)"', html) if "/_/js/" in s)
        segs = urljoin(SHELL, src.replace("&amp;", "&")).split("/")
        base = "/".join(s for s in segs if not s.startswith("m="))
        r = anon.get(f"{base}/m={','.join(mods)}")
    text = r.text if r.status_code == 200 else ""
    has = lambda k: bool(re.search(rf"""["']{k}["']""", text))  # noqa: E731
    controls = [
        "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(6))
        for _ in range(200)
    ]
    return {
        "modules": mods,
        "status": r.status_code,
        "bytes": len(text),
        "bl": wiz(html, "cfb2h"),
        "known_found": sum(map(has, KNOWN)),
        "known_total": len(KNOWN),
        "missing": [k for k in KNOWN if not has(k)],
        "random_controls_found": sum(map(has, controls)),
        "xa_descriptor_count": len(set(XA_RE.findall(text))),
    }


if __name__ == "__main__":
    if "--page" in sys.argv:
        import asyncio

        args = [a for a in sys.argv[1:] if a != "--page"]
        res = asyncio.run(page_arm(args[0] if args else "denon82"))
        res["constructed"] = construct_arm(res["module_lists"])
        PAGE_OUT = OUT.with_name("rpc_ids_page_arm.json")
        PAGE_OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(json.dumps(res, indent=1))
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "denon82"))
