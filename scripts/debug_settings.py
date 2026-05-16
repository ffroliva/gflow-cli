import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

PROFILE = r"C:\Users\ffrol\AppData\Local\ffroliva\gflow-cli\profile_denon82"
OUT = Path("test_assets/debug_settings")
OUT.mkdir(parents=True, exist_ok=True)

async def main():
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            PROFILE, channel="chrome", headless=False,
            ignore_default_args=["--enable-automation", "--no-sandbox"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://labs.google/fx/tools/flow?hl=en")
        await page.wait_for_timeout(3000)

        # Enter an existing project (reuse last one)
        btn = page.locator("button:has(i.google-symbols:text('add_2'))").first
        await btn.click()
        await page.wait_for_url(lambda u: "/project/" in u, timeout=15000)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=str(OUT / "01_editor.png"))

        # Click the settings gear
        gear = page.locator("button:has(i.google-symbols:text('settings_2'))").first
        await gear.click()
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT / "02_settings_open.png"))

        # Dump all visible text/aria elements inside the settings panel
        panel_info = await page.evaluate("""
            () => {
                const els = Array.from(document.querySelectorAll(
                    '[role="dialog"] *, [role="menu"] *, [class*="settings"] *, [class*="panel"] *'
                )).filter(e => e.innerText && e.innerText.trim().length > 0 && e.innerText.trim().length < 100);
                return [...new Set(els.map(e => ({
                    tag: e.tagName,
                    text: e.innerText.trim().slice(0, 80),
                    aria: e.getAttribute('aria-label'),
                    role: e.getAttribute('role'),
                    selected: e.getAttribute('aria-selected') || e.getAttribute('aria-checked'),
                })))].slice(0, 60);
            }
        """)
        Path(str(OUT / "settings_panel.json")).write_text(json.dumps(panel_info, indent=2))
        print("=== SETTINGS PANEL CONTENTS ===")
        for el in panel_info:
            if el.get("text"):
                print(f"  [{el['tag']}] text={el['text']!r} aria={el['aria']!r} role={el['role']!r} selected={el['selected']!r}")

        await ctx.close()

asyncio.run(main())
