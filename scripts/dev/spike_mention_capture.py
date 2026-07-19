#!/usr/bin/env python3
r"""Asset-tagging mention capture spike — T-1..T-5.

This script automates the UI to dump the dropdown DOM on typing `@` (T-1),
and captures the POST bodies on image (T-3) and video (T-4/T-5) paths to confirm
the wire serialization format (H1 vs H2). All POST requests are aborted before
reaching Google to protect credits.

Usage (headed, supervised):

    ! .venv\Scripts\python.exe scripts\dev\spike_mention_capture.py \
        --profile default --project <project_id> --name Zoro --ingredient logo
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports.ui_automation_image import ImageGenerationMixin  # noqa: E402
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    VideoGenerationMixin,
)

_VIDEO_GEN_ROUTE = "**/video:batchAsyncGenerateVideo*"
_IMAGE_GEN_ROUTE = "**/image:batchAsyncGenerateImage*"
_PROMPT_BOX = "div[role='textbox'][data-slate-editor='true']"
_SUBMIT_BTN = "button:has(i.google-symbols:text('arrow_forward'))"


async def _run(
    *,
    profile_dir: Path,
    project_id: str,
    name: str,
    ingredient: str,
    out_path: Path,
) -> int:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    captured: dict[str, Any] = {}

    async def _on_video_route(route: Any) -> None:
        req = route.request
        step("intercept", f"Video generate request: {req.url}", prefix="spike")
        captured["video_post_data"] = req.post_data
        await route.abort()

    async def _on_image_route(route: Any) -> None:
        req = route.request
        step("intercept", f"Image generate request: {req.url}", prefix="spike")
        captured["image_post_data"] = req.post_data
        await route.abort()

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001

        await page.route(_VIDEO_GEN_ROUTE, _on_video_route)
        await page.route(_IMAGE_GEN_ROUTE, _on_image_route)

        locale = "en"  # default
        url = routes.project_editor_url(locale, project_id)
        step("1", f"Navigating to {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(4_000)
        await page.keyboard.press("Escape")

        # T-1: Dropdown DOM probe
        step("T-1", "Focusing prompt box and typing '@'...")
        box = page.locator(_PROMPT_BOX).first
        await box.wait_for(state="visible", timeout=8000)
        await box.click()
        await page.wait_for_timeout(500)
        await page.keyboard.type("@")
        await page.wait_for_timeout(2000)

        # Look for potential dropdown containers
        dropdown_selectors = [
            "[role='listbox']",
            "[role='menu']",
            ".google-symbols",
            "div:has-text('Character')",
            "div:has-text('Assets')",
        ]
        dropdown_dump = {}
        for sel in dropdown_selectors:
            locs = page.locator(sel)
            count = await locs.count()
            if count > 0:
                dropdown_dump[sel] = []
                for i in range(count):
                    html = await locs.nth(i).evaluate("el => el.outerHTML")
                    dropdown_dump[sel].append(html)

        out_dropdown = out_dir / "T-1_dropdown_dom.json"
        out_dropdown.write_text(json.dumps(dropdown_dump, indent=2), encoding="utf-8")
        step("T-1", f"Dumped dropdown HTML selectors to {out_dropdown}")

        # T-2: Slate chip node dump (select first option, escape to close dropdown but keep chip)
        step("T-2", "Selecting the first dropdown option...")
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(1000)

        box_html = await box.evaluate("el => el.outerHTML")
        out_slate = out_dir / "T-2_slate_chip_dom.html"
        out_slate.write_text(box_html, encoding="utf-8")
        step("T-2", f"Dumped prompt box Slate HTML to {out_slate}")

        # Clear prompt box
        await box.click()
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(500)

        # T-3: Image path: submit with character mention
        step("T-3", "Switching to Image mode...")
        # (Image mode switch code)
        await ImageGenerationMixin._switch_to_image_mode(page, out_dir=None)  # noqa: SLF001
        await page.wait_for_timeout(1000)

        step("T-3", f"Typing prompt '@{name} walking' for image submit capture...")
        await box.click()
        await page.keyboard.type(f"@{name} walking")
        await page.wait_for_timeout(1000)
        # Select from dropdown if open
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)

        step("T-3", "Submitting (will intercept)...")
        submit = page.locator(_SUBMIT_BTN).first
        await submit.click()
        await page.wait_for_timeout(3000)

        # Clear prompt
        await box.click()
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")

        # T-4: Video path: submit with character mention
        step("T-4", "Switching to Video mode...")
        await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)  # noqa: SLF001
        await page.wait_for_timeout(1000)

        step("T-4", f"Typing prompt '@{name} running' for video submit capture...")
        await box.click()
        await page.keyboard.type(f"@{name} running")
        await page.wait_for_timeout(1000)
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)

        step("T-4", "Submitting (will intercept)...")
        await submit.click()
        await page.wait_for_timeout(3000)

        # Save all captured payloads
        out_payloads = out_dir / "T-3_T-4_captured_payloads.json"
        out_payloads.write_text(json.dumps(captured, indent=2), encoding="utf-8")
        step("finished", f"Captured payloads saved to {out_payloads}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike mention capture script")
    parser.add_argument("--profile", default="default", help="Chrome profile name")
    parser.add_argument("--project", required=True, help="Flow Project ID")
    parser.add_argument("--name", default="Zoro", help="Character name to mention")
    parser.add_argument("--ingredient", default="logo", help="Ingredient name to mention")
    parser.add_argument(
        "--out-path",
        type=Path,
        help="Custom output file path",
    )

    args = parser.parse_args()
    profile_dir = resolve_profile_dir(args.profile)
    out_path = args.out_path or default_out_path("spike_mention_capture", ".json")

    return asyncio.run(
        _run(
            profile_dir=profile_dir,
            project_id=args.project,
            name=args.name,
            ingredient=args.ingredient,
            out_path=out_path,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
