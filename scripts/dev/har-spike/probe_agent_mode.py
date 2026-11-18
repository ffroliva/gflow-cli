"""Probe whether a gflow profile lands Flow in agent mode (manual, headed).

Takes the profile by NAME and resolves it through gflow's own profile store,
so the script runs on any machine and any account. It used to hardcode one
developer's absolute Windows path — unrunnable for anyone else, and a
repo-hygiene violation caught by scripts/ci/check_repo_hygiene.py the moment
this harness moved in-tree.

    python scripts/dev/har-spike/probe_agent_mode.py [profile-name]
"""

import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _spike_common import resolve_profile_dir  # noqa: E402


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", nargs="?", default="default", help="gflow profile name")
    args = parser.parse_args()

    profile_dir = str(resolve_profile_dir(args.profile))
    if not Path(profile_dir).exists():
        print(f"Error: Profile directory does not exist: {profile_dir}")
        sys.exit(1)

    print(f"Launching Chrome with profile: {profile_dir}...")
    async with async_playwright() as p:
        # Launch persistent context using system Google Chrome
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                channel="chrome",
                headless=False,
                no_viewport=True
            )
        except Exception as e:
            print(f"Error: Could not launch Chrome. Is another Chrome instance using this profile? Details: {e}")
            sys.exit(1)

        page = await context.new_page()
        url = "https://labs.google/fx/tools/flow?hl=en"
        print(f"Opening Flow URL: {url}...")
        await page.goto(url)

        print("\n" + "="*80)
        print("Please use the opened Chrome window to navigate to a Flow project.")
        print("Once you are on the project page, return here.")
        print("="*80 + "\n")

        input("Press Enter once you have loaded a Flow project...")

        print("-" * 60)
        print(f"Active page URL: {page.url}")
        print(f"Active page title: {await page.title()}")
        print("-" * 60)

        # 1. Probe the DOM of the active project page
        body_text = await page.inner_text("body")
        has_agent_text = "agent" in body_text.lower()
        print(f"Contains 'Agent' text on page: {has_agent_text}")

        close_btn_selector = "button:has(i.google-symbols:text-is('close'))"
        agent_btn_selector = "button:has(i.google-symbols:text-is('article_spark'))"
        add_card_selector = "#instruction-add-card"

        close_btn = page.locator(close_btn_selector).first
        agent_btn = page.locator(agent_btn_selector).first
        add_card = page.locator(add_card_selector).first

        close_present = await close_btn.count() > 0
        agent_present = await agent_btn.count() > 0
        add_card_present = await add_card.count() > 0

        print(f"article_spark button (opens sidebar) present: {agent_present}")
        print(f"close button (closes sidebar) present: {close_present}")
        print(f"instruction-add-card present: {add_card_present}")
        print("-" * 60)

        # Check if the sidebar button is present
        if agent_present:
            print("Clicking article_spark button to toggle sidebar...")
            await agent_btn.click()
            await asyncio.sleep(2)

            close_present_after = await page.locator(close_btn_selector).first.count() > 0
            add_card_present_after = await page.locator(add_card_selector).first.count() > 0

            print("After click:")
            print(f"  close button present: {close_present_after}")
            print(f"  instruction-add-card present: {add_card_present_after}")

            # Print active instruction cards if present
            if add_card_present_after:
                textareas = await page.locator("textarea[placeholder='Create a guideline for your agent'], textarea.gCefUl").all()
                print(f"  Found {len(textareas)} instruction card textareas.")
                for i, ta in enumerate(textareas):
                    val = await ta.input_value()
                    print(f"    Card {i}: '{val}'")

            # Click close to restore original state
            if close_present_after:
                print("Clicking close to restore sidebar state...")
                await page.locator(close_btn_selector).first.click()
                await asyncio.sleep(1)
        else:
            print("Could not find article_spark button. Make sure Agent Mode is enabled on the project.")

        print("\nClosing browser...")
        await context.close()

if __name__ == "__main__":
    asyncio.run(main())
