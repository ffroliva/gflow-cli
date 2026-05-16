"""Smoke — Worker-style Playwright UI mimicry -> PNG.

Mimics the proven CG Worker (``python/workers/google-flow-worker/flow_logic.py``)
approach: Playwright's ``launch_persistent_context`` manages its own headed
Chromium with an *internal* random CDP port (NOT the externally-visible
``--remote-debugging-port=9222`` that our earlier CDP-attach smoke exposed).

Why this matters: empirical signal from ``smoke_real_chrome_image.py`` showed
that real Chrome spawned with ``--remote-debugging-port=9222`` still failed
reCAPTCHA with ``PUBLIC_ERROR_UNUSUAL_ACTIVITY`` — Google's risk scoring flags
the externally-exposed debug port as automation, regardless of binary or
account. The Worker, however, ships images daily using Playwright's internal
CDP path (no public port). So we mirror its lifecycle here.

Selectors and the gallery -> editor flow are pinned copies of the Worker's
``flow_logic.py`` (DO NOT cross-import from the monorepo). Refresh the copies
if Flow's DOM evolves.

First-run UX:
  1. Script launches Chromium against ``--profile-dir`` (default:
     ``~/gflow-worker-style-smoke`` — a fresh dir,
     Playwright-Chromium-compatible).
  2. If not signed in to Flow, polls every 5s until you authenticate
     manually inside the open Chromium window (no stdin needed; works under
     ``uv run`` on Windows).
  3. Once authenticated, clicks "+ New project", types the prompt, clicks
     Create, captures the ``batchGenerateImages`` response, downloads each
     PNG to ``tmp/smoke/<utc>/``.

Subsequent runs reuse the same profile dir (cookies + Flow session persist).

Usage::

    uv run python scripts/smoke_worker_style.py \\
        --profile-dir ~/gflow-worker-style-smoke \\
        --prompt "a calm forest at dawn, cinematic photography, 16:9"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
import structlog
from playwright.async_api import BrowserContext, Page, Response, async_playwright

log = structlog.get_logger(__name__)


FLOW_URL = "https://labs.google/fx/tools/flow?hl=en"

PROMPT_INPUT_SELECTORS = [
    'div[role="textbox"][data-slate-editor="true"]',
    'div[contenteditable="true"]',
    'textarea',
    '[aria-label*="prompt"]',
]

SUBMIT_BUTTON_SELECTORS = [
    # Universal: icon-based selector (locale-invariant, same pattern as NEW_PROJECT_SELECTORS)
    "button:has(i.google-symbols:text('arrow_forward'))",
    "button:has(i:text('arrow_forward'))",
    # Fallback: button whose visible text includes the icon ligature text
    "button:has-text('arrow_forward')",
]

# Selectors that open the per-generation settings panel (aspect ratio + count).
# The trigger button shows the CURRENT ratio icon (e.g. crop_16_9) + count (e.g. x2).
# We enumerate all possible ratio icon names so the selector is ratio-invariant.
GEN_SETTINGS_BUTTON_SELECTORS = [
    "button:has(i.google-symbols:text('crop_16_9'))",
    "button:has(i.google-symbols:text('crop_9_16'))",
    "button:has(i.google-symbols:text('crop_square'))",
    "button:has(i.google-symbols:text('crop_portrait'))",
    "button:has(i.google-symbols:text('crop_landscape'))",
]

# Map CLI --aspect-ratio values to Flow tab button text (locale-invariant number format).
ASPECT_RATIO_MAP: dict[str, str] = {
    "16:9": "16:9",
    "9:16": "9:16",
    "1:1":  "1:1",
    "4:3":  "4:3",
    "3:4":  "3:4",
}

# Map --count to the Flow tab button text.
COUNT_TAB_MAP: dict[int, str] = {1: "1x", 2: "x2", 3: "x3", 4: "x4"}


# (2026-05-12): the button wraps a Material Symbols icon (text "add_2",
# rendered as "+") followed by localized label text "New project" /
# "Novo projeto" / etc. The most robust match is the icon class, which is
# stable across locales: `i.google-symbols` is the Material Symbols span.
NEW_PROJECT_SELECTORS = [
    # Universal: any button containing the google-symbols "add_2" icon.
    "button:has(i.google-symbols:text('add_2'))",
    "button:has(i:text('add_2'))",
    # Localized text fallbacks
    "button:has-text('New project')",
    "button:has-text('Novo projeto')",
    "button:has-text('Nuevo proyecto')",
    "button:has-text('Nouveau projet')",
    "[role='button']:has-text('New project')",
    "a:has-text('New project')",
    r"button:text-matches('\+\s+\S+', 'i')",
    "[aria-label*='New project' i]",
    "[aria-label*='Project' i]",
]


async def _check_logged_in(page: Page) -> bool:
    """Authenticated if we're on a Flow URL and not on a sign-in page.

    The strict-positive variant (require visible +New project CTA) failed
    because the button uses Material Symbols ligature icons whose
    DOM text is "add_2"; not all locale renderings match our text
    selectors. URL gating + sign-in-page negation is sufficient here:
    if accounts.google.com isn't in the URL and we're on
    labs.google/<locale>/tools/flow, the profile's cookies have already
    authenticated us — the gallery is shown.
    """
    if "accounts.google.com" in page.url:
        return False
    on_flow = "labs.google" in page.url and "/flow" in page.url
    if not on_flow:
        return False
    if "/project/" in page.url:
        return True
    # Reject only if there's a top-level sign-in CTA (which exists on the
    # public landing page but not on the authenticated gallery).
    try:
        signin_button = await page.locator(
            "button:has-text('Sign in'), a:has-text('Sign in')"
        ).count()
    except Exception:  # noqa: BLE001
        signin_button = 0
    return signin_button == 0


async def _ensure_logged_in_to_flow(
    page: Page,
    out_dir: Path,
    poll_interval_s: float = 5.0,
    timeout_s: float = 600.0,
) -> None:
    """Wait for the operator to sign in inside Chromium (poll, no stdin needed)."""
    try:
        await page.goto(FLOW_URL, wait_until="networkidle", timeout=45_000)
    except Exception as e:  # noqa: BLE001
        log.warning("flow_initial_goto_failed", error=str(e))

    if await _check_logged_in(page):
        log.info("logged_in", url=page.url)
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    shot = out_dir / "auth_pending.png"
    try:
        await page.screenshot(path=str(shot), full_page=True)
    except Exception:  # noqa: BLE001
        pass
    print(
        "\n>> Not signed in to Flow yet.\n"
        f">> Current URL : {page.url}\n"
        f">> Screenshot  : {shot}\n"
        ">> Sign in inside the open Chromium window (any Flow account).\n"
        ">> Script will auto-detect when you're authenticated and continue.\n"
        f">> Polling every {poll_interval_s:.0f}s, max wait {timeout_s:.0f}s.",
        flush=True,
    )

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval_s)
        if await _check_logged_in(page):
            log.info("logged_in_detected", url=page.url)
            print(">> Detected sign-in — continuing.\n", flush=True)
            return
        if (
            "accounts.google.com" not in page.url
            and "labs.google" not in page.url
        ):
            try:
                await page.goto(FLOW_URL, wait_until="networkidle", timeout=15_000)
            except Exception:  # noqa: BLE001
                pass
        log.info("polling_for_signin", url=page.url[:80])
    raise TimeoutError(
        f"Sign-in not detected within {timeout_s:.0f}s. Last URL: {page.url}"
    )


async def _enter_editor(page: Page, out_dir: Path) -> None:
    """If on the gallery, click 'New project' and wait for /project/UUID nav."""
    if "/project/" in page.url:
        log.info("editor_already_open", url=page.url)
        return
    await page.wait_for_timeout(3000)
    for selector in NEW_PROJECT_SELECTORS:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=5000)
            log.info("clicking_new_project", selector=selector)
            await loc.click()
            try:
                await page.wait_for_url(
                    lambda url: "/project/" in url, timeout=15_000
                )
                log.info("entered_editor", url=page.url)
                return
            except Exception:
                log.warning("new_project_click_did_not_navigate", selector=selector)
        except Exception:
            continue
    out_dir.mkdir(parents=True, exist_ok=True)
    shot = out_dir / "debug_new_project.png"
    try:
        await page.screenshot(path=str(shot), full_page=True)
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError(
        f"Could not find 'New project' on Flow gallery. URL: {page.url}. "
        f"Screenshot: {shot}"
    )


async def _send_prompt(page: Page, prompt_text: str, out_dir: Path) -> None:
    """Type the prompt into the Slate editor and click submit (or press Enter)."""
    input_box = None
    for selector in PROMPT_INPUT_SELECTORS:
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=10_000)
            input_box = loc
            log.info("prompt_input_found", selector=selector)
            break
        except Exception:
            continue
    if input_box is None:
        out_dir.mkdir(parents=True, exist_ok=True)
        shot = out_dir / "debug_prompt_not_found.png"
        try:
            await page.screenshot(path=str(shot), full_page=True)
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(
            f"Prompt input not found in Flow UI. URL: {page.url}. "
            f"Screenshot: {shot}"
        )

    await input_box.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    # insertText fires a single 'beforeinput' event that Slate.js handles
    # natively — much faster than per-keystroke type() (~1.5s/char in headed Chrome).
    await page.keyboard.insert_text(prompt_text)
    await page.wait_for_timeout(600)

    for sel in SUBMIT_BUTTON_SELECTORS:
        try:
            btn = page.locator(sel).first
            await btn.wait_for(state="visible", timeout=3_000)
            await btn.click()
            log.info("prompt_submitted", via=sel)
            return
        except Exception:
            continue
    log.info("prompt_submitted", via="enter_key_fallback")
    await page.keyboard.press("Enter")


async def _capture_batch_response(page: Page, project_id: str, timeout_s: int = 120) -> dict:
    """Wait for a ``batchGenerateImages`` response scoped to *project_id*.

    Filters by project_id in the URL to avoid capturing stale responses from
    previously-visited projects still pending in the browser context.
    Removes the listener after capture or timeout to prevent accumulation.
    """
    captured: list[dict] = []
    url_marker = f"/projects/{project_id}/"

    async def on_response(response: Response) -> None:
        if "batchGenerateImages" not in response.url:
            return
        if url_marker not in response.url:
            log.debug(
                "batch_response_skipped_wrong_project",
                url=response.url,
                expected_project=project_id,
            )
            return
        try:
            body = await response.json()
            captured.append({"status": response.status, "url": response.url, "body": body})
            log.info("batch_response_captured", status=response.status, url=response.url)
        except Exception as e:  # noqa: BLE001
            log.warning("batch_response_parse_failed", error=str(e), url=response.url)

    page.on("response", on_response)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not captured:
        await asyncio.sleep(0.5)
    page.remove_listener("response", on_response)  # prevent listener accumulation
    if not captured:
        raise TimeoutError(
            f"No batchGenerateImages response for project {project_id} within {timeout_s}s. "
            "Did the Create click fire? Did reCAPTCHA fail silently?"
        )
    return captured[0]


def _extract_image_urls(response: dict) -> list[str]:
    """Pull image URLs from a batchGenerateImages response body.

    Real Flow response shape (observed 2026-05-12):
      media[].image.generatedImage.fifeUrl
    """
    body = response.get("body", {})
    urls: list[str] = []

    def _pull(media_list: list) -> None:
        for m in media_list:
            img = m.get("image", {}) or {}
            gen = img.get("generatedImage", {}) or {}
            u = (
                gen.get("fifeUrl")
                or img.get("uri")
                or img.get("downloadUrl")
                or img.get("encodedImage")
                or gen.get("encodedImage")
            )
            if u:
                urls.append(u)

    _pull(body.get("media", []))
    for req in body.get("requests", []):
        _pull(req.get("media", []))
    return urls


async def _download(urls: list[str], out_dir: Path, cookies: dict) -> list[Path]:
    """Download each URL to ``out_dir`` using the Chromium session cookies."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, cookies=cookies) as client:
        for i, url in enumerate(urls):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                # Detect actual format from Content-Type or magic bytes
                ct = resp.headers.get("content-type", "")
                ext = ".jpg" if ("jpeg" in ct or resp.content[:2] == b"\xff\xd8") else ".png"
                p = out_dir / f"image_{i:02d}{ext}"
                p.write_bytes(resp.content)
                paths.append(p)
                log.info("image_saved", path=str(p), bytes=len(resp.content), format=ext)
            except Exception as e:  # noqa: BLE001
                log.error("download_failed", url=url, error=str(e))
    return paths


