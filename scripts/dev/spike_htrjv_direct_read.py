"""Spike (#1375): can Flow's model catalog (`HTrJv`) be read with no browser, like credits?

$0: one project page load (read-only), then plain HTTP reads. Nothing is submitted.

Part A (browser, through gflow's own transport): load a project and capture the exact
`HTrJv` request the page sends — its `f.req` arguments and `source-path` — and the reply's
shape (family count, retirement dates).

Part B (no browser, after the browser has closed): the credits path's own machinery
(`api/flow_wire.py`: saved cookies, the shell's `at`/`f.sid`/`bl`, one batchexecute POST)
sends `HTrJv` with the captured arguments, and again with `[]`, and compares the reply with
Part A's.

Pre-registered readings:

| Outcome | Reading |
|---|---|
| B returns the same families and dates as A | the catalog is a direct read; a browserless `gflow models` refresh is possible |
| B with captured args works, `[]` does not | direct read works but must send the page's arguments |
| B returns an RPC error or 401/redirect | not a direct read today (or outside the cookie window: see `direct-read-window-is-psidts-age`); keep it on the browser path |

Caveat known before running: the credits direct read only works within ~1.5-2.5 h of a
browser visit (`__Secure-1PSIDTS` age). Part A is such a visit, so a Part B success proves
the request shape, not that the read works at any time.

    uv run python scripts/dev/spike_htrjv_direct_read.py --profile NAME --project UUID

Evidence: `scripts/dev/_spike_out/htrjv_direct_<ts>/` (gitignored). Prints shapes only.
"""

# ruff: noqa: SLF001, E501 - a spike reuses flow_wire's private steps on purpose

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from gflow_cli.api import flow_wire as fw
from gflow_cli.api.transports._mc_common import MIGRATED_PROJECT_URL
from gflow_cli.api.transports.batchexecute import parse_frames, rpc_errors
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import get_settings

OUT_ROOT = Path(__file__).parent / "_spike_out"
RPC = "HTrJv"


def families(payload: Any) -> list[tuple[str, str | None, Any]]:
    """(display name, family key, index-6 value) per model family in an HTrJv payload."""
    found: list[tuple[str, str | None, Any]] = []

    def walk(node: Any) -> None:
        if not isinstance(node, list):
            return
        if (
            len(node) >= 4
            and isinstance(node[0], str)
            and isinstance(node[1], list)
            and node[1]
            and isinstance(node[1][0], list)
            and node[1][0]
            and isinstance(node[1][0][0], str)
        ):
            found.append(
                (
                    node[0],
                    node[3] if isinstance(node[3], str) else None,
                    node[6] if len(node) > 6 else None,
                )
            )
        for child in node:
            walk(child)

    walk(payload)
    return found


async def part_a(profile_dir: Path, project: str) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    transport = UiAutomationTransport()
    await transport.setup(profile_dir)
    try:
        page = transport._page
        assert page is not None

        def on_request(request: Any) -> None:
            url = str(request.url)
            if "batchexecute" not in url or RPC not in url or "request" in captured:
                return
            form = parse_qs(request.post_data or "")
            freq = json.loads(form.get("f.req", ["null"])[0])
            for entry in freq[0] if freq else []:
                if entry and entry[0] == RPC:
                    captured["request"] = {
                        "args": entry[1],
                        "source_path": parse_qs(urlparse(url).query).get("source-path", [None])[0],
                    }

        async def on_response(response: Any) -> None:
            if "batchexecute" not in str(response.url) or RPC not in str(response.url):
                return
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001
                return
            for rid, payload in parse_frames(text):
                if rid == RPC and "payload" not in captured:
                    captured["payload"] = payload

        page.on("request", on_request)
        page.on("response", on_response)
        await page.goto(
            MIGRATED_PROJECT_URL.format(project_id=project), wait_until="domcontentloaded"
        )
        for _ in range(40):
            if "payload" in captured and "request" in captured:
                break
            await page.wait_for_timeout(500)
    finally:
        await transport.teardown()
    return captured


async def part_b(profile_dir: Path, args: str, source_path: str) -> dict[str, Any]:
    ua = fw._user_agent(profile_dir)
    cookies = await fw._read_cookies(profile_dir)
    headers = {
        "user-agent": ua or "",
        "accept-language": "en-US,en;q=0.9",
        "cookie": "; ".join(f"{n}={v}" for n, v in cookies),
    }
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=15) as client:
        tokens = await fw._shell_tokens(client, profile_dir, headers)
        query = {
            "rpcids": RPC,
            "source-path": source_path,
            "bl": tokens.bl,
            "f.sid": tokens.fsid,
            "hl": "en",
            "_reqid": "123456",
            "rt": "c",
        }
        body = urlencode({"f.req": json.dumps([[[RPC, args, None, "generic"]]]), "at": tokens.at})
        request = httpx.Request(
            "POST",
            f"{fw._BATCHEXECUTE}?{urlencode({k: v for k, v in query.items() if v is not None})}",
            content=body,
            headers={
                **headers,
                "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
                "x-same-domain": "1",
                "origin": fw._ORIGIN,
                "referer": f"{fw._ORIGIN}/",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            },
        )
        started = time.perf_counter()
        response = await client.send(request)
    frames = [p for rid, p in parse_frames(response.text) if rid == RPC]
    return {
        "status": response.status_code,
        "ms": round((time.perf_counter() - started) * 1000),
        "payload": frames[0] if frames else None,
        "rpc_error_codes": [e.code for e in rpc_errors(response.text)],
    }


async def main(profile: str, project: str) -> None:
    out = OUT_ROOT / f"htrjv_direct_{time.strftime('%Y%m%dT%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    profile_dir = get_settings().profile_subdir(profile)
    a = await part_a(profile_dir, project)
    req = a.get("request") or {}
    print("A request args:", req.get("args"), "source-path:", req.get("source_path"))
    fa = families(a.get("payload"))
    print(
        "A families:",
        len(fa),
        "with index-6 date:",
        [(n, k, d) for n, k, d in fa if isinstance(d, list)],
    )
    results = {}
    for label, args in (("captured", req.get("args") or "[]"), ("empty", "[]")):
        b = await part_b(profile_dir, args, req.get("source_path") or "/")
        fb = families(b["payload"])
        same = [(n, k, d) for n, k, d in fb] == [(n, k, d) for n, k, d in fa]
        print(
            f"B[{label}] status={b['status']} ms={b['ms']} families={len(fb)} identical_to_A={same} rpc_errors={b['rpc_error_codes']}"
        )
        results[label] = b
    (out / "a.json").write_text(json.dumps(a, default=str), encoding="utf-8")
    (out / "b.json").write_text(json.dumps(results, default=str), encoding="utf-8")
    print("evidence:", out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="#1375: HTrJv direct read")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True)
    cli = parser.parse_args()
    asyncio.run(main(cli.profile, cli.project))
