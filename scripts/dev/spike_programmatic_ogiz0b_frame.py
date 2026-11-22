r"""Spike: does a PROGRAMMATICALLY BUILT ogiZ0b frame work for direct image generation?

The earlier spike (spike_direct_image_with_browser_token.py) proved that a browser-minted
reCAPTCHA token works in a direct httpx batchexecute POST — but it REPLAYED the exact
positional array the page built. This spike constructs the array from scratch, with a
DIFFERENT prompt, to prove we can build the frame programmatically without capturing one.

Format reference (from tests/api/transports/_ogiz0b.py):

    entry = [
        None,                                    # 0: unknown
        None,                                    # 1: unknown
        media_refs or None,                      # 2: I2I refs (None for T2I)
        <int>,                                   # 3: session counter? (12345 in test)
        3,                                       # 4: image count / mode? (3 in test)
        model_name,                              # 5: "BELUGA" etc
        None,                                    # 6: unknown
        [None, 22, None, None, None, project],   # 7: project context
        [[mentions], [" prompt"]],               # 8: prompt document
        None,                                    # 9: unknown
        entity_refs or None,                     # 10: character refs
        None,                                    # 11: unknown
    ]
    inner = [None, [entry], 1, [None, 22, None, None, None, project]]

The f.req body is: [[[rpcid, json.dumps(inner), None, "generic"]]]

Pre-registered readings:
    - HTTP 200 + image records → programmatic frame is accepted; feature is buildable
    - HTTP 200 but empty → format is wrong; slot values need correction
    - HTTP 400/403 → missing or wrong field; compare with captured frame
    - 200 with reCAPTCHA error → token format or action mismatch

Cost: 1 image generation (image quota, zero credits).
"""

from __future__ import annotations

import argparse
import asyncio
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
    ImageGenerationRecord,
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
_PROMPT = "a small red fox sitting on a mossy rock in morning fog, watercolor style"
_MODEL = "BELUGA"  # Nano Banana 2.1 on flow.google.com
_RECAPTCHA_ACTION = "IMAGE_GENERATION"


def _build_ogiz0b_payload(
    prompt: str,
    model: str,
    project_id: str,
    recaptcha_token: str,
    *,
    session_counter: int = 1998179912,
    image_count: int = 2,
) -> list[Any]:
    """Build the inner batchexecute payload for ogiZ0b (image generation).

    Format reverse-engineered from a $0 route-abort capture (2026-11-22). The reCAPTCHA
    token sits at project_ctx[10] as [token, 1], duplicated in both inner[3] and entry[7].
    """
    prompt_doc = [[[prompt]]]  # triple-nested: [[["prompt text"]]]
    batch_uuid = str(uuid.uuid4()).upper()
    project_ctx: list[object] = [
        None, 22, None, None, None, project_id,
        None, None, None, None,
        [recaptcha_token, 1],
    ]
    entry: list[object] = [
        None,  # 0
        None,  # 1
        None,  # 2: media refs (None = T2I)
        session_counter,  # 3: session counter
        image_count,  # 4: image count
        model,  # 5: model name
        None,  # 6
        list(project_ctx),  # 7: project ctx WITH token (copy)
        prompt_doc,  # 8: prompt
        None,  # 9
        None,  # 10: entity refs
        None,  # 11
    ]
    return [None, [entry], 1, project_ctx, [batch_uuid]]


