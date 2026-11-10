"""#1140 probe: does ONE merged --disable-features switch keep Playwright's list AND
stop CloudSyncedFolderChecker from holding Chrome's close for 30 s?

Four headed real-Chrome arms on a throwaway profile, each held open >2 s (a window
closed within ~2 s does not show the hang):

  none    no switch of ours           expect: http stays http, slow close
  lone    --disable-features=CloudSyncedFolderChecker
                                      expect: http UPGRADED (Playwright's list lost)
                                      -- the positive control: if this arm stays http,
                                      the probe cannot see the regression at all
  merged  disable_features_args()     expect: http stays http, fast close
  login   login_launch_kwargs(...)    expect: as merged, navigator.webdriver unchanged

Run:  uv run python scripts/dev/spike_disable_features_merge.py
No Flow account, no credits. Prints one line per arm.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from gflow_cli.auth.internal_chromium import login_launch_kwargs
from gflow_cli.browser_manager import disable_features_args

_URL = "http://example.com/"
_CLOSE_LIMIT_S = 45.0


async def _arm(pw: Any, name: str, kwargs: dict[str, Any]) -> None:
    ctx = await pw.chromium.launch_persistent_context(**kwargs)
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(_URL, wait_until="domcontentloaded", timeout=30_000)
        final = page.url
        webdriver = await page.evaluate("navigator.webdriver")
        version = await ctx.new_page()
        await version.goto("chrome://version", timeout=15_000)
        cmdline = await version.locator("#command_line").inner_text()
        switches = [s for s in cmdline.split() if s.startswith("--disable-features")]
        await asyncio.sleep(3)  # past the ~2 s window that hides the hang
    finally:
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(ctx.close(), timeout=_CLOSE_LIMIT_S)
            closed = f"{time.monotonic() - t0:.1f}s"
        except TimeoutError:
            closed = f">{_CLOSE_LIMIT_S:.0f}s (timed out)"
    last = switches[-1].split("=", 1)[1].split(",") if switches else []
    print(
        f"{name:7} url={final} webdriver={webdriver} close={closed} "
        f"disable_features_switches={len(switches)} last_has_HttpsUpgrades="
        f"{'HttpsUpgrades' in last} last_has_CloudSynced={'CloudSyncedFolderChecker' in last}",
        flush=True,
    )


async def main() -> None:
    (merged,) = disable_features_args()
    async with async_playwright() as pw:
        for name, extra in (
            ("none", []),
            ("lone", ["--disable-features=CloudSyncedFolderChecker"]),
            ("merged", [merged]),
        ):
            with tempfile.TemporaryDirectory(dir="tmp") as d:
                await _arm(
                    pw,
                    name,
                    {"user_data_dir": d, "channel": "chrome", "headless": False, "args": extra},
                )
        with tempfile.TemporaryDirectory(dir="tmp") as d:
            await _arm(pw, "login", login_launch_kwargs(Path(d), False, channel="chrome"))


if __name__ == "__main__":
    Path("tmp").mkdir(exist_ok=True)
    asyncio.run(main())
