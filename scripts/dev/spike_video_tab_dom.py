"""What does Flow's mode dropdown ACTUALLY render? (#539, video-tab drift)

`spike_model_inventory.py` could not reach video mode on 2026-08-26: all five
`VIDEO_TAB_IN_MENU_SELECTORS` missed on an `html_lang=en` account, while the
image tab matched via `aria-controls*='IMAGE'`. Note the asymmetry — image uses
a CONTAINS match, video an ENDS-WITH (`$='-content-VIDEO'`). This dumps every
`[role='tab']` with its id / aria-controls / text so the miss is diagnosed from
evidence rather than from a guess about which trigger is which.

Navigation and menu clicks only. Never submits. Zero credits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spike_common import build_client, resolve_profile_dir  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    MODE_SWITCH_TRIGGER_SELECTORS,
)

_DUMP_TABS = r"""
() => ({
  tabs: Array.from(document.querySelectorAll("[role='tab']")).map(e => ({
    id: e.id || null,
    ariaControls: e.getAttribute('aria-controls'),
    ariaSelected: e.getAttribute('aria-selected'),
    text: (e.innerText || '').replace(/\s+/g, ' ').trim(),
    visible: !!(e.offsetParent || e.getClientRects().length),
  })),
  menus: document.querySelectorAll("[role='menu']").length,
  menuitems: Array.from(document.querySelectorAll("[role='menuitem']"))
      .map(e => (e.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean),
  dropdowns: Array.from(document.querySelectorAll("button[aria-haspopup='menu']")).map(e => ({
    text: (e.innerText || '').replace(/\s+/g, ' ').trim(),
    expanded: e.getAttribute('aria-expanded'),
    icons: Array.from(e.querySelectorAll('i')).map(i => (i.textContent || '').trim()),
    visible: !!(e.offsetParent || e.getClientRects().length),
  })),
})
"""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()

    out: dict[str, Any] = {}
    async with build_client(resolve_profile_dir(args.profile)) as client:
        t = client.transport
        page = client._page
        await t._enter_editor(page, None, project_id=args.project)
        out["html_lang"] = await page.evaluate("document.documentElement.lang")

        out["before_open"] = await page.evaluate(_DUMP_TABS)

        opened = None
        for sel in MODE_SWITCH_TRIGGER_SELECTORS:
            if await page.locator(sel).count() > 0:
                await page.locator(sel).first.click(timeout=6000)
                opened = sel
                break
        out["mode_trigger_used"] = opened
        await page.wait_for_timeout(1200)
        out["after_open"] = await page.evaluate(_DUMP_TABS)

        # Now go to VIDEO mode and dump the settings panel's dropdowns, so the
        # model picker is identified by what it renders, not by an index guess.
        await t._switch_to_video_mode(page, out_dir=None)
        await t._open_gen_settings_panel(page)
        await page.wait_for_timeout(800)
        out["video_panel"] = await page.evaluate(_DUMP_TABS)

    print(f"html_lang={out['html_lang']}  trigger={out['mode_trigger_used']}")
    for stage in ("before_open", "after_open"):
        blk = out[stage]
        print(f"\n=== {stage} === menus={blk['menus']} dropdowns={blk['dropdowns']}")
        for tb in blk["tabs"]:
            print(
                f"  id={tb['id']!r} aria-controls={tb['ariaControls']!r} "
                f"selected={tb['ariaSelected']} visible={tb['visible']} text={tb['text']!r}"
            )

    dest = Path("scripts/dev/_spike_out/spike_video_tab_dom.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nevidence: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
