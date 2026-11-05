r"""Does the workspace check accept a session revoked on the server? (#1144) $0.

Run this on a DISPOSABLE profile only: it signs that profile out of Google.

    python scripts/dev/spike_revoked_session.py <profile>
    python scripts/dev/spike_workspace_oracle.py <profile>-revoked

Copying first freezes the cookies on disk. The original then signs out through Google's own
logout page, which ends the session the copy still holds. My Account is the control: while it
accepts the copy, the session is alive and the second script's forced arm proves nothing.

Pre-registered readings (2026-11-05, before the run):

| control | forced | Reading |
|---|---|---|
| None | None | the fallback refuses a revoked session, so #1155 can merge |
| None | authenticated | FALSE POSITIVE, #1155 stops |
| authenticated | any | the session is still alive, so the test is void |
"""

from __future__ import annotations

import asyncio
import shutil
import sys

from gflow_cli.auth import profile_dir
from gflow_cli.auth.strategies import async_playwright
from gflow_cli.browser_manager import channel_for_profile

_SKIP = shutil.ignore_patterns("Singleton*", "lockfile", "*.lock", "Cache", "Code Cache", "GPUCache")


async def sign_out(profile: str) -> None:
    path = profile_dir(profile)
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(path),
            channel=channel_for_profile(path),
            headless=True,
            ignore_default_args=["--enable-automation", "--no-sandbox"],
            args=["--password-store=basic", "--disable-blink-features=AutomationControlled"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://accounts.google.com/Logout", wait_until="load", timeout=30_000)
            await page.wait_for_timeout(5000)
            names = {c.get("name") for c in await ctx.cookies("https://accounts.google.com")}
            print("logout landed on", page.url.split("?")[0], "SID kept:", "SID" in names)
        finally:
            await ctx.close()


def main(profile: str) -> None:
    source = profile_dir(profile)
    copy = profile_dir(f"{profile}-revoked")
    if copy.exists():
        sys.exit(f"{copy.name} already exists; remove it first")
    shutil.copytree(source, copy, ignore=_SKIP)
    print("copied to", copy.name)
    asyncio.run(sign_out(profile))


if __name__ == "__main__":
    main(sys.argv[1])
