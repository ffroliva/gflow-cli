"""Spike: what does a stale profile's labs.google NextAuth do during a renewal?

Usage: uv run python scripts/dev/spike_labs_nextauth_refresh.py <profile> quick|settle
Logs request paths, statuses and the session body's `error` key only — never tokens.
"""

import asyncio
import json
import sys
import time
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from gflow_cli.browser_manager import channel_for_profile, ensure_profile_engine_compatible
from gflow_cli.config import get_settings
from gflow_cli.profile_lease import ProfileLease

name, mode = sys.argv[1], sys.argv[2]  # mode: quick | settle
pdir = get_settings().profile_subdir(name)
SESSION = "https://labs.google/fx/api/auth/session"


def shape(body: str) -> dict:
    try:
        d = json.loads(body)
    except Exception:
        return {"json": False}
    return {
        "keys": sorted(d),
        "has_token": bool(d.get("access_token")),
        "error": d.get("error"),
        "expires": d.get("expires"),
    }


async def main():
    t0 = time.time()
    async with ProfileLease(pdir):
        # #477: never open a profile with an older engine than the one that wrote it.
        ensure_profile_engine_compatible(pdir, channel_for_profile(pdir))
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                str(pdir),
                channel=channel_for_profile(pdir),
                headless=True,
                ignore_default_args=["--enable-automation", "--no-sandbox"],
                args=[
                    "--password-store=basic",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            print("channel", channel_for_profile(pdir))
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            def on_resp(r):
                u = urlsplit(r.url)
                if (
                    "auth" in u.path or "accounts.google" in u.netloc or "oauth" in u.path
                ) and u.netloc in ("labs.google", "accounts.google.com", "flow.google.com"):
                    print(
                        f"  {time.time() - t0:5.1f}s {r.request.method} {r.status} {u.netloc}{u.path}"
                    )

            page.on("response", on_resp)
            r = await ctx.request.get(SESSION)
            print("before:", r.status, shape(await r.text()))
            if mode == "settle":
                try:
                    await page.goto(
                        "https://labs.google/fx/", wait_until="networkidle", timeout=30000
                    )
                except Exception as e:
                    print("networkidle miss", type(e).__name__)
                await asyncio.sleep(2.5)
            else:
                await page.goto(
                    "https://labs.google/fx/", wait_until="domcontentloaded", timeout=30000
                )
            print("landed", urlsplit(page.url).netloc + urlsplit(page.url).path)
            r = await page.request.get(SESSION)
            print("after:", r.status, shape(await r.text()))
            await ctx.close()


asyncio.run(main())
