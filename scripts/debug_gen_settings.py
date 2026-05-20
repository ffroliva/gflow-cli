import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from gflow_cli.auth import profile_dir as resolve_profile_dir


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Debug: inspect Flow generation settings panel")
    p.add_argument("--profile", default="default", help="gflow profile name (default: default)")
    return p.parse_args()


async def main() -> None:
    args = _parse_args()
    profile_path = resolve_profile_dir(args.profile)
    out = Path("tmp") / "debug" / "gen_settings"
    out.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(profile_path), channel="chrome", headless=False,
            ignore_default_args=["--enable-automation", "--no-sandbox"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://labs.google/fx/tools/flow?hl=en")
        await page.wait_for_timeout(3000)

        # Enter a project
        btn = page.locator("button:has(i.google-symbols:text('add_2'))").first
        await btn.click()
        await page.wait_for_url(lambda u: "/project/" in u, timeout=15000)
        await page.wait_for_timeout(2000)

        # Type something so the prompt toolbar fully appears
        editor = page.locator('div[role="textbox"][data-slate-editor="true"]').first
        await editor.click()
        await page.keyboard.insert_text("test")
        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(out / "01_with_text.png"))

        # Find the crop/aspect ratio + count button (shows crop_16_9 and x2)
        all_btns = await page.evaluate("""
            () => Array.from(document.querySelectorAll("button")).map(b => ({
                text: b.innerText.trim().slice(0, 100),
                aria: b.getAttribute("aria-label"),
                cls: b.className.slice(0, 60),
            })).filter(b => b.text || b.aria)
        """)
        print("=== ALL BUTTONS WITH TEXT ===")
        for b in all_btns:
            print(f"  text={b['text']!r} aria={b['aria']!r}")

        # Click the button containing crop_16_9 (aspect ratio / count selector)
        crop_btn = page.locator("button:has-text('crop_16_9')").first
        count = await crop_btn.count()
        print(f"\nFound crop_16_9 button: {count}")
        if count:
            await crop_btn.click()
            await page.wait_for_timeout(1500)
            await page.screenshot(path=str(out / "02_gen_settings_panel.png"))

            panel = await page.evaluate("""
                () => Array.from(document.querySelectorAll(
                    '[role="dialog"] *, [role="menu"] *, [role="listbox"] *, [role="radiogroup"] *'
                )).filter(e => e.innerText?.trim()).map(e => ({
                    tag: e.tagName,
                    text: e.innerText.trim().slice(0, 80),
                    role: e.getAttribute('role'),
                    selected: e.getAttribute('aria-selected') || e.getAttribute('aria-checked') || e.getAttribute('aria-pressed'),
                }))
            """)
            print("\n=== GENERATION SETTINGS PANEL ===")
            for el in panel:
                if el.get("text"):
                    print(f"  [{el['tag']}] text={el['text']!r} role={el['role']!r} selected={el['selected']!r}")

        await ctx.close()

asyncio.run(main())
