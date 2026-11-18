#!/usr/bin/env python3
r"""Issue #314 recon — does the image i2i reference picker's search match an
uploaded file's FILENAME? (0 credits — navigation + picker only.)

The "select existing asset instead of re-upload" machinery already exists
(_select_existing_asset, keyed on media UUID). #314 wants a FILENAME-keyed
variant so dedup works without trusting UUIDs across runs. The load-bearing
unknown: does typing a filename into the picker search (#add-menu-input)
filter to the matching library asset, and what do result tiles expose?

This spike opens the Add-media picker in image mode, dumps the first tiles'
structure (thumbnail URL → UUID, plus any name/alt/title/aria text), then
types a token derived from the first tile's own visible text and checks
whether the tile count drops (search filters by that displayed name). No
prompt is submitted, no generate route is hit.

Usage (headed, supervised):
    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_issue314_picker_filename_search.py \
        --profile ffroliva --project <project-id> [--term son]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports.ui_automation import UiAutomationTransport  # noqa: E402
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    ADD_MEDIA_BUTTON,
    PICKER_SEARCH_INPUT,
)

_ADD_MEDIA_FALLBACK = "button:has(i.google-symbols:text-is('add_2'))"
_TILE = "[role='option']"

_DUMP_JS = r"""
() => {
  const tiles = Array.from(document.querySelectorAll("[role='option']")).slice(0, 8);
  return tiles.map(t => {
    const img = t.querySelector('img');
    return {
      text: (t.innerText || '').trim().slice(0, 120),
      ariaLabel: t.getAttribute('aria-label'),
      title: t.getAttribute('title'),
      dataTileId: t.getAttribute('data-tile-id'),
      imgSrc: img ? img.getAttribute('src') : null,
      imgAlt: img ? img.getAttribute('alt') : null,
      imgTitle: img ? img.getAttribute('title') : null,
    };
  });
}
"""


async def _open_picker(page: Any) -> None:
    add = page.locator(ADD_MEDIA_BUTTON).first
    if await add.count() == 0:
        add = page.locator(_ADD_MEDIA_FALLBACK).first
    await add.wait_for(state="visible", timeout=8000)
    await add.click()
    await page.wait_for_timeout(1000)


async def _enter_image_mode(page: Any) -> bool:
    try:
        await UiAutomationTransport._switch_to_image_mode(page)  # noqa: SLF001
        return True
    except Exception:  # noqa: BLE001
        return False


async def _run(profile: str, project: str, term: str | None) -> dict[str, Any]:
    out_dir = default_out_path("issue314_picker", ".json").parent
    result: dict[str, Any] = {"profile": profile, "project": project}
    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001  # spike: drive the persistent context's page
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        url = routes.project_editor_url("en", project)
        step("A", f"goto {url}", prefix="314")
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(2500)

        step("B", "enter image mode", prefix="314")
        result["image_mode_entered"] = await _enter_image_mode(page)

        step("C", "open Add-media picker", prefix="314")
        await _open_picker(page)
        await page.screenshot(path=str(out_dir / "issue314_picker_open.png"))

        step("D", "dump first tiles (structure + what they expose)", prefix="314")
        tiles_before = await page.evaluate(_DUMP_JS)
        result["tiles_before"] = tiles_before
        result["tile_count_before"] = await page.locator(_TILE).count()

        # Derive a search token: explicit --term, else the first tile's first word.
        token = term
        if not token and tiles_before:
            txt = (tiles_before[0].get("text") or tiles_before[0].get("ariaLabel") or "").strip()
            token = txt.split()[0] if txt else None
        result["search_token"] = token

        if token:
            step("E", f"type {token!r} into picker search #add-menu-input", prefix="314")
            search = page.locator(PICKER_SEARCH_INPUT).first
            if await search.count() == 0:
                result["search_input_present"] = False
            else:
                result["search_input_present"] = True
                await search.click()
                await search.press_sequentially(token, delay=30)
                await page.wait_for_timeout(1500)
                result["tile_count_after"] = await page.locator(_TILE).count()
                result["tiles_after"] = await page.evaluate(_DUMP_JS)
                await page.screenshot(path=str(out_dir / "issue314_picker_searched.png"))

        out_file = out_dir / "issue314_picker_recon.json"
        out_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
        result["_out_file"] = str(out_file)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--term", default=None, help="explicit search token (else first tile's first word)")
    args = ap.parse_args(argv)
    res = asyncio.run(_run(args.profile, args.project, args.term))
    print(json.dumps({k: v for k, v in res.items() if not k.startswith("tiles")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
