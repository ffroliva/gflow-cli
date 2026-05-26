"""CDP-attach driver + frame-slot selector probe (issue #63 sanity check).

Attaches to a Chrome already launched with ``--remote-debugging-port`` (the
gflow-agent-browser-spike's ``launch-flow-chrome.ps1``), drives the I2V editor
setup via the production ``VideoGenerationMixin`` static helpers, then counts
candidate selectors against the live DOM.

Differs from ``capture_i2v_frame_slots_dom.py`` in transport: that script
launches its own Playwright persistent context; this one ATTACHES to the
user's real Chrome via CDP. Same selectors, different runtime — verifies the
fix survives both transports.

Zero credits: we only READ + navigate, never click Generate.

Usage:
    .venv/Scripts/python.exe scripts/dev/cdp_drive_and_probe.py --port 9334
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from playwright.async_api import async_playwright

sys.path.append("src")

from gflow_cli.api.transports.ui_automation import UiAutomationTransport  # noqa: E402
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    FRAME_SLOT_BY_LABEL,
    FRAME_SLOTS_STRUCT,
    VideoGenerationMixin,
)

_FLOW_URL = "https://labs.google/fx/tools/flow"


async def probe(port: int) -> int:
    cdp_url = f"http://127.0.0.1:{port}"
    print(f"Connecting via CDP to {cdp_url}...")
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        if not browser.contexts:
            print("ERROR: no contexts on the attached browser", file=sys.stderr)
            return 2
        ctx = browser.contexts[0]
        pages = ctx.pages
        page = pages[0] if pages else await ctx.new_page()
        print(f"Initial page URL: {page.url}")

        if "labs.google" not in page.url or "flow" not in page.url:
            print(f"Navigating to {_FLOW_URL} ...")
            await page.goto(_FLOW_URL, wait_until="domcontentloaded")

        # Reuse production helpers for the editor + video mode + sub-mode
        # transition. `_enter_editor` is an instance method but its body only
        # uses `self` for structlog labels; instantiating a bare transport
        # is fine here.
        transport = UiAutomationTransport()
        print("Entering editor (clicks gallery '+')...")
        await transport._enter_editor(page, None)
        print("Dismissing blocking overlays...")
        await transport._dismiss_blocking_overlays(page, None)
        print("Switching to video mode...")
        await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)
        print("Waiting for video editor SPA to mount...")
        await VideoGenerationMixin._wait_video_editor_ready(page)
        print("Switching to I2V 'frames' sub-mode...")
        await VideoGenerationMixin._switch_video_sub_mode(page, "frames", out_dir=None)
        print("Closing settings panel (Escape)...")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(600)

        candidates = {
            "OLD PR #70 SWAP_CONTAINER (broken)": (
                "div:has(> button:has(i.google-symbols:text-is('swap_horiz')))"
            ),
            "swap_horiz icon, any icon class": ("div:has(> button:has(i:text-is('swap_horiz')))"),
            "FIXED Tier 1: FRAME_SLOTS_STRUCT (current code)": FRAME_SLOTS_STRUCT,
            "Tier 2: FRAME_SLOT_BY_LABEL Start (EN-only)": (
                FRAME_SLOT_BY_LABEL.format(label="Start")
            ),
            "Tier 2: FRAME_SLOT_BY_LABEL End (EN-only)": (FRAME_SLOT_BY_LABEL.format(label="End")),
            "pt-BR variant: Inicial": (FRAME_SLOT_BY_LABEL.format(label="Inicial")),
            "pt-BR variant: Final": (FRAME_SLOT_BY_LABEL.format(label="Final")),
        }

        print()
        print("=== Selector match counts (CDP-attached, live DOM) ===")
        results: dict[str, int] = {}
        for name, sel in candidates.items():
            n = await page.locator(sel).count()
            results[name] = n
            print(f"  {n:3}  {name}")
        print()

        primary = results["FIXED Tier 1: FRAME_SLOTS_STRUCT (current code)"]
        verdict = "PASS" if primary == 2 else f"FAIL (primary count={primary}, expected 2)"
        print(f"Tier 1 verdict (CDP attach): {verdict}")
        print(f"Page URL at probe time: {page.url}")
        return 0 if primary == 2 else 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        default=9334,
        help="CDP port (must match the spike launch script's -Port)",
    )
    args = parser.parse_args()
    code = asyncio.run(probe(args.port))
    sys.exit(code)


if __name__ == "__main__":
    main()
