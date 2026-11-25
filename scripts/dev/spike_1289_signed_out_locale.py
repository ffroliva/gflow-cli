"""#1289 — signed-out vs signed-in Flow landings across locales ($0, read-only).

Cost: $0 (read-only navigation and DOM inspection; nothing typed or submitted).

Pre-registered outcome readings:
- ServiceLogin anchor + <flow-landing-page> 1/1 across en-US, pt-BR, zh-CN when signed out,
  and 0/1 when signed in -> safe Tier-1 locale-invariant sign-in anchors.
- Signed-out landing stays on labs.google/fx/tools/flow or lands elsewhere -> record actual URL
  and anchors before changing `_check_logged_in`.
- 0/N reproduction or mixed -> treat as unmeasured / flaking; do not guess.

Usage:
    python scripts/dev/spike_1289_signed_out_locale.py
    python scripts/dev/spike_1289_signed_out_locale.py --profile <name>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, resolve_profile_dir  # noqa: E402, isort: skip
from gflow_cli.api.transports._common import (  # noqa: E402
    flow_host_kind,
    flow_landing_kind,
    safe_page_url,
)
from gflow_cli.api.transports._ui_session import (  # noqa: E402
    _SIGNIN_CTA_SELECTOR,
    UiSessionMixin,
)
from gflow_cli.config import get_settings  # noqa: E402

SIGNED_OUT_PROBES: tuple[tuple[str, str, str], ...] = (
    ("en-US", "en-US,en;q=0.9", "https://flow.google.com/"),
    ("pt-BR", "pt-BR,pt;q=0.9", "https://flow.google.com/?hl=pt-BR"),
    ("zh-CN", "zh-CN,zh;q=0.9", "https://flow.google.com/?hl=zh-CN"),
    ("pt-BR", "pt-BR,pt;q=0.9", "https://labs.google/fx/tools/flow?hl=pt-BR"),
    ("pt-BR", "pt-BR,pt;q=0.9", "https://labs.google/fx/pt/tools/flow"),
)

_DOM_PROBE_JS = """() => ({
  lang: document.documentElement.lang,
  serviceLoginCount: document.querySelectorAll(
    "a[href^='https://accounts.google.com/ServiceLogin']"
  ).length,
  signOutOptionsCount: document.querySelectorAll(
    "a[href^='https://accounts.google.com/SignOutOptions']"
  ).length,
  landingPageCount: document.querySelectorAll("flow-landing-page").length,
})"""


async def _probe_signed_out(proxy: str | None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    launch_kwargs: dict[str, Any] = {"channel": "chrome", "headless": True}
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**launch_kwargs)
        try:
            for locale, accept_lang, url in SIGNED_OUT_PROBES:
                ctx = await browser.new_context(
                    locale=locale,
                    extra_http_headers={"Accept-Language": accept_lang},
                    viewport={"width": 1280, "height": 800},
                )
                page = await ctx.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                    await page.locator(_SIGNIN_CTA_SELECTOR).first.wait_for(
                        state="attached", timeout=15_000
                    )
                    await page.wait_for_timeout(1_000)
                    dom = await page.evaluate(_DOM_PROBE_JS)
                    results.append(
                        {
                            "mode": "signed_out",
                            "start": url,
                            "locale": locale,
                            "final_url": safe_page_url(page.url),
                            "host": flow_host_kind(page.url),
                            "landing": flow_landing_kind(page.url),
                            "check_logged_in": await UiSessionMixin._check_logged_in(page),  # noqa: SLF001
                            **dom,
                        }
                    )
                finally:
                    await ctx.close()
        finally:
            await browser.close()
    return results


async def _probe_signed_in(profile: str) -> list[dict[str, Any]]:
    profile_dir = resolve_profile_dir(profile)
    results: list[dict[str, Any]] = []
    async with build_client(profile_dir) as client:
        page = client._page  # noqa: SLF001
        assert page is not None
        for locale, accept_lang, url in (
            ("en-US", "en-US,en;q=0.9", "https://flow.google.com/"),
            ("pt-BR", "pt-BR,pt;q=0.9", "https://flow.google.com/?hl=pt-BR"),
        ):
            await page.set_extra_http_headers({"Accept-Language": accept_lang})
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await page.locator("aisandbox-root").first.wait_for(state="attached", timeout=15_000)
            await page.wait_for_timeout(3_000)
            dom = await page.evaluate(_DOM_PROBE_JS)
            results.append(
                {
                    "mode": "signed_in",
                    "start": url,
                    "locale": locale,
                    "final_url": safe_page_url(page.url),
                    "host": flow_host_kind(page.url),
                    "landing": flow_landing_kind(page.url),
                    "check_logged_in": await UiSessionMixin._check_logged_in(page),  # noqa: SLF001
                    **dom,
                }
            )
    return results


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="", help="Optional signed-in profile for control pass")
    parser.add_argument("--proxy", default=None, help="Optional Chrome proxy server URL")
    args = parser.parse_args()

    proxy = args.proxy if args.proxy is not None else get_settings().browser_proxy
    rows = await _probe_signed_out(proxy)
    if args.profile:
        rows.extend(await _probe_signed_in(args.profile))
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
