r"""#1229: can a page-free reader take the profile's cookies from Chrome's jar, and which ones?

Cost: $0. Read-only. `nzlxg` (balance) is a free read; GETs of https://flow.google.com/ are
free. One headed browser on the profile, opened through FlowApiClient (which takes the profile
lease), loads the app root and idles ~20 s; nothing is clicked or submitted.

Questions
---------
Q1  With no Chrome on the profile: which cookies (NAME + DOMAIN) does the jar hold for
    google.com / flow.google.com / labs.google? Does nzlxg answer 200 with a parseable balance
    with (a) every cookie a browser would send to flow.google.com, (b) a minimal set found by
    backward elimination (drop one name; keep it dropped if the read still passes)?
Q2  With Chrome running on the profile: can browser_cookie3 read the jar (thread and separate
    process)? Can the file be opened / byte-copied / opened by sqlite? Does a wire read work then?
Q3  After Chrome closes: did the jar's metadata (expires/last_access/last_update) move, and does
    the wire still work?
Q4  Rotation conflict: after Chrome has run, is the in-memory snapshot taken before it opened
    still accepted? Which cookie NAMES changed value? (Session survival is checked afterwards
    with `gflow auth status` + `gflow credits user`, outside this script.)
Q5  Encryption prefixes of the Google-domain cookie values (v10/v11/v20/other, counts only) and
    whether browser_cookie3 decrypted every one.

Every wire response's Set-Cookie NAMES are recorded (does Google try to rotate on a wire read?).

Pre-registered readings (written before run 1)
----------------------------------------------
| Observation | Reading |
|---|---|
| Q1 baseline GET has SNlM0e and nzlxg 200 + parsed | the jar alone is enough for a page-free read |
| Q1 baseline GET has no SNlM0e | jar not accepted today: Q1 UNMEASURED, no ablation run |
| no-cookie control has SNlM0e, or no-cookie POST is 200 | the controls fail: Q1 settles nothing |
| junk-`at` control is 200 | `at` is not checked: the read does not prove the token matters |
| full set fails at the end of ablation | the session changed under the spike: ablation VOID |
| elimination ends with set S and S passes alone | S is *a* minimal set (order-dependent), not *the* minimal set |
| Q2 every disk probe fails with a sharing violation (WinError 32 / sqlite cannot open) | Chrome holds the jar exclusively: a running browser forces the page path (or an in-memory jar) |
| Q2 sqlite `immutable=1` or copy2 succeeds while Chrome runs | the jar is readable concurrently; WireReader need not fall back on a running browser |
| Q2 browser_cookie3 says "Unable to read database file" | lock, not DPAPI (its message collapses every method; the direct probes separate them) |
| Q2 browser_cookie3 raises a DPAPI/MAC error | decryption, not a lock |
| Q3/Q4 stale snapshot nzlxg 200 + parsed | rotation during a browser run does not invalidate the old jar (today) |
| Q3/Q4 stale snapshot fails, fresh jar passes | the reader must re-read the jar after any browser run |
| Q5 any v20 prefix | app-bound encryption present; browser_cookie3 cannot decrypt those values |
| 0 changed names after Chrome ran | no rotation observed in this window; settles nothing about longer runs |

Prints and writes NO cookie value, `at`, f.sid, bl value or id: only names, domains, counts,
statuses, booleans, and "parsed: yes/no" for the balance.

Re-run mode, added after run 1 and before run 2. In run 1 another agent's e2e suite was using
the same profile. The profile lease prevents overlapping Chrome sessions, but it does not stop a
foreign session from running in the gaps between this script's phases. `--q34` skips the Q1
ablation, takes the snapshot and one baseline read, then runs Q2-Q4. It records UTC timestamps
for every lease hand-over, so the gaps where a foreign session could slip in are bounded and
visible. Pre-registered reading for run 2: if the stale snapshot fails here while it passed in
run 1, the run-1 Q4 pass is not reproducible and Q4 is unmeasured, not passed.

Usage: uv run python scripts/dev/spike_cookie_jar_wire.py [profile] [--q34]   (default denon82)
"""

# ruff: noqa: E501  (the pre-registered readings table is wider than 100 columns)
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "dev"))

from _spike_common import build_client, resolve_profile_dir  # noqa: E402

