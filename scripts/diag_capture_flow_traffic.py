"""Diagnostic — capture Flow's outgoing requests via page.route.

Spec § 11 mentioned a captured wire sample (samples/captured/06_batchGenerateImages.json)
but the file doesn't exist. This script produces that ground truth — what
headers does Flow's UI actually attach when it calls aisandbox-pa?

Run: `uv run python scripts/diag_capture_flow_traffic.py --profile <your-profile>`

Output goes to `tmp/captured/flow_outgoing_<utc>.jsonl` by default. **NEVER write
captured traffic to `samples/captured/`** — those files contain live Bearer tokens
and API keys; `tmp/` is gitignored and `samples/captured/flow_outgoing_*` is
also explicitly blocked in `.gitignore`. Only redacted reference samples should
live under `samples/captured/`.

Opens HEADED Chromium against the named profile (sign-in must be valid), navigates
to Flow editor, installs a page.route interceptor on every
`aisandbox-pa.googleapis.com` URL, logs every captured request to
the output path, and waits ~30s for traffic.

Each line of the JSONL is a captured event:
    {"ts": ..., "method": "POST", "url": "...", "headers": {...},
     "post_data_preview": "...first 200 chars..."}

Use this to answer:
  - Which auth header(s) does Flow attach? (Bearer / SAPISIDHASH / cookie / multiple?)
  - What's the action name for batchGenerateImages reCAPTCHA?
  - What's the exact wire format of the request body?
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import structlog
from playwright.async_api import Request, async_playwright

from gflow_cli.auth import profile_dir

log = structlog.get_logger(__name__)


async def run(profile_name: str, wait_seconds: int, out_path: Path) -> None:
    """Drive the capture loop."""
    pdir = profile_dir(profile_name)
    if not pdir.exists():
        raise SystemExit(f"Profile directory not found: {pdir}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    captured: list[dict[str, object]] = []

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(pdir),
            headless=False,  # MUST be headed so reCAPTCHA Enterprise + Flow JS load fully
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        async def on_request(req: Request) -> None:
            url = req.url
            if "aisandbox-pa.googleapis.com" not in url:
                return
            post_data = ""
            try:
                if req.method in ("POST", "PUT", "PATCH"):
                    raw = req.post_data
                    if raw:
                        post_data = raw[:200]
            except Exception:  # noqa: BLE001
                pass
            event = {
                "ts": time.time(),
                "method": req.method,
                "url": url,
                "headers": dict(await req.all_headers()),
                "post_data_preview": post_data,
            }
            captured.append(event)
            print(f"  [{req.method}] {url}")
            print(f"    auth: {event['headers'].get('authorization', '(none)')[:80]}")
            print(
                f"    x-goog-recaptcha-action: "
                f"{event['headers'].get('x-goog-recaptcha-action', '(none)')}"
            )

        page.on("request", on_request)

        print(f"Navigating to Flow… (profile: {profile_name})")
        await page.goto("https://labs.google/fx/tools/flow?hl=en", wait_until="networkidle")
        print(f"Capturing aisandbox-pa traffic for {wait_seconds}s…")
        await asyncio.sleep(wait_seconds)
        print(f"Captured {len(captured)} request(s). Writing to {out_path} …")
        with out_path.open("w", encoding="utf-8") as f:
            for event in captured:
                f.write(json.dumps(event) + "\n")
        print("Done.")
        await ctx.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="gflow profile name (e.g. my-profile)")
    parser.add_argument("--wait", type=int, default=30, help="Seconds to keep capturing")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSONL path (default: tmp/captured/flow_outgoing_<utc>.jsonl — gitignored)",
    )
    args = parser.parse_args()
    out = args.out or (
        Path("tmp")
        / "captured"
        / f"flow_outgoing_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.jsonl"
    )
    asyncio.run(run(args.profile, args.wait, out))


if __name__ == "__main__":
    main()
