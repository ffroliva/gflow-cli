r"""Binary-search which ogiZ0b entry slots are required for Flow to accept the frame.

The captured frame with 15-slot entries works (template approach). The from-scratch
12-slot build fails with gRPC code 3. This spike nullifies slots in the captured
template to find the minimum viable frame.

Arms (sequential, stop after first generation):
  A: entry[12-14] = null (all three extra slots nulled)
  B: entry[12-13] = random UUID, entry[14] = 4 (random UUIDs, constant kept)
  C: entry[12-13] = null, entry[14] = 4 (UUIDs nulled, constant kept)
  D: entry[3] = 1, entry[4] = 1 (session counter / image mode changed)

Each arm that generates costs 1 image ($0 credits). Rejections (gRPC 3) cost $0.
Budget: up to 4 images total.

Profile: denon82.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from gflow_cli.api.recaptcha import TokenMinter, discover_site_key  # noqa: E402
from gflow_cli.api.transports.batchexecute import (  # noqa: E402
    image_records,
    parse_frames,
    rpc_errors,
)

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

_ORIGIN = "https://flow.google.com"
_BATCHEXECUTE = f"{_ORIGIN}/_/AiSandboxAngularFrontend/data/batchexecute"
_RPCID = "ogiZ0b"
_PROMPT = "a blue parrot perched on a wire fence at sunset, oil painting style"
_RECAPTCHA_ACTION = "IMAGE_GENERATION"


def _redact(s: str, *, keep: int = 20) -> str:
    return s[:keep] + f"...({len(s)})" if len(s) > keep else s


def _build_body(rpcid: str, payload: list[Any], at_token: str) -> str:
    inner_json = json.dumps(payload, separators=(",", ":"))
    return urlencode({
        "f.req": json.dumps([[[rpcid, inner_json, None, "generic"]]]),
        "at": at_token,
    })


def _apply_arm(template: list[Any], arm: str) -> list[Any]:
    """Return a modified copy of the template for the given arm."""
    p = copy.deepcopy(template)
    entry = p[1][0]
    if arm == "A":
        # Null all three extra slots
        while len(entry) > 12:
            entry.pop()
    elif arm == "B":
        # Random UUIDs, keep constant 4
        entry[12] = str(uuid.uuid4()).upper()
        entry[13] = str(uuid.uuid4()).upper()
        entry[14] = 4
    elif arm == "C":
        # Null UUIDs, keep constant 4
        entry[12] = None
        entry[13] = None
        entry[14] = 4
    elif arm == "D":
        # Minimal session counter and image mode
        entry[3] = 1
        entry[4] = 1
    return p


async def _send_arm(
    arm: str,
    payload: list[Any],
    *,
    url: str,
    headers: dict[str, str],
    at_token: str,
) -> dict[str, Any]:
    """Send one arm and return a result dict."""
    body = _build_body(_RPCID, payload, at_token)
    step(f"ARM_{arm}", f"sending... body={len(body)} bytes")

    t0 = time.perf_counter()
    async with httpx.AsyncClient(
        follow_redirects=False, trust_env=False,
        timeout=httpx.Timeout(120.0, connect=10.0),
    ) as hx:
        resp = await hx.post(url, content=body, headers=headers)
    elapsed = time.perf_counter() - t0

    result: dict[str, Any] = {
        "arm": arm,
        "status": resp.status_code,
        "elapsed_s": round(elapsed, 2),
        "body_len": len(resp.text),
    }

    if resp.status_code != 200:
        result["verdict"] = "HTTP_ERROR"
        step(f"ARM_{arm}", f"HTTP {resp.status_code} in {elapsed:.1f}s")
        return result

    frames = [(rid, p) for rid, p in parse_frames(resp.text) if rid == _RPCID]
    errors = rpc_errors(resp.text)
    result["frame_count"] = len(frames)
    result["error_count"] = len(errors)
    if errors:
        result["errors"] = [{"code": e.code, "reasons": list(e.reasons)} for e in errors]

    records = []
    for _, pdata in frames:
        try:
            records.extend(image_records(_RPCID, pdata))
        except Exception:  # noqa: BLE001
            pass
    result["image_count"] = len(records)

    if records:
        rec = records[0]
        w, h = rec.dimensions
        result["verdict"] = "GENERATED"
        result["media_id"] = rec.media_id
        result["dims"] = f"{w}x{h}"
        result["prompt_match"] = _PROMPT in (rec.prompt or "")
        step(f"ARM_{arm}", f"GENERATED in {elapsed:.1f}s — {w}x{h}, prompt_match={result['prompt_match']}")
    elif errors:
        codes = [e.code for e in errors]
        reasons = [r for e in errors for r in e.reasons]
        result["verdict"] = "REJECTED"
        step(f"ARM_{arm}", f"REJECTED in {elapsed:.1f}s — codes={codes} reasons={reasons}")
    else:
        result["verdict"] = "EMPTY"
        step(f"ARM_{arm}", f"EMPTY 200 in {elapsed:.1f}s — no frames, no errors")

    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="denon82")
    parser.add_argument("--project", default=None)
    args = parser.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    out_path = default_out_path("spike_ogiz0b_minimum_frame", ".jsonl")
    step("START", f"profile={args.profile}")

    # Load the captured template
    import glob as _glob
    capture_files = sorted(_glob.glob(str(
        Path(__file__).parent / "_spike_out" / "capture_ogiz0b_structure_*.json"
    )))
    if not capture_files:
        step("ABORT", "no captured frame template found — run spike_capture_ogiz0b_structure.py first")
        return
    with open(capture_files[-1]) as cf:
        template = json.load(cf)["inner"]
    step("TEMPLATE", f"loaded {Path(capture_files[-1]).name}")

    async with build_client(profile_dir, headless=False) as client:
        page = client._page  # noqa: SLF001
        if not page:
            step("ABORT", "no page")
            return

        # Discover project
        project_id = args.project
        if not project_id:
            await page.goto(f"{_ORIGIN}/", wait_until="networkidle", timeout=30_000)
            await asyncio.sleep(2)
            href = await page.locator('a[href*="/project/"]').first.get_attribute("href", timeout=10_000)
            if href and "/project/" in href:
                project_id = href.split("/project/", 1)[1].split("/")[0].split("?")[0]
            else:
                step("ABORT", "no project")
                return
        step("PROJECT", project_id)

        # Navigate to project
        await page.goto(f"{_ORIGIN}/project/{project_id}", wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(2)

        # Auth setup
        site_key = await discover_site_key(page)
        minter = TokenMinter(page)
        wiz = await page.evaluate("""() => ({
            at: window.WIZ_global_data?.SNlM0e || '',
            fsid: window.WIZ_global_data?.FdrFJe || '',
            bl: window.WIZ_global_data?.cfb2h || '',
        })""")
        at_token = wiz["at"]
        fsid = wiz.get("fsid", "")
        bl = wiz.get("bl", "")
        if not at_token:
            step("ABORT", "no at token")
            return

        cookies = await page.context.cookies([_ORIGIN])
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        ua = await page.evaluate("() => navigator.userAgent")
        sec_ch_ua = await page.evaluate("() => navigator.userAgentData?.toJSON()")

        reqid = 1000 + secrets.randbelow(9000)
        query = {
            "rpcids": _RPCID, "source-path": f"/project/{project_id}",
            "bl": bl, "f.sid": fsid, "hl": "en", "_reqid": str(reqid), "rt": "c",
        }
        url = f"{_BATCHEXECUTE}?{urlencode({k: v for k, v in query.items() if v})}"

        headers: dict[str, str] = {
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "x-same-domain": "1", "origin": _ORIGIN,
            "referer": f"{_ORIGIN}/project/{project_id}",
            "user-agent": ua, "cookie": cookie_str,
            "sec-fetch-site": "same-origin", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty",
        }
        if sec_ch_ua:
            brands = sec_ch_ua.get("brands", [])
            if brands:
                headers["sec-ch-ua"] = ", ".join(f'"{b["brand"]}";v="{b["version"]}"' for b in brands)
            if sec_ch_ua.get("mobile") is not None:
                headers["sec-ch-ua-mobile"] = "?1" if sec_ch_ua["mobile"] else "?0"
            if sec_ch_ua.get("platform"):
                headers["sec-ch-ua-platform"] = f'"{sec_ch_ua["platform"]}"'

        # ================================================================
        # RUN ARMS SEQUENTIALLY
        # ================================================================
        results: list[dict[str, Any]] = []
        arms = ["A", "B", "C", "D"]

        for arm in arms:
            # Mint a fresh token for each arm (tokens are single-use)
            step(f"ARM_{arm}", "minting fresh reCAPTCHA token...")
            token = await minter.mint(_RECAPTCHA_ACTION)
            step(f"ARM_{arm}", f"token: {len(token)} chars")

            # Build the modified template
            payload = _apply_arm(template, arm)
            entry = payload[1][0]
            # Swap prompt
            entry[8] = [[[_PROMPT]]]
            # Swap token in both contexts
            if isinstance(payload[3], list) and len(payload[3]) > 10:
                payload[3][10] = [token, 1]
            if isinstance(entry[7], list) and len(entry[7]) > 10:
                entry[7][10] = [token, 1]

            desc = {
                "A": "entry truncated to 12 slots (no [12-14])",
                "B": "entry[12-13]=random UUID, entry[14]=4",
                "C": "entry[12-13]=null, entry[14]=4",
                "D": "entry[3]=1, entry[4]=1 (counter+mode changed)",
            }[arm]
            step(f"ARM_{arm}", desc)

            result = await _send_arm(arm, payload, url=url, headers=headers, at_token=at_token)
            results.append(result)

            if result.get("verdict") == "GENERATED":
                step(f"ARM_{arm}", "first generation found — stopping here")
                break

            # Small delay between arms
            await asyncio.sleep(1)

        # ================================================================
        # SUMMARY
        # ================================================================
        step("SUMMARY", "=" * 60)
        for r in results:
            step("SUMMARY", f"Arm {r['arm']}: {r['verdict']} ({r['elapsed_s']}s)")
        step("SUMMARY", "=" * 60)

    # Save findings
    with open(out_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, default=str) + "\n")
    step("SAVED", str(out_path))


if __name__ == "__main__":
    asyncio.run(main())
