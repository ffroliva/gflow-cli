"""Smoke — real-Chrome via CDP + Flow UI -> PNG.

Validates the persistent-browser path (Phase D.2.3) end-to-end against real
Flow WITHOUT going through FlowApiClient or the strategy abstraction. Uses
BrowserManager to spawn (or attach to) the user's system Chrome with
``--remote-debugging-port=9222``, then drives Flow's UI to generate one
image. The selectors are pinned copies of CG Worker's
``flow_logic.py`` (DO NOT cross-import from the monorepo; this is a
sample copy with a revision-cite below).

Selectors copied from
``C:/development/github/compiled-growth/compile-growth-monorepo/python/workers/google-flow-worker/flow_logic.py``
on 2026-05-12. Re-check if Flow's DOM changes.

First-run UX:
  1. Script spawns Chrome against the working profile dir (default:
     ``C:/Users/ffrol/gflow-cdp-smoke``).
  2. If not logged in to Flow, script pauses with a prompt asking the
     operator to sign in manually inside the opened Chrome window.
  3. Once authenticated, script types the prompt, clicks Generate, waits
     for ``batchGenerateImages`` to fire, and downloads each PNG to
     ``tmp/smoke/<utc>/``.

Subsequent runs reuse the same profile dir (cookies persist) and
re-attach to Chrome if it's still running.

Usage::

    uv run python scripts/smoke_real_chrome_image.py \\
        --profile-dir C:/Users/ffrol/gflow-cdp-smoke \\
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
from playwright.async_api import Page, Response

from gflow_cli.browser_manager import get_or_launch_browser
from gflow_cli.errors import AuthMissingError, ConfigurationError

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

# Copied from CG Worker flow_logic.py @ 2026-05-12. DO NOT cross-import.
NEW_PROJECT_SELECTORS = [
    r"button:text-matches('^\s*\+\s+\S+', 'i')",
    r"[role='button']:text-matches('^\s*\+\s+\S+', 'i')",
    r"a:text-matches('^\s*\+\s+\S+', 'i')",
    "button:has-text('New project')",
    "[role='button']:has-text('New project')",
    "a:has-text('New project')",
    ":text('New project')",
    "[aria-label*='New project' i]",
]


async def _ensure_logged_in_to_flow(page: Page, out_dir: Path) -> None:
    """Robust auth check — pause for manual sign-in if not authenticated."""
    while True:
        try:
            await page.goto(FLOW_URL, wait_until="networkidle", timeout=45_000)
        except Exception as e:  # noqa: BLE001
            log.warning("flow_goto_failed_retrying", error=str(e))
            await page.wait_for_timeout(2000)
            continue

        # Authenticated indicators
        on_accounts = "accounts.google.com" in page.url
        signin_button = await page.locator(
            "button:has-text('Sign in'), a:has-text('Sign in')"
        ).count()
        # Positive auth signal: the gallery page renders the "+ New project" CTA
        # or any /project/<uuid> URL (already in editor).
        in_project = "/project/" in page.url
        new_project_visible = 0
        if not on_accounts and signin_button == 0 and not in_project:
            try:
                new_project_visible = await page.locator(
                    NEW_PROJECT_SELECTORS[0]
                ).count()
            except Exception:  # noqa: BLE001
                new_project_visible = 0

        if (not on_accounts and signin_button == 0 and (in_project or new_project_visible > 0)):
            log.info("logged_in", url=page.url, in_project=in_project)
            return

        out_dir.mkdir(parents=True, exist_ok=True)
        shot = out_dir / "auth_pending.png"
        try:
            await page.screenshot(path=str(shot), full_page=True)
        except Exception:  # noqa: BLE001
            pass
        print(
            "\n>> Not signed in to Flow in this Chrome window.\n"
            f">> URL: {page.url}\n"
            f">> Screenshot: {shot}\n"
            ">> Sign in manually inside the open Chrome window,\n"
            ">> then press Enter to continue (Ctrl+C to abort).",
            flush=True,
        )
        input()


async def _enter_editor(page: Page, out_dir: Path) -> None:
    """If on the gallery, click 'New project' and wait for /project/UUID nav."""
    if "/project/" in page.url:
        log.info("editor_already_open", url=page.url)
        return
    await page.wait_for_timeout(3000)  # gallery stabilisation per CG Worker
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


async def _capture_batch_response(page: Page, timeout_s: int = 90) -> dict:
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
            "Did the Generate click fire? Did reCAPTCHA fail silently?"
        )
    return captured[0]


def _extract_image_urls(response: dict) -> list[str]:
    """Pull image URLs from a batchGenerateImages response body."""
    body = response.get("body", {})
    urls: list[str] = []
    # Per HAR samples: top-level `media` (singular result) or per-request media[]
    medias = body.get("media", [])
    for m in medias:
        img = m.get("image", {})
        u = img.get("uri") or img.get("downloadUrl") or img.get("encodedImage")
        if u:
            urls.append(u)
    # Also try nested .requests[].media[]
    for req in body.get("requests", []):
        for m in req.get("media", []):
            img = m.get("image", {})
            u = img.get("uri") or img.get("downloadUrl") or img.get("encodedImage")
            if u:
                urls.append(u)
    return urls


async def _download(urls: list[str], out_dir: Path, cookies: dict) -> list[Path]:
    """Download each URL to ``out_dir`` using the Chrome session cookies."""
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


async def run(profile_dir: Path, port: int, prompt_text: str, out_dir: Path) -> None:
    """Drive the smoke."""
    try:
        context = await get_or_launch_browser(profile_dir, port=port)
    except AuthMissingError:
        log.warning("logged_in_check_failed_first_pass_will_retry_interactively")
        context = await get_or_launch_browser(profile_dir, port=port)
    except ConfigurationError as e:
        log.error("configuration_error", error=str(e))
        raise

    page = context.pages[0] if context.pages else await context.new_page()

    await _ensure_logged_in_to_flow(page, out_dir)
    await _enter_editor(page, out_dir)

    # Wait for the editor's prompt input to render
    for selector in PROMPT_INPUT_SELECTORS:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=30_000)
            log.info("editor_ready", selector=selector)
            break
        except Exception:
            continue

    # Set up response listener BEFORE clicking submit
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
    cookies = {c["name"]: c["value"] for c in cookie_list}
    paths = await _download(urls, out_dir, cookies)

    print(f"\n>> Smoke complete. {len(paths)} PNG(s) saved to {out_dir}")
    for p in paths:
        print(f"   - {p}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path("C:/Users/ffrol/gflow-cdp-smoke"),
        help="Chrome user-data-dir (default: C:/Users/ffrol/gflow-cdp-smoke)",
    )
    parser.add_argument("--port", type=int, default=9222, help="CDP port (default: 9222)")
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
    asyncio.run(run(args.profile_dir, args.port, args.prompt, out_dir))


if __name__ == "__main__":
    main()
