import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from gflow_cli.auth import profile_dir as resolve_profile_dir


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Debug: dump Flow editor buttons to tmp/")
    p.add_argument("--profile", default="default", help="gflow profile name (default: default)")
    return p.parse_args()


async def main() -> None:
    args = _parse_args()
    profile_path = resolve_profile_dir(args.profile)
    out = Path("tmp") / "debug" / "editor"
    out.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(profile_path),
            channel="chrome",
            headless=False,
            ignore_default_args=["--enable-automation", "--no-sandbox"],
            args=["--disable-blink-features=AutomationControlled"],
            slow_mo=500,
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://labs.google/fx/tools/flow?hl=en")
        await page.wait_for_timeout(3000)

        # Click new project
        btn = page.locator("button:has(i.google-symbols:text('add_2'))").first
        await btn.click()
        await page.wait_for_timeout(3000)
        await page.screenshot(path=str(out / "01_editor_loaded.png"), full_page=True)

        # Type prompt
        editor = page.locator('div[role="textbox"][data-slate-editor="true"]').first
        await editor.click()
        await page.keyboard.type("test prompt steampunk airship")
        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(out / "02_prompt_typed.png"), full_page=True)

        # Dump all buttons in the DOM
        buttons = await page.evaluate("""
            () => Array.from(document.querySelectorAll("button")).map(b => ({
                tag: "button",
                text: b.innerText.trim().slice(0,80),
                aria: b.getAttribute("aria-label"),
                cls: b.className.slice(0,80),
                type: b.getAttribute("type"),
                disabled: b.disabled
            }))
        """)
        import json
        Path(str(out / "buttons.json")).write_text(json.dumps(buttons, indent=2))
        print("Buttons dumped to", out / "buttons.json")
        print(f"Total buttons: {len(buttons)}")
        for b in buttons:
            if b["text"] or b["aria"]:
                print(f"  text={b['text']!r} aria={b['aria']!r} cls={b['cls']!r}")

        await ctx.close()

asyncio.run(main())
