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
     ``C:/Users/ffrol/gflow-worker-style-smoke`` — a fresh dir,
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
        --profile-dir C:/Users/ffrol/gflow-worker-style-smoke \\
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
    'button:has(i.google-symbols:has-text("arrow_forward"))',
    'button:has-text("arrow_forward"):has-text("Create")',
    'button[aria-label*="Create"]',
]

# Selectors for the "new project" gallery CTA. From the live DOM dump
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
    labs.google/<locale>/tools/flow, denon82 cookies have already
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
    # Slate.js requires real keyboard events; .fill() bypasses onChange.
    await page.keyboard.type(prompt_text)
    await page.wait_for_timeout(500)

    for sel in SUBMIT_BUTTON_SELECTORS:
        try:
            btn = page.locator(sel).first
            await btn.wait_for(state="visible", timeout=2_000)
            await btn.click()
            log.info("prompt_submitted", via=sel)
            return
        except Exception:
            continue
    log.info("prompt_submitted", via="enter_key_fallback")
    await page.keyboard.press("Enter")


async def _capture_batch_response(page: Page, timeout_s: int = 120) -> dict:
    """Wait for the next ``batchGenerateImages`` response and return its parsed body."""
    captured: list[dict] = []

    async def on_response(response: Response) -> None:
        if "batchGenerateImages" not in response.url:
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
    if not captured:
        raise TimeoutError(
            f"No batchGenerateImages response within {timeout_s}s. "
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
                p = out_dir / f"image_{i:02d}.png"
                p.write_bytes(resp.content)
                paths.append(p)
                log.info("png_saved", path=str(p), bytes=len(resp.content))
            except Exception as e:  # noqa: BLE001
                log.error("download_failed", url=url, error=str(e))
    return paths


async def _drive(context: BrowserContext, prompt_text: str, out_dir: Path) -> None:
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

    listener_task = asyncio.create_task(_capture_batch_response(page, timeout_s=120))
    await _send_prompt(page, prompt_text, out_dir)
    response = await listener_task

    if response["status"] != 200:
        body_str = json.dumps(response["body"], indent=2)[:500]
        log.error(
            "batch_request_failed",
            status=response["status"],
            body_preview=body_str,
        )
        raise RuntimeError(f"batchGenerateImages returned {response['status']}")

    urls = _extract_image_urls(response)
    if not urls:
        body_str = json.dumps(response["body"], indent=2)[:1000]
        log.error("no_image_urls_in_response", body_preview=body_str)
        raise RuntimeError("batchGenerateImages returned 200 but no image URLs found.")

    log.info("urls_extracted", count=len(urls))
    cookie_list = await context.cookies("https://labs.google")
    cookies = {c.get("name", ""): c.get("value", "") for c in cookie_list if c.get("name")}
    paths = await _download(urls, out_dir, cookies)

    print(f"\n>> Smoke complete. {len(paths)} PNG(s) saved to {out_dir}")
    for p in paths:
        print(f"   - {p}")


async def run(profile_dir: Path, prompt_text: str, out_dir: Path) -> None:
    """Drive the smoke using Playwright's persistent context (Worker pattern)."""
    log.info("launching_persistent_context", profile_dir=str(profile_dir))
    async with async_playwright() as pw:
        # MUST be headed — reCAPTCHA Enterprise + Flow JS rely on a real
        # rendering pipeline. Same flag the Worker uses.
        context = await pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        try:
            await _drive(context, prompt_text, out_dir)
        finally:
            await context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path("C:/Users/ffrol/gflow-worker-style-smoke"),
        help="Playwright Chromium user-data-dir (default: "
        "C:/Users/ffrol/gflow-worker-style-smoke)",
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
    args = parser.parse_args()

    out_dir = args.out or (
        Path("tmp") / "smoke" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    )
    args.profile_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(run(args.profile_dir, args.prompt, out_dir))


if __name__ == "__main__":
    main()
