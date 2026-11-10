"""#1227 spike ($0): do project create, rename and upload work as direct batchexecute calls?

No browser. The profile's saved cookies and the app shell's ``at``/``f.sid``/``bl`` are
read exactly as ``src/gflow_cli/api/flow_wire.py`` reads them for ``nzlxg`` (its private
helpers are reused here; ``src`` is not changed and its allow-list is not widened). Each
request copies the frame the app itself sent in the #1226 capture
(``scripts/dev/spike_rpc_map_1226.py``), changing only the title, project id or file:

  jHPbke  ["projects/*", [null, [title]], [null, 22]]          -> [project_id, [title]]
  o8DA4   ["projects/<id>", [title], [["project_title"]], [null, 22]] -> [title]
  maseQ   NOT sent: see below
  QI2zvc  ["projects/<id>"]                                    (delete; cleanup only)
  ngNC2   ["tools/PINHOLE/projects/<id>"]                       -> [id, [title, ...]] (read)
  UpteDb  ["projects/*", 21, null, null, null, null, [1]]       -> project listing (read)
  Zzl0ze  ["projects/<id>", null, null, null, [1]]              -> project records (read)

Order: create -> verify -> rename -> verify -> delete the project (QI2zvc) -> verify it is
gone. Every project made here is titled
``spike1227-*``; cleanup deletes every such project the listing shows, so an ambiguous
create cannot leave one behind. The operator also checks the listing in the browser after.

Pre-registered reading (written before the run), per call:
- HTTP 200, a frame for the id, AND the object read back (create/rename: the title in
  ``ngNC2`` and in the ``UpteDb`` listing; upload: the media id in the project's
  ``Zzl0ze``) -> a direct write tier is possible for that call.
- An error frame or a non-200 -> refused: record the status and the error code/reasons.
- 200 with no frame, or a frame whose object never reads back -> ambiguous: unmeasured.
- ``maseQ``: if the captured app frame carries an opaque token slot (a reCAPTCHA-shaped
  ``<tok:N>`` string), it is NOT sent; the reading is "needs a page-minted token".
- Applied before this script ran: the app's own ``maseQ`` (captured by route-abort in
  #1226, never sent) carries a 2425-char token at ``[0][10][0]``, right after the page
  POSTs ``recaptcha/enterprise/reload``. So the upload is "needs a page-minted token" and
  this script has no upload arm: sending one would mean emulating reCAPTCHA.
- If the delete is not proven (the project still reads back), STOP and report: nothing
  else is created.

Nothing printed holds a cookie, ``at``, full id or signed URL: ids print as 8 chars.

Usage:
    python scripts/dev/spike_direct_writes_1227.py --profile denon82 [--cleanup-only]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from gflow_cli.api import flow_wire as fw
from gflow_cli.api.transports.batchexecute import parse_frames, rpc_errors
from gflow_cli.config import get_settings

OUT = Path("scripts/dev/_spike_out/1227")
PREFIX = "spike1227-"


class Wire:
    def __init__(self, client: httpx.AsyncClient, tokens: Any, headers: dict[str, str]) -> None:
        self.client, self.tokens, self.headers = client, tokens, headers
        self.reqid = 1000 + secrets.randbelow(9000)

    async def call(self, rpcid: str, inner: Any, source_path: str = "/") -> dict[str, Any]:
        self.reqid += 100_000
        query = {"rpcids": rpcid, "source-path": source_path, "bl": self.tokens.bl}
        query |= {"f.sid": self.tokens.fsid, "hl": "en", "_reqid": str(self.reqid), "rt": "c"}
        freq = json.dumps([[[rpcid, json.dumps(inner), None, "generic"]]])
        body = urlencode({"f.req": freq, "at": self.tokens.at})
        response = await self.client.post(
            f"{fw._BATCHEXECUTE}?{urlencode({k: v for k, v in query.items() if v})}",  # noqa: SLF001
            content=body,
            headers={
                **self.headers,
                "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
                "x-same-domain": "1",
                "origin": fw._ORIGIN,  # noqa: SLF001
                "referer": f"{fw._ORIGIN}{source_path}",  # noqa: SLF001
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            },
        )
        text = response.text
        frames = [p for rid, p in parse_frames(text) if rid == rpcid]
        errors = [[e.code, list(e.reasons)] for e in rpc_errors(text) if e.rpcid == rpcid]
        return {
            "status": response.status_code,
            "frame": frames[0] if frames else None,
            "has_frame": bool(frames),
            "errors": errors,
        }


def short(value: Any) -> str:
    return str(value)[:8] if value else "-"


def report(name: str, r: dict[str, Any], **extra: Any) -> None:
    line = {
        "call": name,
        "status": r["status"],
        "has_frame": r["has_frame"],
        "errors": r["errors"],
        **extra,
    }
    print(json.dumps(line))
    with (OUT / "result.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


async def listing(w: Wire) -> list[tuple[str, str]]:
    r = await w.call("UpteDb", ["projects/*", 21, None, None, None, None, [1]])
    rows = (r["frame"] or [[]])[0] or []
    return [(row[0], (row[1] or [""])[0]) for row in rows if isinstance(row, list)]


async def title_of(w: Wire, pid: str) -> tuple[dict[str, Any], str | None]:
    r = await w.call("ngNC2", [f"tools/PINHOLE/projects/{pid}"], f"/project/{pid}")
    frame = r["frame"]
    return r, (frame[1][0] if isinstance(frame, list) and len(frame) > 1 and frame[1] else None)


async def delete_all_spike_projects(w: Wire) -> bool:
    gone = True
    for pid, title in await listing(w):
        if title.startswith(PREFIX):
            r = await w.call("QI2zvc", [f"projects/{pid}"])
            report("QI2zvc delete", r, project=short(pid), reply=str(r["frame"])[:40])
            _, still = await title_of(w, pid)
            in_list = any(p == pid for p, _ in await listing(w))
            print(
                json.dumps(
                    {"after_delete": short(pid), "ngNC2_title": bool(still), "in_UpteDb": in_list}
                )
            )
            gone = gone and not in_list
    return gone


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--cleanup-only", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    profile_dir = get_settings().profile_subdir(a.profile)
    ua = fw._user_agent(profile_dir)  # noqa: SLF001
    cookies = fw.read_cookies_for(profile_dir)
    assert ua and cookies, "no UA or cookies for this profile"
    headers = {
        "user-agent": ua,
        "accept-language": "en-US,en;q=0.9",
        "cookie": "; ".join(f"{n}={v}" for n, v in cookies),
    }
    async with httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(60, connect=5),
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
    ) as client:
        tokens = await fw._shell_tokens(client, profile_dir, headers)  # noqa: SLF001
        w = Wire(client, tokens, headers)
        before = await listing(w)
        print(
            json.dumps(
                {
                    "listing_before": len(before),
                    "spike_before": sum(t.startswith(PREFIX) for _, t in before),
                }
            )
        )
        if a.cleanup_only:
            print(json.dumps({"cleanup_ok": await delete_all_spike_projects(w)}))
            return

        title = f"{PREFIX}{secrets.token_hex(3)}"
        r = await w.call("jHPbke", ["projects/*", [None, [title]], [None, 22]])
        pid = r["frame"][0] if r["has_frame"] and r["frame"] else None
        _, read_title = await title_of(w, pid) if pid else (None, None)
        listed = any(p == pid and t == title for p, t in await listing(w))
        report(
            "jHPbke create",
            r,
            project=short(pid),
            ngNC2_title_match=read_title == title,
            in_UpteDb=listed,
        )
        if not pid:
            print(json.dumps({"cleanup_ok": await delete_all_spike_projects(w)}))
            return

        new_title = f"{title}-renamed"
        r = await w.call(
            "o8DA4",
            [f"projects/{pid}", [new_title], [["project_title"]], [None, 22]],
            f"/project/{pid}",
        )
        _, read_title = await title_of(w, pid)
        listed = any(p == pid and t == new_title for p, t in await listing(w))
        report(
            "o8DA4 rename",
            r,
            reply_title_match=(r["frame"] or [None])[0] == new_title,
            ngNC2_title_match=read_title == new_title,
            in_UpteDb=listed,
        )

        print(json.dumps({"cleanup_ok": await delete_all_spike_projects(w)}))


if __name__ == "__main__":
    asyncio.run(main())