from gflow_cli.api.dto import CreditsInfo  # noqa: E402
from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: E402
from gflow_cli.paths import get_cookies_path  # noqa: E402
from gflow_cli.profile_lease import ProfileLease  # noqa: E402

ORIGIN = "https://flow.google.com"
BE = f"{ORIGIN}/_/AiSandboxAngularFrontend/data/batchexecute"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
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
OUT_DIR = ROOT / "scripts" / "dev" / "_spike_out"
OUT = OUT_DIR / "cookie_jar_wire.json"
SENT_DOMAINS = {"google.com", ".google.com", "flow.google.com", ".flow.google.com"}
TRIAL_CAP = 40
GAP_S = 1.1
_last_call = [0.0]
_trials = [0]


def now() -> str:
    return time.strftime("%H:%M:%S", time.gmtime())


def redact(msg: str) -> str:
    # Windows drive paths and POSIX home paths (/home/x, /Users/x, /root)
    msg = re.sub(r"[A-Za-z]:[\\/][^'\"\s]*", "<path>", msg)
    return re.sub(r"(?:/home/|/Users/|/root\b)[^'\"\s]*", "<path>", msg)[:200]


def exc_info(e: BaseException) -> dict[str, Any]:
    return {
        "ok": False,
        "class": type(e).__name__,
        "winerror": getattr(e, "winerror", None),
        "errno": getattr(e, "errno", None),
        "message": redact(str(e)),
    }


def wiz(text: str, key: str) -> str | None:
    m = re.search(rf'"{key}":"([^"]*)"', text)
    return m.group(1) if m else None


def setcookie_names(r: httpx.Response) -> list[str]:
    return sorted({h.split("=", 1)[0].strip() for h in r.headers.get_list("set-cookie")})


def pace() -> None:
    wait = GAP_S - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


# ---------------------------------------------------------------- jar reads (values in memory)


def read_jar(cookie_file: Path, key_file: Path) -> list[Any]:
    import browser_cookie3

    return list(browser_cookie3.chrome(cookie_file=cookie_file, key_file=key_file))


def sent_to_flow(cookies: list[Any]) -> list[Any]:
    """Cookies a browser sends on GET https://flow.google.com/ (domain match, path '/')."""
    return [c for c in cookies if c.domain.lower() in SENT_DOMAINS and c.path == "/"]


def header(cookies: list[Any]) -> str:
    return "; ".join(f"{c.name}={c.value}" for c in cookies)


def inventory(cookies: list[Any]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {"google.com": [], "flow.google.com": [], "labs.google": []}
    for c in cookies:
        d = c.domain.lstrip(".").lower()
        for g in groups:
            if d == g or (
                g == "google.com" and d.endswith(".google.com") and d != "flow.google.com"
            ):
                groups[g].append(f"{c.name} @ {c.domain} {c.path}")
                break
    return {g: sorted(v) for g, v in groups.items()}


def metadata(cookie_file: Path) -> dict[str, Any]:
    """Per (host,name,path): expiry/access/update + value-free encryption prefix."""
    uri = cookie_file.absolute().as_uri() + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            "select host_key, name, path, expires_utc, last_access_utc, last_update_utc,"
            " substr(encrypted_value,1,3), length(value) from cookies"
        ).fetchall()
    finally:
        con.close()
    meta: dict[str, Any] = {}
    for host, name, path, exp, acc, upd, prefix, vlen in rows:
        if "google" not in host:
            continue
        meta[f"{name}@{host}{path}"] = {
            "exp": exp,
            "acc": acc,
            "upd": upd,
            "prefix": (prefix or b"").decode("latin1") if prefix else ("plain" if vlen else ""),
        }
    return meta