async def _configure_generation_settings(
    page: Page, aspect_ratio: str | None, count: int | None
) -> None:
    """Open the per-generation settings panel and set aspect ratio and/or count.

    The trigger button shows the current ratio icon (e.g. crop_16_9) + count (e.g. x2).
    DOM confirmed 2026-05-16: aspect ratio tabs are role=tab inside a tablist,
    count tabs are role=tab with text 1x / x2 / x3 / x4.
    """
    if aspect_ratio is None and count is None:
        return

    # Open the settings panel
    opened = False
    for sel in GEN_SETTINGS_BUTTON_SELECTORS:
        try:
            btn = page.locator(sel).first
            await btn.wait_for(state="visible", timeout=3_000)
            await btn.click()
            await page.wait_for_timeout(600)
            opened = True
            log.info("gen_settings_opened", via=sel)
            break
        except Exception:
            continue
    if not opened:
        log.warning("gen_settings_panel_not_found", skipping=True)
        return

    # Set aspect ratio
    if aspect_ratio:
        ratio_text = ASPECT_RATIO_MAP.get(aspect_ratio, aspect_ratio)
        try:
            tab = page.locator(f'[role="tab"]:has-text("{ratio_text}")').first
            await tab.wait_for(state="visible", timeout=3_000)
            await tab.click()
            log.info("aspect_ratio_set", value=aspect_ratio)
        except Exception as e:
            log.warning("aspect_ratio_set_failed", value=aspect_ratio, error=str(e))

    # Set count
    if count is not None:
        count_text = COUNT_TAB_MAP.get(count)
        if count_text is None:
            log.warning("unsupported_count", value=count, supported=list(COUNT_TAB_MAP))
        else:
            try:
                tab = page.locator(f'[role="tab"]:text-is("{count_text}")').first
                await tab.wait_for(state="visible", timeout=3_000)
                await tab.click()
                log.info("count_set", value=count, tab_text=count_text)
            except Exception as e:
                log.warning("count_set_failed", value=count, error=str(e))

    # Close the panel by pressing Escape
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)


