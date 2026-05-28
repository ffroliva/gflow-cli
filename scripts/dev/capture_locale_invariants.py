"""Capture Flow DOM attributes across locales to identify stable selectors.

Environment:
  GFLOW_CLI_E2E_PROFILE: authenticated Chrome-strategy profile name.
  GFLOW_CLI_LOCALES: optional comma-separated BCP 47 tags; defaults to
    ``en-US,pt-BR,es-ES``.

Run:
  uv run python scripts/dev/capture_locale_invariants.py
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from gflow_cli.auth import profile_dir as resolve_auth_profile_dir
from gflow_cli.browser_manager import channel_for_profile

DEFAULT_LOCALES = ("en-US", "pt-BR", "es-ES")
FLOW_URL = "https://labs.google/fx/tools/flow"
LOCALES_ENV = "GFLOW_CLI_LOCALES"
PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"
OUTPUT_PATH = Path("tmp/locale_discovery.json")

ElementMeta = dict[str, str | None]
LocaleCapture = dict[str, list[ElementMeta]]
CaptureResults = dict[str, LocaleCapture | None]
Summary = dict[str, list[str]]


def _parse_locales(raw: str | None) -> list[str]:
    """Parse ``GFLOW_CLI_LOCALES`` while keeping a useful default."""
    if raw is None or not raw.strip():
        return list(DEFAULT_LOCALES)
    locales = [part.strip() for part in raw.split(",") if part.strip()]
    return locales or list(DEFAULT_LOCALES)


def _flow_url(locale: str) -> str:
    """Build the Flow URL without truncating BCP 47 locale tags."""
    return f"{FLOW_URL}?hl={locale}"


def _resolve_profile_dir(
    env: Mapping[str, str] | None = None,
    *,
    resolver: Callable[[str], Path] = resolve_auth_profile_dir,
) -> Path:
    """Resolve the authenticated e2e profile or exit with setup instructions."""
    environ = os.environ if env is None else env
    profile_name = environ.get(PROFILE_ENV, "").strip()
    if not profile_name:
        raise SystemExit(
            f"{PROFILE_ENV} is required. Run `gflow auth login --browser chrome "
            "--profile <name>` first, then set "
            f"`{PROFILE_ENV}=<name>` and re-run this script."
        )

    candidate = resolver(profile_name)
    if not candidate.exists():
        raise SystemExit(
            f"Profile directory not found: {candidate}. "
            f"Run `gflow auth login --browser chrome --profile {profile_name}` "
            "to create it."
        )
    return candidate


def _launch_options(
    locale: str,
    profile_dir: Path,
    *,
    channel_resolver: Callable[[Path], str | None] = channel_for_profile,
) -> dict[str, Any]:
    """Return Playwright persistent-context launch options for a locale probe."""
    return {
        "headless": False,
        "locale": locale,
        "channel": channel_resolver(profile_dir),
        "args": ["--disable-blink-features=AutomationControlled"],
    }


def _id_suffix(raw_id: str | None) -> str | None:
    if not raw_id:
        return None
    for separator in ("-", "_", ":"):
        if separator in raw_id:
            return raw_id.rsplit(separator, 1)[-1]
    return raw_id


def _iter_element_meta(capture: LocaleCapture) -> list[ElementMeta]:
    return [
        element
        for group in ("tabs", "buttons", "menuitems", "textboxes")
        for element in capture.get(group, [])
    ]


def _stable_values_by_locale(
    results: CaptureResults,
    extractor: Callable[[ElementMeta], str | None],
) -> list[str]:
    locale_value_sets = [
        {value for element in _iter_element_meta(capture) if (value := extractor(element))}
        for capture in results.values()
        if capture is not None
    ]
    if not locale_value_sets:
        return []
    return sorted(set.intersection(*locale_value_sets))


def _summarize(results: CaptureResults) -> Summary:
    """Return selector candidates that appear in every successful locale capture."""
    return {
        "locales": sorted(locale for locale, capture in results.items() if capture is not None),
        "failed_locales": sorted(locale for locale, capture in results.items() if capture is None),
        "stable_id_suffixes": _stable_values_by_locale(
            results,
            lambda element: _id_suffix(element.get("id")),
        ),
        "stable_aria_labels": _stable_values_by_locale(
            results,
            lambda element: element.get("aria_label"),
        ),
        "stable_icon_ligatures": _stable_values_by_locale(
            results,
            lambda element: element.get("icon_ligature"),
        ),
    }


async def capture_locale(locale: str, profile_dir: Path) -> LocaleCapture | None:
    """Capture Flow DOM metadata for one locale."""
    print(f"Capturing locale: {locale}")
    async with async_playwright() as playwright:
        context = None
        for attempt in range(1, 4):
            try:
                context = await playwright.chromium.launch_persistent_context(
                    str(profile_dir),
                    **_launch_options(locale, profile_dir),
                )
                break
            except PlaywrightError as exc:
                print(f"Attempt {attempt} failed to launch browser: {exc}")
                await asyncio.sleep(2)

        if context is None:
            print(f"Failed to launch browser for {locale} after 3 attempts.")
            return None

        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(_flow_url(locale))
            print("Waiting for editor...")
            await page.wait_for_selector('div[role="textbox"]', timeout=60_000)
            return await page.evaluate(
                """() => {
                    const iconSelector = [
                        'mat-icon',
                        '.google-symbols',
                        '[class*="material-symbols"]'
                    ].join(',');

                    const cleaned = (value) => {
                        const text = value?.trim();
                        return text ? text : null;
                    };

                    const iconLigature = (el) => {
                        const icon = el.matches(iconSelector)
                            ? el
                            : el.querySelector(iconSelector);
                        return cleaned(icon?.textContent);
                    };

                    const getMeta = (el) => ({
                        tag: el.tagName,
                        text: cleaned(el.innerText)?.slice(0, 80) ?? null,
                        aria_label: el.getAttribute('aria-label'),
                        aria_controls: el.getAttribute('aria-controls'),
                        role: el.getAttribute('role'),
                        data_testid: el.getAttribute('data-testid'),
                        id: cleaned(el.id),
                        icon_ligature: iconLigature(el),
                    });

                    return {
                        tabs: Array.from(document.querySelectorAll('[role="tab"]')).map(getMeta),
                        buttons: Array.from(document.querySelectorAll('button')).map(getMeta),
                        menuitems: Array.from(
                            document.querySelectorAll('[role="menuitem"]')
                        ).map(getMeta),
                        textboxes: Array.from(
                            document.querySelectorAll('[role="textbox"]')
                        ).map(getMeta),
                    };
                }"""
            )
        except PlaywrightTimeoutError:
            print(f"Timeout waiting for editor in {locale}. Check auth.")
            return None
        finally:
            await context.close()


async def main() -> int:
    profile_dir = _resolve_profile_dir()
    locales = _parse_locales(os.environ.get(LOCALES_ENV))
    results: CaptureResults = {}
    for locale in locales:
        results[locale] = await capture_locale(locale, profile_dir)

    payload = {"captures": results, "summary": _summarize(results)}
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Results saved to {OUTPUT_PATH}")
    print(json.dumps(payload["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