def prefix_counts(meta: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in meta.values():
        p = m["prefix"] if m["prefix"] in ("v10", "v11", "v20", "plain", "") else "other"
        out[p or "empty"] = out.get(p or "empty", 0) + 1
    return out


def meta_diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    common = a.keys() & b.keys()
    return {
        "count_before": len(a),
        "count_after": len(b),
        "added": sorted(b.keys() - a.keys()),
        "removed": sorted(a.keys() - b.keys()),
        "expires_changed": sorted(k for k in common if a[k]["exp"] != b[k]["exp"]),
        "last_access_changed": len([k for k in common if a[k]["acc"] != b[k]["acc"]]),
        "last_update_changed": sorted(k for k in common if a[k]["upd"] != b[k]["upd"]),
    }


def value_changes(old: list[Any], new: list[Any]) -> dict[str, Any]:
    def h(cs: list[Any]) -> dict[str, str]:
        return {
            f"{c.name}@{c.domain}{c.path}": hashlib.sha256(c.value.encode()).hexdigest() for c in cs
        }

    a, b = h(old), h(new)
    return {
        "changed_value_names": sorted(k for k in a.keys() & b.keys() if a[k] != b[k]),
        "added": sorted(b.keys() - a.keys()),
        "removed": sorted(a.keys() - b.keys()),
    }


# ---------------------------------------------------------------- wire


def get_shell(cookie_header: str | None) -> tuple[dict[str, Any], dict[str, str | None]]:
    pace()
    hdr = dict(NAV_HEADERS)
    if cookie_header:
        hdr["cookie"] = cookie_header
    with httpx.Client(timeout=30, follow_redirects=False) as hx:
        r = hx.get(f"{ORIGIN}/", headers=hdr)
    at = wiz(r.text, "SNlM0e") if r.status_code == 200 else None
    rec = {
        "status": r.status_code,
        "location_host": urlsplit(r.headers.get("location", "")).netloc or None,
        "bytes": len(r.content),
        "has_SNlM0e": bool(at),
        "set_cookie": setcookie_names(r),
    }
    return rec, {"at": at, "fsid": wiz(r.text, "FdrFJe"), "bl": wiz(r.text, "cfb2h")}


def nzlxg(cookie_header: str | None, tok: dict[str, str | None], at: str | None = None) -> dict:
    pace()
    q = {
        "rpcids": "nzlxg",
        "source-path": "/",
        "bl": tok["bl"],
        "f.sid": tok["fsid"],
        "hl": "en",
        "_reqid": 100001,
        "rt": "c",
    }
    body = urlencode(
        {"f.req": json.dumps([[["nzlxg", "[]", None, "generic"]]]), "at": at or tok["at"]}
    )
    hdr = {
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
        "x-same-domain": "1",
        "origin": ORIGIN,
        "referer": f"{ORIGIN}/",
        "user-agent": UA,
    }
    if cookie_header:
        hdr["cookie"] = cookie_header
    url = f"{BE}?{urlencode({k: v for k, v in q.items() if v is not None})}"
    with httpx.Client(timeout=30, follow_redirects=False) as hx:
        r = hx.post(url, content=body, headers=hdr)
    parsed = False
    if r.status_code == 200:
        for rid, p in parse_frames(r.text):
            if rid == "nzlxg":
                try:
                    CreditsInfo.from_nzlxg(p)
                    parsed = True
                except ValueError:
                    pass
    return {"status": r.status_code, "parsed": parsed, "set_cookie": setcookie_names(r)}


def wire_read(cookies: list[Any] | None) -> dict[str, Any]:
    """One trial: 1 GET + at most 1 POST, both with exactly `cookies` (no re-mint)."""
    _trials[0] += 1
    ch = header(cookies) if cookies else None
    shell, tok = get_shell(ch)
    out: dict[str, Any] = {"get": shell}
    if tok["at"]:
        out["post"] = nzlxg(ch, tok)
    out["ok"] = bool(out.get("post", {}).get("parsed"))
    return out


# ---------------------------------------------------------------- phases


def phase_q1(cookie_file: Path, key_file: Path) -> tuple[dict[str, Any], list[Any], Any]:
    rep: dict[str, Any] = {}
    try:
        all_cookies = read_jar(cookie_file, key_file)
        rep["browser_cookie3_read"] = {"ok": True, "count": len(all_cookies)}
    except Exception as e:  # noqa: BLE001 - the exception IS the finding
        rep["browser_cookie3_read"] = exc_info(e)
        return rep, [], None
    google = [c for c in all_cookies if "google" in c.domain]
    rep["q5_google_cookies"] = len(google)
    rep["q5_google_empty_value_after_decrypt"] = sum(1 for c in google if not c.value)
    rep["inventory"] = inventory(all_cookies)
    sent = sent_to_flow(all_cookies)
    rep["sent_to_flow_names"] = sorted(f"{c.name} @ {c.domain}" for c in sent)
    rep["google_non_root_path_names"] = sorted(
        f"{c.name} @ {c.domain} {c.path}"
        for c in all_cookies
        if c.domain.lower() in SENT_DOMAINS and c.path != "/"
    )

    base = wire_read(sent)
    rep["baseline_start"] = base
    if not base["get"]["has_SNlM0e"]:
        rep["verdict"] = "UNMEASURED: baseline shell carries no SNlM0e; no ablation"
        return rep, all_cookies, None
    _, tok = get_shell(header(sent))
    _trials[0] += 1
    rep["control_no_cookies_get"] = get_shell(None)[0]
    rep["control_no_cookies_post_with_good_at"] = nzlxg(None, tok)
    rep["control_junk_at"] = nzlxg(header(sent), tok, at="AJunk:0")
    _trials[0] += 2

    names = sorted({c.name for c in sent})
    keep = set(names)
    steps = []
    budget = TRIAL_CAP - _trials[0] - 2  # reserve final-set confirm + end baseline
    for name in names:
        if budget <= 0:
            steps.append({"drop": name, "skipped": "trial cap"})
            continue
        trial = keep - {name}
        res = wire_read([c for c in sent if c.name in trial])
        budget -= 1
        needed = not res["ok"]
        if not needed:
            keep = trial
        steps.append(
            {
                "drop": name,
                "needed": needed,
                "get_status": res["get"]["status"],
                "location_host": res["get"]["location_host"],
                "has_SNlM0e": res["get"]["has_SNlM0e"],
                "post_status": res.get("post", {}).get("status"),
            }
        )
    rep["ablation_steps"] = steps
    rep["minimal_set_names"] = sorted(keep)
    rep["minimal_set_alone"] = wire_read([c for c in sent if c.name in keep])
    rep["baseline_end"] = wire_read(sent)
    rep["ablation_void"] = not rep["baseline_end"]["ok"]
    rep["trials_used"] = _trials[0]
    return rep, all_cookies, keep


SUBPROC = r"""
import sys, re
import browser_cookie3
try:
    cs = list(browser_cookie3.chrome(cookie_file=sys.argv[1], key_file=sys.argv[2]))
    print("OK", len(cs))
except Exception as e:
    print("ERR", type(e).__name__, re.sub(r"[A-Za-z]:[\\/][^'\"\s]*", "<path>", str(e))[:200])
"""


def disk_probes(cookie_file: Path, key_file: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        with open(cookie_file, "rb") as f:
            f.read(16)
        out["open_rb"] = {"ok": True}
    except Exception as e:  # noqa: BLE001
        out["open_rb"] = exc_info(e)
    for label, opt in (("sqlite_ro", "?mode=ro"), ("sqlite_ro_immutable", "?mode=ro&immutable=1")):
        try:
            con = sqlite3.connect(cookie_file.absolute().as_uri() + opt, uri=True)
            n = con.execute("select count(*) from cookies").fetchone()[0]
            con.close()
            out[label] = {"ok": True, "rows": n}
        except Exception as e:  # noqa: BLE001
            out[label] = exc_info(e)
    copy_dir = OUT_DIR / "cookie_copy"
    copy_dir.mkdir(parents=True, exist_ok=True)
    for side in ("", "-journal", "-wal"):
        src = Path(str(cookie_file) + side)
        if not src.exists():
            out[f"copy2{side or '_main'}"] = {"ok": None, "exists": False}
            continue
        try:
            shutil.copy2(src, copy_dir / f"Cookies{side}")
            out[f"copy2{side or '_main'}"] = {"ok": True}
        except Exception as e:  # noqa: BLE001
            out[f"copy2{side or '_main'}"] = exc_info(e)
    try:
        out["browser_cookie3_on_copy"] = {
            "ok": True,
            "count": len(read_jar(copy_dir / "Cookies", key_file)),
        }
    except Exception as e:  # noqa: BLE001
        out["browser_cookie3_on_copy"] = exc_info(e)
    try:
        out["browser_cookie3_thread"] = {"ok": True, "count": len(read_jar(cookie_file, key_file))}
    except Exception as e:  # noqa: BLE001
        out["browser_cookie3_thread"] = exc_info(e)
    p = subprocess.run(  # noqa: S603 - our own interpreter and literal code
        [sys.executable, "-c", SUBPROC, str(cookie_file), str(key_file)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    out["browser_cookie3_subprocess"] = redact(p.stdout.strip() or p.stderr.strip()[-200:])
    return out


async def phase_q2(pdir: Path, cookie_file: Path, key_file: Path, old: list[Any]) -> dict:
    rep: dict[str, Any] = {}
    try:
        async with build_client(pdir) as client:
            page = client._page  # noqa: SLF001 - dev instrument
            await page.goto(f"{ORIGIN}/", wait_until="domcontentloaded", timeout=60_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:  # noqa: BLE001 - idle is best effort
                rep["networkidle_timeout"] = True
            rep["chrome_opened_utc"] = now()
            await asyncio.sleep(20)
            rep["chrome_running"] = True
            rep["probes"] = await asyncio.to_thread(disk_probes, cookie_file, key_file)
            ctx = await page.context.cookies()
            rep["context_cookies_flow_sendable"] = len(
                [c for c in ctx if c["domain"].lower() in SENT_DOMAINS and c["path"] == "/"]
            )
            try:
                open_meta = await asyncio.to_thread(metadata, cookie_file)
                rep["metadata_while_open"] = {"ok": True, "rows": len(open_meta)}
            except Exception as e:  # noqa: BLE001
                rep["metadata_while_open"] = exc_info(e)
            live = None
            if rep["probes"]["browser_cookie3_thread"].get("ok"):
                live, src = read_jar(cookie_file, key_file), "disk_jar_while_open"
            elif rep["probes"]["browser_cookie3_on_copy"].get("ok"):
                live, src = read_jar(OUT_DIR / "cookie_copy" / "Cookies", key_file), "byte_copy"
            else:
                src = "pre_open_in_memory_snapshot (disk unreadable)"
            rep["wire_while_open_source"] = src
            rep["wire_while_open"] = await asyncio.to_thread(wire_read, sent_to_flow(live or old))
    finally:  # the byte copy holds cookie values: remove it on every exit path
        shutil.rmtree(OUT_DIR / "cookie_copy", ignore_errors=True)
    return rep


def main(profile: str, *, q34_only: bool = False) -> int:
    pdir = resolve_profile_dir(profile)
    cookie_file, key_file = get_cookies_path(pdir), pdir / "Local State"
    try:
        import shadowcopy  # noqa: F401

        has_shadow = True
    except ImportError:
        has_shadow = False
    out: dict[str, Any] = {
        "env": {
            "shadowcopy_importable": has_shadow,
            "elevated": bool(ctypes.windll.shell32.IsUserAnAdmin()),
            "cookie_file_rel": str(cookie_file.relative_to(pdir)),
        }
    }
    # Q1 + Q5 under the profile lease: no Chrome can start on the profile meanwhile.
    tl: dict[str, str] = {}
    out["timeline_utc"] = tl
    with ProfileLease(pdir):
        tl["lease1_acquired"] = now()
        meta_before = metadata(cookie_file)
        out["q5_prefix_counts_google"] = prefix_counts(meta_before)
        if q34_only:
            old = read_jar(cookie_file, key_file)
            out["q34_baseline_with_snapshot"] = wire_read(sent_to_flow(old))
        else:
            out["q1"], old, _ = phase_q1(cookie_file, key_file)
        tl["lease1_released"] = now()
    if not old:
        print(json.dumps(out, indent=1))
        return 1
    out["q2"] = asyncio.run(phase_q2(pdir, cookie_file, key_file, sent_to_flow(old)))
    tl["chrome_closed"] = now()
    time.sleep(3)  # let Chrome flush and release the file
    with ProfileLease(pdir):
        tl["lease2_acquired"] = now()
        meta_after = metadata(cookie_file)
        fresh = read_jar(cookie_file, key_file)
        out["q3_metadata_diff"] = meta_diff(meta_before, meta_after)
        out["q4_value_changes_flow_sendable"] = value_changes(
            sent_to_flow(old), sent_to_flow(fresh)
        )
        out["q4_wire_stale_snapshot"] = wire_read(sent_to_flow(old))
        out["q3_wire_fresh_jar"] = wire_read(sent_to_flow(fresh))
        tl["lease2_released"] = now()
    out["total_trials"] = _trials[0]
    dest = OUT.with_name("cookie_jar_wire_q34.json") if q34_only else OUT
    OUT.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--q34"]
    raise SystemExit(main(args[0] if args else "denon82", q34_only="--q34" in sys.argv))