async def _drive(context: BrowserContext, prompt_text: str, out_dir: Path, expected_count: int = 2, aspect_ratio: str | None = None) -> None:
    page = context.pages[0] if context.pages else await context.new_page()

    await _ensure_logged_in_to_flow(page, out_dir)
    await _enter_editor(page, out_dir)

    for selector in PROMPT_INPUT_SELECTORS:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=30_000)
            log.info("editor_ready", selector=selector)
            break
        except Exception:
            continue

    project_id = page.url.split("/project/")[1].split("?")[0]
    # Collect responses until expected_count reached or timeout.
    responses: list[dict] = []
    all_urls: list[str] = []

    async def on_response(response: Response) -> None:
        if "batchGenerateImages" not in response.url:
            return
        if f"/projects/{project_id}/" not in response.url:
            return
        try:
            body = await response.json()
            entry = {"status": response.status, "url": response.url, "body": body}
            responses.append(entry)
            urls = _extract_image_urls(entry)
            all_urls.extend(urls)
            log.info("batch_response_captured", status=response.status, total_so_far=len(all_urls))
        except Exception as e:  # noqa: BLE001
            log.warning("batch_response_parse_failed", error=str(e))

    page.on("response", on_response)
    await _configure_generation_settings(page, aspect_ratio, expected_count)
    await _send_prompt(page, prompt_text, out_dir)

    # Wait up to 180s for all expected images to arrive.
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if len(all_urls) >= expected_count:
            break
        await asyncio.sleep(0.5)
    page.remove_listener("response", on_response)

    if not all_urls:
        raise RuntimeError(
            f"No image URLs captured for project {project_id} within 180s. "
            "Did generation fire? Check the browser window."
        )

    log.info("urls_extracted", count=len(all_urls), expected=expected_count)
    if len(all_urls) < expected_count:
        log.warning("fewer_images_than_expected", got=len(all_urls), expected=expected_count)

    cookie_list = await context.cookies("https://labs.google")
    cookies = {c.get("name", ""): c.get("value", "") for c in cookie_list if c.get("name")}
    paths = await _download(all_urls, out_dir, cookies)

    print(f"\n>> Smoke complete. {len(paths)} image(s) saved to {out_dir}")
    for p in paths:
        print(f"   - {p}")


