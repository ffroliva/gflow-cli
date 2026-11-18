"""Durable verification tool for 'Real Chrome' authentication viability.

This script launches the system's installed Google Chrome via Playwright with 
an isolated user-data-dir and navigates to the Google Login page.

It serves as the definitive 'ground truth' prober for:
1. G12 Block: Does Google accept this browser/CDP configuration?
2. Isolation: Does it run side-by-side with the user's daily Chrome profile?
3. Environment: Is Chrome correctly detected and launchable via Playwright?

USAGE:
    uv run scripts/verify_chrome_auth_viability.py
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

import structlog
from playwright.async_api import async_playwright

from gflow_cli.observability import configure_logging

log = structlog.get_logger()


async def verify_viability() -> bool:
    """Run the empirical auth viability check."""
    configure_logging()

    tmp_dir = Path(tempfile.mkdtemp(prefix="gflow-auth-verify-"))
    log.info("verification_start", tmp_dir=str(tmp_dir))

    try:
        async with async_playwright() as p:
            log.info("probing_chrome_executable")
            try:
                # This is the industry-standard way to find the real Chrome path
                chrome_path = p.chromium.executable_path
                log.info("chrome_path_found", path=chrome_path)
            except Exception as e:
                log.error("chrome_detection_failed", error=str(e))
                print("\n[ERROR] Google Chrome was not detected on this system.")
                print("Please ensure Chrome is installed or use the bundled fallback.")
                return False

            log.info("launching_stealth_chrome", channel="chrome")
            context = await p.chromium.launch_persistent_context(
                user_data_dir=tmp_dir,
                channel="chrome",
                headless=False,
                ignore_default_args=["--enable-automation", "--no-sandbox"],
                # Required: prevents Blink from setting navigator.webdriver as a
                # non-configurable native property before our JS init script runs.
                # A cosmetic "unsupported flag" notice may appear — expected and harmless.
                args=["--disable-blink-features=AutomationControlled"],
            )

            # Register stealth script BEFORE creating/accessing any page.
            # add_init_script fires at Page.addScriptToEvaluateOnNewDocument,
            # before any page JS, on every navigation including cross-origin redirects.
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            """)

            page = await context.new_page()

            url = "https://labs.google/fx/tools/flow?hl=en"
            log.info("navigating_to_flow", url=url)
            await page.goto(url)

            print("\n" + "=" * 60)
            print("ACTION REQUIRED: PLEASE LOG IN")
            print("=" * 60)
            print("1. A Google Chrome window has opened.")
            print("2. I will automatically click 'Create with Flow' and your account.")
            print("3. Complete any remaining sign-in steps (password, MFA).")
            print("-" * 60)
            print("Monitoring for successful authentication...")

            # Monitoring loop: check for cookies AND URL transition
            success = False
            clicked_landing = False
            clicked_account = False

            for _ in range(120): # 10 minutes total, 5s intervals
                current_url = page.url
                cookies = await context.cookies()
                cookie_names = [c["name"] for c in cookies]

                # 1. Check for Landing Page and click through
                if not clicked_landing:
                    create_btn = page.locator('text="Create with Flow"').first
                    if await create_btn.is_visible():
                        log.debug("auth_clicking_landing_page")
                        await create_btn.click(no_wait_after=True, timeout=3000)
                        clicked_landing = True

                # 2. Check for Account Chooser and click first account
                if not clicked_account:
                    account_btn = page.locator("[data-email]").first
                    if await account_btn.is_visible():
                        email = await account_btn.get_attribute("data-email")
                        log.info("auth_selecting_account", email=email)
                        await account_btn.click(no_wait_after=True, timeout=3000)
                        clicked_account = True

                # Success Condition: Landed in the tool with valid cookies
                if "fx/tools/flow" in current_url and "SAPISID" in cookie_names:
                    # Double check we aren't just on the landing page
                    if await page.locator('text="New project"').count() > 0 or \
                       await page.locator('text="Your projects"').count() > 0:
                        success = True
                        break

                await asyncio.sleep(5)

            if success:
                print("\n[SUCCESS] Authentication verified! Session is active.")
                log.info("verification_result", status="PASSED", cookie_count=len(cookies))

                print("\n" + "=" * 60)
                print("TEST: GENERATE BATCH OF IMAGES?")
                print("=" * 60)
                print("Would you like to verify the session by generating")
                print("2 test images in the SAME browser window? [y/n]")

                while True:
                    response = input("> ").lower().strip()
                    if response in ["y", "yes"]:
                        # Run the test generation using the ALREADY OPEN context
                        return await _run_test_generation_in_context(context, tmp_dir)
                    if response in ["n", "no"]:
                        return True
                    print("Please enter 'y' or 'n'.")
            else:
                print("\n[FAILURE] Authentication timed out or failed.")
                return False

    except Exception as e:
        log.exception("verification_error", error=str(e))
        return False
    finally:
        if "context" in locals():
            log.info("closing_browser")
            try:
                # We use a timeout to avoid hanging if the browser is unresponsive
                await asyncio.wait_for(context.close(), timeout=5) # type: ignore[name-defined]
            except Exception:
                # Target already closed or unresponsive — safe to ignore
                pass

        log.info("cleaning_up", tmp_dir=str(tmp_dir))
        # Keep window open a bit longer if failed so user can see why
        await asyncio.sleep(2)
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _run_test_generation_in_context(context, tmp_dir: Path) -> bool:
    """Attempt a real generation using the existing context to guarantee persistence."""
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.image import Aspect, GenerateImageRequest, Model

    print("\nStarting test generation...")
    # We create a client that uses the existing playwright context
    client = FlowApiClient(context=context)

    try:
        log.info("finding_or_creating_project")
        project = await client.get_or_create_project("Auth Verification Test")
        print(f"  Using Project: {project.project_id}")

        for i in range(1, 3):
            prompt = "A high-tech terminal with a green glowing screen" if i == 1 else "A futuristic city skyline"
            print(f"  Generating Image {i}/2: '{prompt}'...")

            req = GenerateImageRequest(
                prompt=prompt,
                model=Model.NANO2,
                aspect=Aspect.SQUARE
            )

            images = await client.generate_images(project.project_id, req)
            if images:
                out_path = tmp_dir / f"test_image_{i}.png"
                await client.download(images[0].media_name, out_path)
                print(f"    [OK] Saved to {out_path}")
            else:
                print(f"    [FAIL] No images returned for prompt {i}")
                return False

        print("\n[RESULT] All test images generated successfully!")
        return True
    except Exception as e:
        log.exception("test_generation_failed", error=str(e))
        print(f"\n[ERROR] Test generation failed: {e}")
        return False


if __name__ == "__main__":
    if not asyncio.run(verify_viability()):
        sys.exit(1)
