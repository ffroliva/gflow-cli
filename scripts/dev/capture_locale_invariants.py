"""Capture DOM attributes from Flow across different locales to find invariants."""

import asyncio
import json
from pathlib import Path
import sys

from playwright.async_api import async_playwright

# Add src to path so we can import from gflow_cli
sys.path.append(str(Path(__file__).parent.parent.parent / "src"))

from gflow_cli.paths import profile_subdir, default_home

LOCALES = ["en-US", "pt-BR", "es-ES"]
FLOW_URL = "https://labs.google/fx/tools/flow"

async def capture_locale(locale: str):
    print(f"Capturing locale: {locale}")
    async with async_playwright() as p:
        p_dir = profile_subdir(default_home(), "denon82")
        
        # Try to launch with retries to handle lock files
        browser = None
        for i in range(3):
            try:
                browser = await p.chromium.launch_persistent_context(
                    str(p_dir),
                    headless=False,
                    locale=locale,
                    args=["--disable-blink-features=AutomationControlled"]
                )
                break
            except Exception as e:
                print(f"Attempt {i+1} failed to launch browser: {e}")
                await asyncio.sleep(2)
        
        if not browser:
            print(f"Failed to launch browser for {locale} after 3 attempts.")
            return None
        
        page = browser.pages[0]
        await page.goto(f"{FLOW_URL}?hl={locale.split('-')[0]}")
        
        # Wait for editor to load
        print("Waiting for editor...")
        try:
            await page.wait_for_selector('div[role="textbox"]', timeout=60000)
        except:
            print(f"Timeout waiting for editor in {locale}. Check auth.")
            await browser.close()
            return None

        # Capture interesting elements
        data = await page.evaluate("""() => {
            const getMeta = (el) => ({
                tag: el.tagName,
                text: el.innerText?.slice(0, 50),
                aria_label: el.getAttribute('aria-label'),
                aria_controls: el.getAttribute('aria-controls'),
                role: el.getAttribute('role'),
                data_testid: el.getAttribute('data-testid'),
                id: el.id
            });

            return {
                tabs: Array.from(document.querySelectorAll('[role="tab"]')).map(getMeta),
                buttons: Array.from(document.querySelectorAll('button')).map(getMeta),
                textboxes: Array.from(document.querySelectorAll('[role="textbox"]')).map(getMeta)
            };
        }""")
        
        await browser.close()
        return data

async def main():
    results = {}
    for locale in LOCALES:
        results[locale] = await capture_locale(locale)
    
    out_path = Path("tmp/locale_discovery.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"Results saved to {out_path}")

if __name__ == "__main__":
    asyncio.run(main())
