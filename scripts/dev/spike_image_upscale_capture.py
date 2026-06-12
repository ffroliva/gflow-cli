#!/usr/bin/env python3
r"""Image-upscale spike — capture the 1K/2K/4K download-menu wire (0 credits).

Capture-harness + human-drive hybrid. Opens the Flow IMAGE editor headed at a
specific generated image and attaches a passive network capture. YOU click the
download button and pick the size options; the script records the wire so we can
answer the three open questions before any implementation:

  1. TRIGGER — does picking "2K Upscaled" fire a NEW endpoint (an upscale
     operation returning a fresh signed URL — possibly Bearer-only REST), or
     does the browser just fetch a different `=s<N>` variant of the SAME
     pre-signed fifeUrl (in which case there is no upscale "call" at all)?
  2. RESPONSE SHAPE — endpoint name, method, body params (size/resolution
     enum?), and whether the result is inline base64 or a signed URL.
  3. TIER GATING — where does the account tier (Pro vs Ultra) surface on the
     wire (likely GET /fx/api/auth/session or a subscription/entitlement
     endpoint), so a future build can gate 4K client-side with an honest
     "re-authenticate after upgrading" hint when the session is stale.

Image operations are FREE (no credits, no reCAPTCHA per the capability matrix),
so passive capture is sufficient. On a Pro account 4K shows "Upgrade" and is NOT
capturable — scope is 1K + 2K. Run on an Ultra account to capture 4K too.

The script never clicks anything that spends credits; it only records.

Usage (headed, supervised, background):

    ! set PYTHONUTF8=1 && .venv\Scripts\python.exe scripts\dev\spike_image_upscale_capture.py ^
        --profile ffroliva ^
        --project 5ee3e625-ff3f-44a1-9f17-1434f432f30e ^
        --media 860b0b2a-3684-402c-8c83-db8af252c9db ^
        --window 300

Or point it straight at a URL:

    ... spike_image_upscale_capture.py --profile ffroliva --url "https://labs.google/fx/tools/flow/project/<pid>/edit/<mid>"

Outputs (gitignored, flushed continuously):
  scripts/dev/_spike_out/spike_image_upscale_capture_<ts>.json
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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

# ---------------------------------------------------------------------------
# What we care about. We do NOT know the upscale endpoint name yet, so the net
# is deliberately broad: every POST, plus any GET whose URL hints at media
# download, image variants, the session, or account tier.
# ---------------------------------------------------------------------------
_INTERESTING_GET = (
    # upscale / super-resolution naming guesses
    "upscale",
    "upres",
    "superres",
    "super_res",
    "enhance",
    "highres",
    "high_res",
    # media download / signed-url variants
    "getmediaurlredirect",
    "media",
    "fife",
    "flow-content",
    "googleusercontent",
    # the size suffix Google FIFE URLs use (=s2048, =w2048-h..., =s4096)
    "=s",
    "=w",
    "imagesize",
    "downloadsize",
    "resolution",
    # tier / subscription / account surfaces
    "auth/session",
    "subscription",
    "entitlement",
    "entitlements",
    "tier",
    "account",
    "userinfo",
    "user-info",
    "quota",
    "plan",
    "billing",
)
# Endpoints whose JSON body we want to keep in full (after redaction).
_KEEP_BODY = (
    "upscale",
    "upres",
    "superres",
    "enhance",
    "auth/session",
    "subscription",
    "entitlement",
    "tier",
    "account",
    "userinfo",
    "user-info",
    "quota",
    "plan",
    "getmediaurlredirect",
    "batchgenerateimages",
    "flowmedia",
)
# Query-param names whose VALUES we mask (keep the key so we still see shape).
_SENSITIVE_PARAMS = {
    "signature",
    "x-goog-signature",
    "expires",
    "x-goog-credential",
    "keyname",
    "key",
    "token",
    "authuser",
    "sig",
}
# Content types we refuse to read bodies from (raw image/video bytes).
_BINARY_CT = ("image/", "video/", "audio/", "octet-stream", "font/")


def _redact_url(url: str) -> str:
    """Mask sensitive query-param VALUES but keep keys + non-sensitive params.

    Critically preserves size hints (``=s2048``, ``width``, ``imageSize``) that
    tell us how the 2K/4K variant is requested, while never logging the signed
    download signature.
    """
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        kept = [
            (k, "<MASKED>" if k.lower() in _SENSITIVE_PARAMS else v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
        return urlunsplit(parts._replace(query=urlencode(kept)))
    except Exception:  # noqa: BLE001
        return url.split("?")[0] + "?<unparseable-query-redacted>"


def _redact_body(obj: Any) -> Any:
    sensitive = ("authorization", "cookie", "signature", "token", "bearer")
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k.lower() in ("authorization", "cookie"):
                out[k] = "<REDACTED>"
            else:
                out[k] = _redact_body(v)
        return out
    if isinstance(obj, list):
        return [_redact_body(x) for x in obj]
    if isinstance(obj, str) and any(m in obj.lower() for m in sensitive) and len(obj) > 40:
        return "<REDACTED>"
    return obj


def _interesting(url: str, method: str) -> bool:
    low = url.lower()
    if method == "POST":
        return True
    return any(f in low for f in _INTERESTING_GET)


def _keep_body(url: str) -> bool:
    low = url.lower()
    return any(f in low for f in _KEEP_BODY)


def _is_binary(headers: dict[str, str]) -> bool:
    ct = (headers.get("content-type") or "").lower()
    return any(b in ct for b in _BINARY_CT)


def _edit_url(*, locale: str | None, project_id: str, media_id: str) -> str:
    seg = f"/{locale.strip().split('-', 1)[0].lower()}" if locale else ""
    return f"https://labs.google/fx{seg}/tools/flow/project/{project_id}/edit/{media_id}"


async def _run(
    *,
    profile_dir: Path,
    target_url: str,
    window: int,
    out_path: Path,
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    state = {"upscale_seen": 0}

    def flush() -> None:
        out_path.write_text(
            json.dumps(
                {
                    "spike": "image-upscale-capture",
                    "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "targetUrl": _redact_url(target_url),
                    "events": events,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001

        def on_request(req: Any) -> None:
            try:
                if not _interesting(req.url, req.method):
                    return
                raw = req.post_data if req.method == "POST" else None
                body: Any = None
                if raw:
                    try:
                        body = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        body = raw[:2000]
                ev = {
                    "dir": "request",
                    "t": round(time.monotonic(), 2),
                    "method": req.method,
                    "url": _redact_url(req.url),
                    "body": _redact_body(body) if isinstance(body, (dict, list)) else body,
                }
                events.append(ev)
                low = req.url.lower()
                if any(k in low for k in ("upscale", "upres", "superres", "enhance")):
                    state["upscale_seen"] += 1
                    step("WIRE", f"UPSCALE-candidate request: {ev['url'][:120]}", prefix="ups")
                else:
                    step("wire", f"{req.method} {ev['url'].split('?')[0][-80:]}", prefix="ups")
                flush()
            except Exception as e:  # noqa: BLE001
                step("on_request-err", str(e), prefix="ups")

        async def on_response(resp: Any) -> None:
            try:
                req = resp.request
                if not _interesting(resp.url, req.method):
                    return
                headers = await resp.all_headers()
                ev: dict[str, Any] = {
                    "dir": "response",
                    "t": round(time.monotonic(), 2),
                    "status": resp.status,
                    "method": req.method,
                    "url": _redact_url(resp.url),
                    "contentType": (headers.get("content-type") or "")[:60],
                    "contentLength": headers.get("content-length"),
                }
                if _keep_body(resp.url) and not _is_binary(headers):
                    try:
                        rbody = await resp.json()
                        ev["body"] = _redact_body(rbody)
                    except Exception:  # noqa: BLE001
                        try:
                            ev["bodyText"] = (await resp.text())[:4000]
                        except Exception:  # noqa: BLE001
                            ev["body"] = "<unreadable>"
                events.append(ev)
                step(
                    "resp",
                    f"{resp.status} {ev['url'].split('?')[0][-80:]} ct={ev['contentType']}",
                    prefix="ups",
                )
                flush()
            except Exception as e:  # noqa: BLE001
                step("on_response-err", str(e), prefix="ups")

        page.on("request", on_request)
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        step("1", f"goto {target_url}", prefix="ups")
        await page.goto(target_url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(3000)

        print("\n" + "=" * 72, flush=True)
        print("  IMAGE-UPSCALE CAPTURE READY — do the following IN THE BROWSER now:", flush=True)
        print("  1. Click the DOWNLOAD button (top-right, the down-arrow icon).", flush=True)
        print("  2. The menu shows: 1K Original / 2K Upscaled / 4K Upscaled.", flush=True)
        print("  3. Click '2K Upscaled'. Wait for the download/processing to finish.", flush=True)
        print("  4. Re-open the menu and click '1K Original size' for the baseline.", flush=True)
        print("  5. (Ultra account only) click '4K Upscaled' too. On Pro it shows", flush=True)
        print("     'Upgrade' and is not clickable — skip it; that's expected.", flush=True)
        print("  6. If a separate page/tab opens the high-res image, let it load.", flush=True)
        print(f"  Capturing for up to {window}s. Press Ctrl-C to stop early.", flush=True)
        print("=" * 72 + "\n", flush=True)

        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            flush()
        step("done", f"capture window ({window}s) elapsed — exiting", prefix="ups")
        flush()
        client._checkin_page(page)  # noqa: SLF001

    ups = state["upscale_seen"]
    print(f"\n[ups] events captured: {len(events)} (upscale-candidate requests: {ups})", flush=True)
    print(f"[ups] out -> {out_path}", flush=True)
    if ups == 0:
        print(
            "[ups] NOTE: no obviously-named upscale request fired. The 2K/4K "
            "variant may be a different =s<N> suffix on the SAME fifeUrl — "
            "inspect the download GET URLs in the capture for size params.",
            flush=True,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Flow image upscale (1K/2K/4K) download-menu wire capture — 0 credits."
    )
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "ffroliva"))
    p.add_argument("--project", default=None, help="Project UUID (with --media).")
    p.add_argument("--media", default=None, help="Generated-image media UUID (with --project).")
    p.add_argument(
        "--locale",
        default=None,
        help="Optional locale segment (e.g. 'en', 'pt'). Omit for the default /fx path.",
    )
    p.add_argument(
        "--url",
        default=None,
        help="Full editor URL override (takes precedence over --project/--media).",
    )
    p.add_argument(
        "--window", type=int, default=300, help="Capture window in seconds (default 300)."
    )
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    if args.url:
        target_url = args.url
    elif args.project and args.media:
        target_url = _edit_url(locale=args.locale, project_id=args.project, media_id=args.media)
    else:
        print(
            "[ups] ERROR: provide either --url, or both --project and --media.",
            file=sys.stderr,
            flush=True,
        )
        return 2

    profile_dir = resolve_profile_dir(args.profile)
    out_path = (
        Path(args.out) if args.out else default_out_path("spike_image_upscale_capture", ".json")
    )
    step("--", f"profile={args.profile} url={_redact_url(target_url)}", prefix="ups")
    try:
        return asyncio.run(
            _run(
                profile_dir=profile_dir,
                target_url=target_url,
                window=args.window,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[ups] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
