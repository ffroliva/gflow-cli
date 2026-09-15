r"""Can #799's Agent settings pane set model/aspect per run, and put them back? ($0)

No generation. Opens `tune`, reads every toggle group and both model pickers, opens the
VIDEO model menu and records its entries, switches video model + video aspect, clicks Save,
reloads, reads back, then restores the originals, Saves, reloads, reads back again.

    python scripts/dev/spike_agent_settings_defaults.py --profile <name> --project <id>

Pre-registered readings:

| Observation | Reading |
|---|---|
| model menu opens as `[role=menuitem]` entries | `MigratedComposer._select_model` matching carries over |
| menu entries have another role / none | record it; the matcher needs a new item anchor |
| after Save + reload the new model/aspect read back | defaults are drivable and persist server-side |
| they read back as the originals | Save did not persist (or needs something else) — directive only |
| restore reads back the originals | a run can set-then-restore without leaving the account changed |
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import (  # noqa: E402, isort: skip
    build_client,
    default_out_path,
    resolve_profile_dir,
    step,
)

SETTINGS = "button:has(mat-icon:text-is('tune'))"
PANE = "flow-settings-view"
SAVE = "flow-settings-view button.settings-save-button"
VIDEO_PICKER = "flow-settings-view button.video-model-picker-button"
IMAGE_PICKER = "flow-settings-view button.image-model-picker-button"

_STATE_JS = r"""
() => {
  const pane = document.querySelector('flow-settings-view');
  if (!pane) return null;
  const groups = [...pane.querySelectorAll('mat-button-toggle-group')].map(g => {
    const on = g.querySelector("button[role=radio][aria-checked='true']");
    return on ? (on.innerText || '').replace(/\s+/g, ' ').trim() : null;
  });
  const txt = (s) => { const b = pane.querySelector(s); return b ? (b.innerText || '').replace(/\s+/g, ' ').trim() : null; };
  const confirm = [...pane.querySelectorAll('mat-radio-button')]
    .map(r => ({ checked: r.classList.contains('mat-mdc-radio-checked'), text: (r.innerText || '').trim() }));
  return { groups, image_model: txt('button.image-model-picker-button'),
           video_model: txt('button.video-model-picker-button'), confirm };
}
"""

_OVERLAY_JS = r"""
() => [...document.querySelectorAll('.cdk-overlay-pane *')]
  .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
  .filter(e => e.getAttribute('role') || e.tagName.includes('-') || e.tagName === 'BUTTON')
  .map(e => ({ tag: e.tagName.toLowerCase(), role: e.getAttribute('role'),
               classes: [...e.classList].slice(0, 4),
               text: (e.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60) }))
"""


async def _open(page: Any) -> dict[str, Any]:
    if not await page.locator(PANE).count():
        await page.locator(SETTINGS).last.click(timeout=8_000)
        await page.locator(PANE).wait_for(state="visible", timeout=8_000)
        await page.wait_for_timeout(1_000)
    return await page.evaluate(_STATE_JS)


async def _reload(page: Any, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(8_000)


async def _pick_model(
    page: Any, picker: str, wanted: str | None, f: dict[str, Any], tag: str
) -> str:
    """Open the menu, record it, click `wanted` (or the first entry != current)."""
    button = page.locator(picker)
    current = (await button.inner_text()).replace("arrow_drop_down", "").strip()
    await button.click(timeout=5_000)
    await page.wait_for_timeout(1_200)
    f[f"{tag}_overlay"] = await page.evaluate(_OVERLAY_JS)
    items = page.locator("[role='menuitem']")
    texts = [t.strip() for t in await items.all_text_contents()]
    f[f"{tag}_menuitems"] = texts
    step(tag, f"current={current!r} menuitems={texts}")
    if not texts:
        await page.keyboard.press("Escape")
        raise RuntimeError("no [role=menuitem] in the model menu — see overlay capture")
    if wanted is None:
        idx = next(i for i, t in enumerate(texts) if current not in t and t not in current)
    else:
        idx = next(i for i, t in enumerate(texts) if wanted in t or t in wanted)
    await items.nth(idx).click(timeout=5_000)
    await page.wait_for_timeout(800)
    return current


async def _click_aspect(page: Any, group_index: int, ligature: str) -> None:
    group = page.locator(f"{PANE} mat-button-toggle-group").nth(group_index)
    radio = group.locator(f"button[role='radio']:has(mat-icon:text-is('{ligature}'))")
    await radio.click(timeout=5_000)
    await page.wait_for_timeout(400)


async def _save(page: Any, f: dict[str, Any], tag: str) -> None:
    await page.locator(SAVE).click(timeout=5_000)
    await page.wait_for_timeout(2_500)
    f[f"{tag}_pane_after_save"] = await page.locator(PANE).count()
    step(tag, f"saved; pane still open={f[f'{tag}_pane_after_save']}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    url = f"https://flow.google.com/project/{args.project}"
    f: dict[str, Any] = {"question": "#799 agent settings: set, persist, restore"}

    async with build_client(resolve_profile_dir(args.profile)) as client:
        page = await client._context.new_page()  # noqa: SLF001 - spike reads the live context
        await _reload(page, url)
        f["original"] = original = await _open(page)
        step("original", json.dumps(original))
        # Toggle groups in DOM order: image aspect, image count, video aspect, video count.
        video_aspect = original["groups"][2] or ""
        new_lig = "crop_9_16" if "16:9" in video_aspect else "crop_16_9"
        old_lig = "crop_16_9" if new_lig == "crop_9_16" else "crop_9_16"
        changed = False
        try:
            old_model = await _pick_model(page, VIDEO_PICKER, None, f, "switch")
            await _click_aspect(page, 2, new_lig)
            f["before_save"] = await page.evaluate(_STATE_JS)
            step("before_save", json.dumps(f["before_save"]))
            await _save(page, f, "switch")
            changed = True
            await _reload(page, url)
            f["after_reload"] = await _open(page)
            step("after_reload", json.dumps(f["after_reload"]))
        finally:
            if changed:
                await _open(page)
                await _pick_model(page, VIDEO_PICKER, old_model, f, "restore")
                await _click_aspect(page, 2, old_lig)
                await _save(page, f, "restore")
                await _reload(page, url)
                f["restored"] = await _open(page)
                step("restored", json.dumps(f["restored"]))
            out = default_out_path("spike_agent_settings_defaults")
            out.write_text(json.dumps(f, indent=2, ensure_ascii=False), encoding="utf-8")
            step("done", f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
