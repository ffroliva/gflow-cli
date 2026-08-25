"""THROWAWAY SPIKE — isolate WHY a profile-less browser cannot render Flow's editor.

The previous run changed two variables at once (bundled chromium AND cookie
injection instead of a profile), so it could not attribute the failure. This
varies ONE thing at a time and carries a positive control in the same run, so a
Flow outage or a stale selector cannot be mistaken for a negative result.

    CONTROL  real Chrome + full profile          expect HIT (proves setup valid NOW)
    D        real Chrome, fresh ctx, labs jar    browser? or cookies?
    E        real Chrome, fresh ctx, FULL jar    do .google.com cookies fix it?
    F        bundled chromium, fresh ctx, FULL   does the browser binary matter?

Reading:
    D HIT              -> cause was the browser binary; CI runners HAVE Chrome -> viable
    D miss, E HIT      -> cause is Google cookies -> ~10min PSIDTS rotation -> blocked
    E miss, F miss     -> profile state (localStorage/IndexedDB) required -> blocked

$0 throughout: navigation and DOM reads only. Cookie values never printed.
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, r"C:\development\github\gflow-cli\src")
sys.path.insert(0, r"C:\development\github\gflow-cli")

from playwright.async_api import async_playwright  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.client import FlowApiClient  # noqa: E402
from gflow_cli.api.transports.mode_control import AGENT_TOGGLE_SELECTOR  # noqa: E402
from gflow_cli.api.transports.ui_automation import PROMPT_INPUT_SELECTORS  # noqa: E402
from gflow_cli.auth import profile_dir as resolve  # noqa: E402

# Set at runtime from a FRESH project. A stale/deleted id renders an error shell
# that looks exactly like an auth wall (~441KB, 3 buttons, 0 icons) — which is
# what voided the first matrix run.
URL = ""
FIELDS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")

PROBES = {
    "composer": PROMPT_INPUT_SELECTORS[0],
    "agent_toggle": AGENT_TOGGLE_SELECTOR,
    "icons": "i.google-symbols",
    "slate": "[data-slate-editor]",
}
RESULTS: list[tuple[str, str, dict[str, int]]] = []


async def measure(page, arm: str, note: str) -> None:
    await page.goto(URL, wait_until="domcontentloaded", timeout=90_000)
    # Wait for the SPA to hydrate rather than guessing: poll for icons, cap at 25s.
    for _ in range(25):
        if await page.locator("i.google-symbols").count():
            break
        await page.wait_for_timeout(1000)
    counts = {k: await page.locator(s).count() for k, s in PROBES.items()}
    RESULTS.append((arm, note, counts))
    print(f"  {arm:9} {note:34} {counts}")


async def control_and_harvest() -> list[dict]:
    """Positive control on the real profile, and harvest the FULL cookie jar.

    ctx.cookies() is called with NO url filter: filtering by url silently drops
    subpath-scoped cookies (issue #222/#230).
    """
    global URL
    async with FlowApiClient(profile_dir=resolve("denon82"), headless=True) as c:
        proj = await c.create_project(title="ci-matrix control")  # $0, and guarantees it EXISTS
        URL = routes.project_editor_url("en", proj.project_id)
        print(f"  (fresh project {proj.project_id})")
        page = c._page or c._context.pages[0]  # noqa: SLF001
        await measure(page, "CONTROL", "real Chrome + full profile")
        return list(await c._context.cookies())  # noqa: SLF001 - unfiltered, all domains


async def arm(label: str, note: str, channel: str | None, cookies: list[dict]) -> None:
    async with async_playwright() as pw:
        kwargs = {"headless": True}
        if channel:
            kwargs["channel"] = channel
        try:
            browser = await pw.chromium.launch(**kwargs)
        except Exception as exc:  # noqa: BLE001
            print(f"  {label:9} LAUNCH FAILED: {type(exc).__name__}")
            return
        try:
            ctx = await browser.new_context()
            await ctx.add_cookies([{k: ck[k] for k in FIELDS} for ck in cookies])
            await measure(await ctx.new_page(), label, note)
        finally:
            await browser.close()


async def main() -> None:
    print("arm       setup                              counts")
    jar = await control_and_harvest()
    labs = [c for c in jar if "labs.google" in c["domain"]]
    print(f"\n  (harvested {len(jar)} cookies total, {len(labs)} on labs.google)\n")

    await arm("D", "real Chrome, fresh ctx, labs jar", "chrome", labs)
    await arm("E", "real Chrome, fresh ctx, FULL jar", "chrome", jar)
    await arm("F", "bundled chromium, fresh, FULL jar", None, jar)
    # G is the EXACT CI shape: no real Chrome, no Google cookies, no profile.
    await arm("G", "bundled chromium, labs jar ONLY", None, labs)
    # H: is the single session cookie sufficient on its own?
    only = [c for c in labs if c["name"] == "__Secure-next-auth.session-token"]
    await arm("H", "bundled chromium, session cookie ONLY", None, only)

    print("\n--- verdict ---")
    by = {a: c for a, _, c in RESULTS}
    ctrl = by.get("CONTROL", {})
    if not ctrl.get("composer"):
        print("CONTROL FAILED — result is VOID (Flow down, or selector already drifted).")
        return
    if by.get("H", {}).get("composer"):
        print("H HIT -> ONE 30-day cookie + bundled chromium is enough. CI capture VIABLE.")
    elif by.get("G", {}).get("composer"):
        print("G HIT -> labs.google jar + bundled chromium is enough. CI capture VIABLE.")
    elif by.get("D", {}).get("composer"):
        print("D HIT -> needs real Chrome. Runners ship Chrome, so still viable.")
    elif by.get("E", {}).get("composer"):
        print("E HIT -> needs .google.com cookies -> PSIDTS rotates ~10min -> CI BLOCKED.")
    else:
        print("D+E miss -> profile state beyond cookies is required -> CI BLOCKED.")


asyncio.run(main())
