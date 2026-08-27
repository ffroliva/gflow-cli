"""What models does Flow ACTUALLY offer, and what does each cost? (#539, #586)

Two questions this settles with evidence:

1. **Model inventory.** Which entries does the picker render? Our image selectors
   are text-only brand names (`ui_automation.py:101`) with no locale-invariant
   fallback, and `VEO_3_1_LITE_LOWER_PRIORITY` (#539) has *never* been seen live —
   its selector missed on both accounts, and the previous capture came back empty.

2. **Cost, credit-free.** Flow's settings popover renders a live cost line that
   updates with the selected model. If "[Lower Priority]" reads 0 — or materially
   below veo-lite's 10 — that answers #539 at zero credits.

**The trap this avoids, twice documented.** #539 records that the earlier capture
"came back empty — taken *after* the menu closed → inconclusive, not proof of
absence". I independently hit the identical failure an hour ago. So this reads the
menu **while it is open** and treats an empty read as INSTRUMENT FAILURE, never as
"the model is gone".

The credit regex is deliberately locale-aware: an English-only /credits/ silently
returns null on a pt-BR UI and reads as "no cost shown".

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
from gflow_cli.api.transports.ui_automation import (  # noqa: E402
    IMAGE_MODEL_PICKER_TRIGGER,
)

# Locale-aware: pt "créditos"/"creditos", es "créditos", en "credits".
_READ_MENU = """
() => {
  const items = Array.from(document.querySelectorAll("[role='menuitem']"))
      .map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim())
      .filter(Boolean);
  const body = document.body ? document.body.innerText : '';
  const m = body.match(/([\\d.,]+)\\s*(?:cr[e\\u00e9]dito?s?|credits?)/i);
  return { items, credits: m ? m[1] : null, item_count: items.length };
}
"""


async def open_and_read(page: Any, label: str, *, trigger_idx: int = 0) -> dict[str, Any]:
    """Click the model picker and read the menu WHILE IT IS OPEN.

    ``trigger_idx`` exists because the trigger selector is an `arrow_drop_down`
    button, and the VIDEO settings panel renders several of them (model,
    duration, aspect...). `.first` is therefore not necessarily the model picker
    — the recorded "video picker uses a different trigger" note was a guess; the
    two module constants are byte-identical strings.
    """
    n = await page.locator(IMAGE_MODEL_PICKER_TRIGGER).count()
    try:
        await page.locator(IMAGE_MODEL_PICKER_TRIGGER).nth(trigger_idx).click(timeout=6000)
    except Exception as exc:  # noqa: BLE001
        return {
            "stage": label,
            "trigger_count": n,
            "instrument_error": f"trigger: {type(exc).__name__}",
        }
    await page.wait_for_timeout(1200)
    snap = await page.evaluate(_READ_MENU)
    snap["stage"] = label
    if snap["item_count"] == 0:
        # Never report this as "no models" — #539's earlier capture made exactly
        # that mistake and the conclusion was wrong.
        snap["instrument_error"] = "menu read empty — menu was not open; INCONCLUSIVE"
    snap["trigger_count"] = n
    snap["trigger_idx"] = trigger_idx
    return snap


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--locale", default=None, help="force a locale segment (default: account)")
    args = ap.parse_args()

    out: dict[str, Any] = {}
    async with build_client(resolve_profile_dir(args.profile)) as client:
        t = client.transport
        page = client._page
        if args.locale:
            t._account_locale = args.locale
        out["locale_used"] = t._account_locale

        await t._enter_editor(page, None, project_id=args.project)
        out["html_lang"] = await page.evaluate("document.documentElement.lang")

        # --- IMAGE ---
        await t._switch_to_image_mode(page)
        await t._open_gen_settings_panel(page)
        out["image"] = await open_and_read(page, "image")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)

        # --- VIDEO ---
        # `_switch_to_video_mode` leaves the settings menu OPEN by contract (its
        # docstring: "The menu stays open afterward so the caller can also set
        # aspect + count"), unlike `_switch_to_image_mode`, which closes it with
        # Escape. So do NOT call `_open_gen_settings_panel` here: the panel and
        # the mode dropdown are the SAME crop_* trigger button, and clicking it
        # again TOGGLES the panel shut. The video tab probe then misses on all
        # five selectors and the capture reads empty.
        #
        # That is the instrument failure #539 recorded as "the video picker uses
        # a different trigger". Falsified 2026-08-26: MODEL_PICKER_TRIGGER and
        # IMAGE_MODEL_PICKER_TRIGGER are byte-identical strings, and a DOM dump
        # (spike_video_tab_dom.py) showed `-trigger-VIDEO` present and visible
        # throughout. Re-entering the editor gives a known-closed starting state.
        await t._enter_editor(page, None, project_id=args.project)
        try:
            await t._switch_to_video_mode(page, out_dir=None)
            out["video"] = await open_and_read(page, "video")
            await page.keyboard.press("Escape")
        except Exception as exc:  # noqa: BLE001
            out["video"] = {
                "stage": "video",
                "instrument_error": f"{type(exc).__name__}: {exc!s}"[:140],
            }

    print(f"\nlocale={out['locale_used']}  html_lang={out.get('html_lang')}")
    for stage in ("image", "video"):
        blk = out.get(stage) or {}
        print(f"\n=== {stage.upper()} MODEL MENU ===")
        if blk.get("instrument_error"):
            print(f"   INSTRUMENT ERROR: {blk['instrument_error']}")
        print(f"   items ({blk.get('item_count')}):")
        for it in blk.get("items", []):
            print(f"     - {it}")
        print(f"   live credit line: {blk.get('credits')}")
        lower = [i for i in blk.get("items", []) if "lower priority" in i.lower()]
        if lower:
            print(f"   >>> LOWER-PRIORITY ENTRY FOUND: {lower}")

    dest = Path("scripts/dev/_spike_out/spike_model_inventory.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nevidence: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
