"""Diagnostic — what site_key + token does gflow-cli's TokenMinter produce?

Run: `uv run python scripts/diag_recaptcha_mint.py --profile denon82`

Opens HEADED Chromium, navigates to Flow, runs TokenMinter, prints the
discovered site_key + minted token length. Compare to HAR ground truth:
  - Real site_key: 6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV
  - Real token length: 2233 chars
  - Real mint endpoint: https://www.google.com/recaptcha/enterprise/reload
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from gflow_cli.api.recaptcha import TokenMinter, discover_site_key
from gflow_cli.auth import profile_dir


async def run(profile_name: str) -> None:
    pdir = profile_dir(profile_name)
    print(f"Profile: {pdir}")

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(pdir),
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        print("Navigating to Flow editor (networkidle)...")
        await page.goto(
            "https://labs.google/fx/tools/flow?hl=en",
            wait_until="networkidle",
            timeout=60_000,
        )
        # Give Flow's JS extra time to bootstrap reCAPTCHA
        await asyncio.sleep(3)

        print("Discovering site_key...")
        try:
            site_key = await discover_site_key(page)
            print(f"  site_key: {site_key}")
            print(f"  matches HAR (6LdsFi...): {site_key == '6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV'}")
        except Exception as exc:
            print(f"  FAILED: {exc}")
            await ctx.close()
            return

        minter = TokenMinter(page)
        for action in ["imageGeneration", "videoGeneration", "submit", "homepage"]:
            print(f"Minting token for action={action!r}...")
            try:
                token = await minter.mint(action)
                print(f"  length: {len(token)} chars (HAR baseline: 2233)")
                print(f"  prefix: {token[:32]}...")
                print(f"  matches HAR prefix (0cAFcWeA): {token.startswith('0cAFcWeA')}")
            except Exception as exc:
                print(f"  FAILED: {exc}")

        await ctx.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.profile))


if __name__ == "__main__":
    main()