def _build_batchexecute_body(rpcid: str, payload: list[Any], at_token: str) -> str:
    """Encode the full f.req + at body for a batchexecute POST."""
    inner_json = json.dumps(payload, separators=(",", ":"))
    return urlencode({
        "f.req": json.dumps([[[rpcid, inner_json, None, "generic"]]]),
        "at": at_token,
    })


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="denon82")
    parser.add_argument("--project", default=None, help="existing project UUID (auto-discovered if omitted)")
    args = parser.parse_args()

    profile_dir = resolve_profile_dir(args.profile)
    out_path = default_out_path("spike_programmatic_ogiz0b", ".jsonl")
    step("START", f"profile={args.profile}, project={args.project}")
    step("OUTPUT", str(out_path))

    async with build_client(profile_dir, headless=False) as client:
        page = client._page  # noqa: SLF001 — spike-only access
        if page is None:
            step("ABORT", "no page available")
            return

        project_id = args.project

        if not project_id:
            # Navigate to Flow home and discover a project from the page
            step("NAV", "navigating to Flow home to discover a project...")
            await page.goto(f"{_ORIGIN}/", wait_until="networkidle", timeout=30_000)
            await asyncio.sleep(3)
            # Click the first project tile to get into a project
            project_link = page.locator('a[href*="/project/"]').first
            href = await project_link.get_attribute("href", timeout=10_000)
            if href and "/project/" in href:
                project_id = href.split("/project/", 1)[1].split("/")[0].split("?")[0]
                step("NAV", f"discovered project: {project_id}")
            else:
                step("ABORT", "could not discover a project from Flow home page")
                return

        # Navigate to the project page (needed for reCAPTCHA minting)
        project_url = f"{_ORIGIN}/project/{project_id}"
        step("NAV", f"navigating to {project_url}")
        await page.goto(project_url, wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(2)  # let reCAPTCHA Enterprise load

        # Discover site key and mint a fresh token
        step("TOKEN", "discovering site key...")
        site_key = await discover_site_key(page)
        step("TOKEN", f"site key: {_redact(site_key)}")

        minter = TokenMinter(page)
        step("TOKEN", f"minting reCAPTCHA token with action={_RECAPTCHA_ACTION}...")
        token = await minter.mint(_RECAPTCHA_ACTION)
        step("TOKEN", f"token minted: {len(token)} chars")

        # Extract the at token and cookies from the page
        step("AUTH", "extracting WIZ tokens from page...")
        wiz = await page.evaluate("""
            () => ({
                at: window.WIZ_global_data?.SNlM0e || '',
                fsid: window.WIZ_global_data?.FdrFJe || '',
                bl: window.WIZ_global_data?.cfb2h || '',
            })
        """)
        at_token = wiz.get("at", "")
        fsid = wiz.get("fsid", "")
        bl = wiz.get("bl", "")
        if not at_token:
            step("ABORT", "no at token found — page may not be authenticated")
            return
        step("AUTH", f"at={_redact(at_token)}, fsid={_redact(fsid or '')}, bl={_redact(bl or '')}")

        # Get the browser's cookies and headers for the httpx call
        cookies = await page.context.cookies([_ORIGIN])
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        step("AUTH", f"cookies: {len(cookies)} entries")

        # Build UA from the browser's own navigator
        ua = await page.evaluate("() => navigator.userAgent")
        sec_ch_ua = await page.evaluate("() => navigator.userAgentData?.toJSON()")

        # ================================================================
        # BUILD THE FRAME PROGRAMMATICALLY
        # ================================================================
        # Try to load a captured frame template from the capture spike
        import glob as _glob

        capture_files = sorted(_glob.glob(str(
            Path(__file__).parent / "_spike_out" / "capture_ogiz0b_structure_*.json"
        )))
        if capture_files:
            step("TEMPLATE", f"using captured frame template: {Path(capture_files[-1]).name}")
            with open(capture_files[-1]) as cf:
                captured_data = json.load(cf)
            payload = captured_data["inner"]
            # Replace the prompt in the captured frame
            if isinstance(payload[1], list) and payload[1] and isinstance(payload[1][0], list):
                entry = payload[1][0]
                entry[8] = [[[_PROMPT]]]
                step("TEMPLATE", f"replaced prompt at entry[8]")
            # Replace the token in both project contexts
            old_token = None
            if isinstance(payload[3], list) and len(payload[3]) > 10 and isinstance(payload[3][10], list):
                old_token = payload[3][10][0]
                payload[3][10][0] = token
                step("TEMPLATE", f"replaced token in inner[3][10] ({len(old_token)} -> {len(token)} chars)")
            if isinstance(payload[1], list) and payload[1] and isinstance(payload[1][0], list):
                entry = payload[1][0]
                if isinstance(entry[7], list) and len(entry[7]) > 10 and isinstance(entry[7][10], list):
                    entry[7][10][0] = token
                    step("TEMPLATE", "replaced token in entry[7][10]")
        else:
            step("BUILD", f"no captured frame found — building from scratch")
            payload = _build_ogiz0b_payload(_PROMPT, _MODEL, project_id, token)
        step("BUILD", f"prompt={_PROMPT!r}, model={_MODEL}")
        _log("payload_built", {
            "prompt": _PROMPT,
            "model": _MODEL,
            "project_id": project_id,
            "payload_json_len": len(json.dumps(payload)),
        })

        # Build query params (matching what the browser sends)
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

        body = _build_batchexecute_body(_RPCID, payload, at_token)
        step("BUILD", f"body length: {len(body)} bytes")

        # Build headers
        headers = {
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "x-same-domain": "1",
            "origin": _ORIGIN,
            "referer": f"{_ORIGIN}/project/{project_id}",
            "user-agent": ua,
            "cookie": cookie_str,
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        }
        if sec_ch_ua:
            brands = sec_ch_ua.get("brands", [])
            if brands:
                headers["sec-ch-ua"] = ", ".join(
                    f'"{b["brand"]}";v="{b["version"]}"' for b in brands
                )
            if sec_ch_ua.get("mobile") is not None:
                headers["sec-ch-ua-mobile"] = "?1" if sec_ch_ua["mobile"] else "?0"
            if sec_ch_ua.get("platform"):
                headers["sec-ch-ua-platform"] = f'"{sec_ch_ua["platform"]}"'

        # ================================================================
        # SEND THE DIRECT POST
        # ================================================================
        step("SEND", "sending direct httpx POST with programmatic frame...")
        t0 = time.perf_counter()
        async with httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(120.0, connect=10.0),
        ) as hx:
            response = await hx.post(url, content=body, headers=headers)
        elapsed = time.perf_counter() - t0

        step("RECV", f"status={response.status_code}, elapsed={elapsed:.2f}s, body={len(response.text)} bytes")
        _log("response", {
            "status": response.status_code,
            "elapsed_s": round(elapsed, 2),
            "body_len": len(response.text),
        })

        # ================================================================
        # PARSE THE RESPONSE
        # ================================================================
        if response.status_code != 200:
            step("FAIL", f"HTTP {response.status_code}")
            _log("verdict", {"verdict": "HTTP_ERROR", "status": response.status_code})
            # Save first 2000 chars for diagnosis
            _log("response_head", {"text": response.text[:2000]})
        else:
            # Parse batchexecute frames
            frames = list(parse_frames(response.text))
            step("PARSE", f"frames: {len(frames)}")

            ogiz0b_frames = [(rpcid, p) for rpcid, p in frames if rpcid == _RPCID]
            step("PARSE", f"ogiZ0b frames: {len(ogiz0b_frames)}")

            errors = rpc_errors(response.text)
            if errors:
                step("PARSE", f"RPC errors: {[{'code': e.code, 'reasons': e.reasons} for e in errors]}")
                _log("rpc_errors", {"errors": [{"code": e.code, "reasons": list(e.reasons)} for e in errors]})

            records: list[ImageGenerationRecord] = []
            for rpcid, payload_data in ogiz0b_frames:
                try:
                    recs = image_records(rpcid, payload_data)
                    records.extend(recs)
                except Exception as exc:  # noqa: BLE001
                    step("PARSE", f"image_records failed: {exc}")
                    _log("parse_error", {"error": str(exc)})

            step("RESULT", f"image records: {len(records)}")
            for i, rec in enumerate(records):
                w, h = rec.dimensions
                step("IMAGE", f"[{i}] media_id={rec.media_id}, seed={rec.seed}, dims={w}x{h}")
                step("IMAGE", f"[{i}] prompt={_redact(rec.prompt, keep=60)}")
                _log("image_record", {
                    "index": i,
                    "media_id": rec.media_id,
                    "workflow_id": rec.workflow_id,
                    "project_id": rec.project_id,
                    "seed": rec.seed,
                    "width": w,
                    "height": h,
                    "prompt_match": _PROMPT in (rec.prompt or ""),
                    "url_present": bool(rec.image_url),
                })

                if rec.image_url:
                    step("DOWNLOAD", f"[{i}] fetching image...")
                    async with httpx.AsyncClient(timeout=30.0) as dl:
                        img_resp = await dl.get(rec.image_url)
                    step("DOWNLOAD", f"[{i}] status={img_resp.status_code}, size={len(img_resp.content)} bytes")
                    magic = img_resp.content[:4].hex() if len(img_resp.content) >= 4 else "?"
                    _log("image_download", {
                        "index": i,
                        "status": img_resp.status_code,
                        "size_bytes": len(img_resp.content),
                        "magic_bytes": magic,
                    })

            # Verdict
            if records and not errors:
                step("DONE", "PROGRAMMATIC FRAME ACCEPTED — direct image generation with built frame works!")
                _log("verdict", {
                    "verdict": "VIABLE",
                    "detail": "programmatically built ogiZ0b frame accepted by Flow",
                    "image_count": len(records),
                })
            elif records and errors:
                step("DONE", "PARTIAL — images returned but with RPC errors")
                _log("verdict", {"verdict": "PARTIAL", "image_count": len(records), "error_count": len(errors)})
            elif not records and not errors:
                step("DONE", "EMPTY — 200 OK but no image records; frame format may be wrong")
                _log("verdict", {"verdict": "EMPTY_200", "frame_count": len(ogiz0b_frames)})
            else:
                step("DONE", "REFUSED — RPC errors, no images")
                _log("verdict", {"verdict": "REFUSED", "error_count": len(errors)})

    # Write findings
    with open(out_path, "w", encoding="utf-8") as f:
        for record in _findings:
            f.write(json.dumps(record, default=str) + "\n")
    step("SAVED", str(out_path))


if __name__ == "__main__":
    asyncio.run(main())
