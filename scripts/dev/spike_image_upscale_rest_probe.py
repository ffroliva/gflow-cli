#!/usr/bin/env python3
r"""Image-upscale spike — RESPONSE SCHEMA + Bearer-only REST probe (0 credits).

Two extract-tests in one driven run, then prints a verdict:

  TEST B (schema): drive the UI (gallery -> open image -> download -> 2K) and
    capture the REAL upsampleImage REQUEST (mediaId) + RESPONSE schema (top-level
    keys, value types, base64 field length) WITHOUT dumping the ~3.8MB blob.

  TEST A (REST viability): using the same mediaId and a Bearer ya29 token from
    /fx/api/auth/session, fire upsampleImage directly via context.request with
    NO reCAPTCHA token. Two variants:
      A1: body {mediaId, targetResolution}                  (no clientContext)
      A2: body {mediaId, targetResolution, clientContext:{}} (no recaptcha token)
    A non-200 (401/403/400) => reCAPTCHA is mandatory => pure REST is DEAD.
    A 200 => a Bearer-only REST path exists (unexpected, but would be gold).

Image ops are FREE (no credits). 4K is Ultra-gated; we use 2K.

Usage (headed, supervised):

    ! set PYTHONUTF8=1 && .venv\Scripts\python.exe scripts\dev\spike_image_upscale_rest_probe.py ^
        --profile ffroliva ^
        --project ffb768fb-cf2d-48b7-a135-92978667c37d ^
        --media 9d5e015c-0162-4b63-9ec4-bd6bf7c68200 --locale pt

Outputs (gitignored): scripts/dev/_spike_out/spike_image_upscale_rest_probe_<ts>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

UPSAMPLE_URL = "https://aisandbox-pa.googleapis.com/v1/flow/upsampleImage"
SESSION_URL = "https://labs.google/fx/api/auth/session"
_UPSAMPLE_MARK = "upsampleimage"


def _gallery_url(locale: str | None, project_id: str) -> str:
    seg = f"/{locale.strip().split('-', 1)[0].lower()}" if locale else ""
    return f"https://labs.google/fx{seg}/tools/flow/project/{project_id}"


def _schema(obj: Any, depth: int = 0) -> Any:
    """Summarise a JSON value: keys + types + string lengths, never raw bytes."""
    if isinstance(obj, dict):
        return {k: _schema(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        head = _schema(obj[0], depth + 1) if obj else None
        return [f"list[{len(obj)}]", head]
    if isinstance(obj, str):
        return f"str(len={len(obj)})" if len(obj) > 60 else f"str:{obj!r}"
    return type(obj).__name__


async def _run(
    *,
    profile_dir: Path,
    project_id: str,
    media_id: str | None,
    locale: str | None,
    out_path: Path,
) -> int:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "spike": "image-upscale-rest-probe",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "projectId": project_id,
        "uiUpsample": None,  # TEST B
        "restProbes": [],  # TEST A
        "verdict": None,
    }
    captured: dict[str, Any] = {
        "media_content_id": None,
        "response_schema": None,
        "request_body_keys": None,
    }

    def flush() -> None:
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    async with build_client(profile_dir, headless=False) as client:
        ctx = client._context  # noqa: SLF001
        page = await client._checkout_page()  # noqa: SLF001

        async def on_response(resp: Any) -> None:
            try:
                if _UPSAMPLE_MARK not in resp.url.lower():
                    return
                req = resp.request
                try:
                    reqbody = json.loads(req.post_data or "{}")
                except Exception:  # noqa: BLE001
                    reqbody = {}
                captured["media_content_id"] = reqbody.get("mediaId")
                captured["request_body_keys"] = sorted(reqbody.keys())
                try:
                    rbody = await resp.json()
                    captured["response_schema"] = _schema(rbody)
                except Exception as e:  # noqa: BLE001
                    captured["response_schema"] = f"<unreadable: {e}>"
                result["uiUpsample"] = {
                    "status": resp.status,
                    "requestBodyKeys": captured["request_body_keys"],
                    "mediaId": captured["media_content_id"],
                    "targetResolution": reqbody.get("targetResolution"),
                    "hasRecaptchaInRequest": "recaptchaContext"
                    in json.dumps(reqbody.get("clientContext", {})),
                    "responseSchema": captured["response_schema"],
                }
                step(
                    "WIRE",
                    f"UI upsample {resp.status}; mediaId={captured['media_content_id']}",
                    prefix="probe",
                )
                flush()
            except Exception as e:  # noqa: BLE001
                step("on_response-err", str(e), prefix="probe")

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        # ---- TEST B: drive UI to a real 2K upscale (proven selectors) ----
        gurl = _gallery_url(locale, project_id)
        step("B1", f"goto gallery {gurl}", prefix="probe")
        await page.goto(gurl, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(4500)
        tile = f"a[href*='{media_id}']" if media_id else "a[href*='/edit/']"
        try:
            await page.locator(tile).first.click(timeout=10_000)
            await page.wait_for_function("() => location.href.includes('/edit/')", timeout=15_000)
            step("B2", f"editor open {page.url[-50:]}", prefix="probe")
        except Exception as e:  # noqa: BLE001
            step("B2-warn", str(e), prefix="probe")
        await page.wait_for_timeout(3000)
        try:
            await page.locator("button:has(i.google-symbols:text-is('download'))").first.click(
                timeout=8000
            )
            await page.wait_for_timeout(1200)
            await page.locator("[role='menuitem']:has-text('2K')").first.click(timeout=8000)
            step("B3", "clicked download -> 2K; awaiting upsample response", prefix="probe")
        except Exception as e:  # noqa: BLE001
            step("B3-warn", str(e), prefix="probe")
        # Wait for the UI upsample response (up to 45s).
        for _ in range(15):
            if result["uiUpsample"] is not None:
                break
            await asyncio.sleep(3)
        flush()

        # ---- Bearer token for the REST probe ----
        token = None
        try:
            sresp = await ctx.request.get(SESSION_URL, timeout=15_000)
            session = await sresp.json()
            token = session.get("access_token")
            step("A0", f"bearer fetched: {'yes' if token else 'NO'}", prefix="probe")
        except Exception as e:  # noqa: BLE001
            step("A0-warn", str(e), prefix="probe")

        mid = captured["media_content_id"] or media_id
        # ---- TEST A: Bearer-only REST, no reCAPTCHA token (two variants) ----
        variants = [
            (
                "A1_no_clientContext",
                {"mediaId": mid, "targetResolution": "UPSAMPLE_IMAGE_RESOLUTION_2K"},
            ),
            (
                "A2_clientContext_no_recaptcha",
                {
                    "mediaId": mid,
                    "targetResolution": "UPSAMPLE_IMAGE_RESOLUTION_2K",
                    "clientContext": {},
                },
            ),
        ]
        for label, body in variants:
            probe: dict[str, Any] = {"variant": label, "sentRecaptcha": False, "mediaId": mid}
            if not token or not mid:
                probe["skipped"] = f"missing {'token' if not token else 'mediaId'}"
                result["restProbes"].append(probe)
                continue
            try:
                r = await ctx.request.post(
                    UPSAMPLE_URL,
                    headers={
                        "authorization": f"Bearer {token}",
                        "content-type": "application/json",
                    },
                    data=json.dumps(body),
                    timeout=30_000,
                )
                txt = (await r.text())[:500]
                probe["status"] = r.status
                probe["bodyPreview"] = txt
                step("A", f"{label} -> HTTP {r.status}", prefix="probe")
            except Exception as e:  # noqa: BLE001
                probe["error"] = str(e)
                step("A-err", f"{label}: {e}", prefix="probe")
            result["restProbes"].append(probe)
            flush()

        # ---- Verdict ----
        rest_statuses = [p.get("status") for p in result["restProbes"] if "status" in p]
        rest_ok = any(s == 200 for s in rest_statuses)
        ui_ok = result["uiUpsample"] and result["uiUpsample"]["status"] == 200
        result["verdict"] = {
            "uiUpscaleWorks": bool(ui_ok),
            "bearerOnlyRestWorks": rest_ok,
            "restStatuses": rest_statuses,
            "conclusion": (
                "REST path exists (Bearer-only 200)"
                if rest_ok
                else "reCAPTCHA mandatory — browser transport required (REST blocked)"
            ),
        }
        flush()

        client._checkin_page(page)  # noqa: SLF001

    print("\n[probe] ===== VERDICT =====", flush=True)
    print(json.dumps(result["verdict"], indent=2), flush=True)
    if result["uiUpsample"]:
        print("\n[probe] UI upsample response schema:", flush=True)
        print(json.dumps(result["uiUpsample"]["responseSchema"], indent=2)[:1200], flush=True)
    print(f"\n[probe] out -> {out_path}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="upsampleImage response-schema + Bearer-only REST probe (0 credits)."
    )
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "ffroliva"))
    p.add_argument("--project", required=True)
    p.add_argument("--media", default=None)
    p.add_argument("--locale", default="pt")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)
    profile_dir = resolve_profile_dir(args.profile)
    out_path = (
        Path(args.out) if args.out else default_out_path("spike_image_upscale_rest_probe", ".json")
    )
    step("--", f"profile={args.profile} project={args.project}", prefix="probe")
    try:
        return asyncio.run(
            _run(
                profile_dir=profile_dir,
                project_id=args.project,
                media_id=args.media,
                locale=args.locale,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[probe] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
