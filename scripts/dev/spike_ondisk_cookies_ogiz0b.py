r"""Spike: do on-disk cookies work for direct httpx ogiZ0b image generation?

The existing direct wire path (flow_wire.py) reads cookies from the Chrome profile's
SQLite DB for nzlxg (credits) and pGCYOe (trash). This spike tests whether those same
on-disk cookies also work for ogiZ0b (image generation, 15-20s server-side render).

The browser is still needed for reCAPTCHA minting, but the POST uses ONLY on-disk
cookies and shell-derived tokens — the browser's live cookies are never extracted.

Pre-registered readings:
    - HTTP 200 + image records → on-disk cookies work; the browser is needed ONLY for
      reCAPTCHA minting (not for cookies)
    - HTTP 200 but code 7 (PUBLIC_ERROR_UNUSUAL_ACTIVITY) → cookies are accepted but
      the reCAPTCHA flow needs the same session
    - HTTP 401 → on-disk cookies are rejected (the shell GET also fails)
    - HTTP 400 / code 3 → cookies work but the frame or tokens are mismatched

Cost: 1 image if accepted, $0 if rejected.
"""

from __future__ import annotations

import asyncio
import glob as _glob
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from gflow_cli.api.flow_wire import (  # noqa: E402
    _user_agent,
    _wiz,
    extract_wiz_token,
)
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
_PROMPT = "a golden retriever puppy playing in autumn leaves, oil painting style"
_RECAPTCHA_ACTION = "IMAGE_GENERATION"


def _redact(s: str, *, keep: int = 20) -> str:
    if len(s) <= keep:
        return s
    return s[:keep] + f"...({len(s)} chars)"


_findings: list[dict[str, Any]] = []