async def run(profile_dir: Path, prompt_text: str, out_dir: Path, expected_count: int = 1, aspect_ratio: str | None = None) -> None:
    """Drive the smoke using Playwright's persistent context (Worker pattern)."""
    log.info("launching_persistent_context", profile_dir=str(profile_dir))
    async with async_playwright() as pw:
        from gflow_cli.browser_manager import channel_for_profile
        channel = channel_for_profile(profile_dir)
        
        context = await pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            channel=channel,
            ignore_default_args=["--enable-automation", "--no-sandbox"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        
        # Stealth init script
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)

        try:
            await _drive(context, prompt_text, out_dir, expected_count=expected_count, aspect_ratio=aspect_ratio)
        finally:
            await context.close()



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path.home() / "gflow-worker-style-smoke",
        help="Playwright Chromium user-data-dir "
        "(default: $HOME/gflow-worker-style-smoke)",
    )
    parser.add_argument(
        "--prompt",
        default="a calm forest at dawn, cinematic photography, 16:9",
        help="Prompt to generate",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output dir (default: tmp/smoke/<utc>)",
    )
    parser.add_argument(
        "--count", "-n",
        type=int,
        default=1,
        help="Number of images to generate and wait for (default: 1)",
    )
    parser.add_argument(
        "--aspect-ratio", "-a",
        dest="aspect_ratio",
        choices=list(ASPECT_RATIO_MAP),
        default=None,
        help="Aspect ratio (default: Flow's current setting). Options: 16:9, 9:16, 1:1, 4:3, 3:4",
    )
    args = parser.parse_args()

    out_dir = args.out or (
        Path("tmp") / "smoke" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    )
    args.profile_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(run(args.profile_dir, args.prompt, out_dir, expected_count=args.count, aspect_ratio=args.aspect_ratio))


if __name__ == "__main__":
    main()
