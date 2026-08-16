#!/usr/bin/env python3
r"""#539 — what does the "Lower Priority" video model cost?

The question is narrow: `Veo 3.1 Lite [Lower Priority]` is a registered
`VideoModel` we ship but have never observed live. Our picker selector
(`[role='menuitem']:has-text('[Lower Priority]')`) missed on both accounts, and
the earlier diagnostic captured the menu AFTER it had closed — so we could not
tell "selector stale" from "model gone".

This dumps every `[role='menuitem']` **while the dropdown is open**, then selects
the lower-priority entry by whatever its real label turns out to be and reads the
popover's live cost line (`Generating will use N credits`).

Known siblings for comparison: omni-flash 7-15 (duration-scaled), veo-lite 10,
veo-fast 20, veo-quality 100.

**Credit-free:** opens the picker and reads the DOM. No prompt, no Generate.

Usage:
    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\capture_lower_priority_model_cost.py \
        --profile denon82 --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts" / "dev"))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports import mode_control  # noqa: E402
from gflow_cli.api.transports.mode_control import CROP_SELECTORS  # noqa: E402
from gflow_cli.api.transports.ui_automation_video import MODEL_PICKER_TRIGGER  # noqa: E402

_CREDIT_RE = re.compile(r"([\d.,]+)\s*(?:cr[eé]dito?s?|credits?)", re.I)


async def _wait_editor(page: Any) -> bool:
    for _ in range(30):
        if await page.locator("button").count() > 8:
            return True
        await page.wait_for_timeout(1000)
    return False


async def _menu_open(page: Any) -> bool:
    return await page.locator("[role='menu'] [role='tab']").count() > 0


async def _open_settings(page: Any) -> bool:
    if await _menu_open(page):
        return True
    for sel in CROP_SELECTORS:
        loc = page.locator(sel).first
        if await loc.count() > 0:
            try:
                await loc.click(timeout=4000)
                await page.wait_for_timeout(600)
            except Exception:  # noqa: BLE001
                pass
            if await _menu_open(page):
                return True
    return False


async def _click_lig(page: Any, ligature: str) -> bool:
    tab = page.locator(f"[role='tab']:has(i.google-symbols:text-is('{ligature}'))").first
    if await tab.count() == 0:
        return False
    try:
        await tab.click(timeout=4000)
        await page.wait_for_timeout(700)
    except Exception:  # noqa: BLE001
        return False
    return True


async def _credits(page: Any) -> str | None:
    text = await page.evaluate(
        "() => { const m = document.querySelector(\"[role='menu']\");"
        " return ((m || document.body).textContent || '').replace(/\\s+/g,' ').trim(); }"
    )
    hit = _CREDIT_RE.search(text or "")
    return hit.group(1) if hit else None


async def _run(profile: str, project: str) -> int:
    out: dict[str, Any] = {"profile": profile, "project": project}
    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        await page.goto(
            routes.project_editor_url("en", project),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        await _wait_editor(page)
        if not await _open_settings(page):
            await mode_control.ensure_media_mode(page, allow_reload=True)
            await _wait_editor(page)
            if not await _open_settings(page):
                step("menu", "ERROR: no composer settings popover")
                return 1
        await _click_lig(page, "videocam")

        # Open the model dropdown and dump it WHILE OPEN — the earlier probe's flaw.
        trigger = page.locator(MODEL_PICKER_TRIGGER).first
        if await trigger.count() == 0:
            step("picker", "ERROR: model picker trigger not found")
            return 1
        await trigger.click(timeout=4000)
        await page.wait_for_timeout(900)

        items = await page.evaluate(
            """() => [...document.querySelectorAll("[role='menuitem']")].map((e, i) => ({
                 i,
                 text: (e.textContent || '').replace(/\\s+/g,' ').trim(),
                 aria: e.getAttribute('aria-label'),
               }))"""
        )
        out["menu_items"] = items
        step("picker", f"{len(items)} menu items visible:")
        for it in items:
            print(f"    [{it['i']}] {it['text']!r}")

        # Find the lower-priority entry by fuzzy label, not our brittle exact match.
        lp = next(
            (
                it
                for it in items
                if re.search(
                    r"low.?priority|lower priority", f"{it['text']} {it['aria'] or ''}", re.I
                )
            ),
            None,
        )
        out["lower_priority_item"] = lp
        if lp is None:
            step("verdict", "NO lower-priority entry in the picker — model appears GONE")
            out["verdict"] = "absent"
        else:
            step("verdict", f"FOUND: {lp['text']!r}")
            await page.locator("[role='menuitem']").nth(lp["i"]).click(timeout=4000)
            await page.wait_for_timeout(1200)
            await _open_settings(page)
            cost = await _credits(page)
            out["lower_priority_credits"] = cost
            step("cost", f"credit line for lower-priority = {cost}")
            out["verdict"] = "present"

    p = default_out_path("lower_priority_model_cost")
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {p}")

    print("\n=== VERDICT ===")
    print(f"  picker entry     : {out.get('verdict')}")
    item = out.get("lower_priority_item")
    if item is not None:
        print(f"  label            : {item['text']!r}")
        print(
            f"  credits          : {out.get('lower_priority_credits')}"
            "   (veo-lite=10, fast=20, quality=100)"
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    return asyncio.run(_run(args.profile, args.project))


if __name__ == "__main__":
    raise SystemExit(main())