def _log(tag: str, data: dict[str, Any]) -> None:
    record = {"ts": time.strftime("%H:%M:%S"), "tag": tag, **data}
    _findings.append(record)
    step(tag, json.dumps(data, indent=None, default=str))


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="denon82")
    args = parser.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    out_path = default_out_path("spike_ondisk_cookies_ogiz0b", ".jsonl")
    step("START", f"profile={args.profile}")

    # ================================================================
    # PHASE 1: Read cookies from disk (NO browser)
    # ================================================================
    step("COOKIES", "reading cookies from Chrome SQLite DB...")
    try:
        disk_cookies = read_cookies_for(profile_dir)
    except Exception as exc:
        step("ABORT", f"cookie read failed: {exc}")
        return

    cookie_names = [name for name, _ in disk_cookies]
    step("COOKIES", f"{len(disk_cookies)} cookies from disk: {sorted(set(cookie_names))[:10]}...")
    _log("disk_cookies", {"count": len(disk_cookies), "names_sample": sorted(set(cookie_names))[:10]})

    # Build the cookie header string
    cookie_str = "; ".join(f"{name}={value}" for name, value in disk_cookies)

    # Build UA from Chrome version on disk
    ua = _user_agent(profile_dir)
    if not ua:
        step("ABORT", "no Chrome version found in profile")
        return
    step("AUTH", f"UA: {_redact(ua, keep=40)}")

    # ================================================================
    # PHASE 2: Get shell tokens (at, fsid, bl) using disk cookies
    # ================================================================
    step("SHELL", "fetching shell tokens from flow.google.com using disk cookies...")
    disk_headers = {
        "user-agent": ua,
        "accept-language": "en-US,en;q=0.9",
        "cookie": cookie_str,
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "upgrade-insecure-requests": "1",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
    }
    async with httpx.AsyncClient(
        follow_redirects=False, trust_env=False,
        timeout=httpx.Timeout(15.0, connect=5.0),
    ) as hx:
        shell_resp = await hx.get(f"{_ORIGIN}/", headers=disk_headers)

    step("SHELL", f"status={shell_resp.status_code}")
    if shell_resp.status_code != 200:
        step("ABORT", f"shell GET failed: HTTP {shell_resp.status_code}")
        _log("verdict", {"verdict": "SHELL_FAILED", "status": shell_resp.status_code})
        return

    at_token = _wiz(shell_resp.text, "SNlM0e") or ""
    fsid = _wiz(shell_resp.text, "FdrFJe") or ""
    bl = _wiz(shell_resp.text, "cfb2h") or ""
    if not at_token:
        step("ABORT", "no at token in shell response — profile may be signed out")
        _log("verdict", {"verdict": "SIGNED_OUT"})
        return
    step("SHELL", f"at={_redact(at_token)}, fsid={_redact(fsid)}, bl={_redact(bl)}")

    # ================================================================
    # PHASE 3: Open browser ONLY for reCAPTCHA minting
    # ================================================================
    step("MINT", "opening browser for reCAPTCHA only...")
    async with build_client(profile_dir, headless=False) as client:
        page = client._page  # noqa: SLF001
        if not page:
            step("ABORT", "no page")
            return

        # Discover project from the page
        await page.goto(f"{_ORIGIN}/", wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(2)
        href = await page.locator('a[href*="/project/"]').first.get_attribute("href", timeout=10_000)
        project_id = ""
        if href and "/project/" in href:
            project_id = href.split("/project/", 1)[1].split("/")[0].split("?")[0]
        if not project_id:
            step("ABORT", "no project found")
            return
        step("MINT", f"project: {project_id}")

        # Navigate to project (reCAPTCHA needs a Flow page)
        await page.goto(f"{_ORIGIN}/project/{project_id}", wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(2)

        site_key = await discover_site_key(page)
        minter = TokenMinter(page)
        token = await minter.mint(_RECAPTCHA_ACTION)
        step("MINT", f"token minted: {len(token)} chars")

    # Browser is now CLOSED — everything below uses only disk cookies

    # ================================================================
    # PHASE 4: Load captured frame template and swap prompt + token
    # ================================================================
    capture_files = sorted(_glob.glob(str(
        Path(__file__).parent / "_spike_out" / "capture_ogiz0b_structure_*.json"
    )))
    if not capture_files:
        step("ABORT", "no captured frame template found — run spike_capture_ogiz0b_structure.py first")
        return

    step("TEMPLATE", f"loading: {Path(capture_files[-1]).name}")
    with open(capture_files[-1]) as cf:
        captured = json.load(cf)
    payload = captured["inner"]

    # Swap prompt
    if isinstance(payload[1], list) and payload[1] and isinstance(payload[1][0], list):
        payload[1][0][8] = [[[_PROMPT]]]
    # Swap token in both project contexts
    if isinstance(payload[3], list) and len(payload[3]) > 10 and isinstance(payload[3][10], list):
        payload[3][10][0] = token
    if isinstance(payload[1], list) and payload[1] and isinstance(payload[1][0], list):
        entry = payload[1][0]
        if isinstance(entry[7], list) and len(entry[7]) > 10 and isinstance(entry[7][10], list):
            entry[7][10][0] = token
    # Also update the project_id in the template to match (in case it differs)
    if isinstance(payload[3], list) and len(payload[3]) > 5:
        payload[3][5] = project_id
    if isinstance(payload[1], list) and payload[1] and isinstance(payload[1][0], list):
        entry = payload[1][0]
        if isinstance(entry[7], list) and len(entry[7]) > 5:
            entry[7][5] = project_id

    step("TEMPLATE", "prompt, token, and project_id swapped")

    # ================================================================
    # PHASE 5: Build and send the POST using ONLY disk cookies
    # ================================================================
    inner_json = json.dumps(payload, separators=(",", ":"))
    body = urlencode({
        "f.req": json.dumps([[[_RPCID, inner_json, None, "generic"]]]),
        "at": at_token,
    })

    reqid = 1000 + secrets.randbelow(9000)
    query = {
        "rpcids": _RPCID,
        "source-path": f"/project/{project_id}",
        "bl": bl,
        "f.sid": fsid,
        "hl": "en",
        "_reqid": str(reqid),
        "rt": "c",
    }
    url = f"{_BATCHEXECUTE}?{urlencode({k: v for k, v in query.items() if v})}"

    post_headers = {
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
        "x-same-domain": "1",
        "origin": _ORIGIN,
        "referer": f"{_ORIGIN}/project/{project_id}",
        "user-agent": ua,
        "cookie": cookie_str,  # ON-DISK COOKIES ONLY
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "accept-language": "en-US,en;q=0.9",
    }

    step("SEND", f"posting with ON-DISK cookies ({len(disk_cookies)} pairs), body={len(body)} bytes")
    _log("send", {"cookie_count": len(disk_cookies), "body_len": len(body), "cookie_source": "disk"})

    t0 = time.perf_counter()
    async with httpx.AsyncClient(
        follow_redirects=False, trust_env=False,
        timeout=httpx.Timeout(120.0, connect=10.0),
    ) as hx:
        response = await hx.post(url, content=body, headers=post_headers)
    elapsed = time.perf_counter() - t0

    step("RECV", f"status={response.status_code}, elapsed={elapsed:.2f}s, body={len(response.text)} bytes")
    _log("response", {"status": response.status_code, "elapsed_s": round(elapsed, 2), "body_len": len(response.text)})

    # ================================================================
    # PHASE 6: Parse the response
    # ================================================================
    if response.status_code != 200:
        step("FAIL", f"HTTP {response.status_code}")
        _log("verdict", {"verdict": "HTTP_ERROR", "status": response.status_code})
    else:
        frames = list(parse_frames(response.text))
        ogiz0b_frames = [(rpcid, p) for rpcid, p in frames if rpcid == _RPCID]
        errors = rpc_errors(response.text)

        step("PARSE", f"frames={len(frames)}, ogiZ0b={len(ogiz0b_frames)}, errors={len(errors)}")
        if errors:
            _log("rpc_errors", {"errors": [{"code": e.code, "reasons": list(e.reasons)} for e in errors]})

        records = []
        for _, payload_data in ogiz0b_frames:
            try:
                records.extend(image_records(_RPCID, payload_data))
            except Exception as exc:  # noqa: BLE001
                step("PARSE", f"decode error: {exc}")

        step("RESULT", f"image records: {len(records)}")
        for i, rec in enumerate(records):
            w, h = rec.dimensions
            step("IMAGE", f"[{i}] media={rec.media_id}, seed={rec.seed}, dims={w}x{h}")
            step("IMAGE", f"[{i}] prompt_match={_PROMPT in (rec.prompt or '')}")
            _log("image", {"index": i, "media_id": rec.media_id, "dims": [w, h], "prompt_match": _PROMPT in (rec.prompt or "")})

            if rec.image_url:
                async with httpx.AsyncClient(timeout=30.0) as dl:
                    img = await dl.get(rec.image_url)
                step("DOWNLOAD", f"[{i}] status={img.status_code}, size={len(img.content)} bytes")
                _log("download", {"index": i, "status": img.status_code, "size": len(img.content), "magic": img.content[:4].hex()})

        if records and not errors:
            step("DONE", "ON-DISK COOKIES WORK for direct image generation!")
            _log("verdict", {"verdict": "VIABLE", "detail": "on-disk cookies accepted for ogiZ0b", "image_count": len(records)})
        elif records:
            step("DONE", "PARTIAL — images but with errors")
            _log("verdict", {"verdict": "PARTIAL"})
        elif errors:
            step("DONE", f"REFUSED — errors: {[e.code for e in errors]}")
            _log("verdict", {"verdict": "REFUSED", "errors": [e.code for e in errors]})
        else:
            step("DONE", "EMPTY — 200 OK but no records")
            _log("verdict", {"verdict": "EMPTY_200"})

    with open(out_path, "w", encoding="utf-8") as f:
        for r in _findings:
            f.write(json.dumps(r, default=str) + "\n")
    step("SAVED", str(out_path))


if __name__ == "__main__":
    asyncio.run(main())
