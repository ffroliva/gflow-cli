r"""#1334 — why is the Extend menu item disabled, and what does the page say about it? ($0)

The clip editor's add-clip menu renders the Extend item (``keyboard_double_arrow_right``)
with ``disabled="true"`` on a low-balance account; the same item is clickable on a funded
one. This spike opens the editor, opens the menu, and records -- without clicking any menu
item -- every item's disabled state, its recorded labels/tooltips, what hovering the
Extend item shows, and whether the submit-path credits warning is visible.

COST: zero. Every generation/extend submit rpc is aborted at the context route; no menu
item is ever clicked.

    uv run --no-sync python scripts/dev/spike_extend_disabled_menu.py \
        --profile <name> --project <pid> --media <media id>

Pre-registered readings (written 2026-11-18, before the first run):

C1  Extend item disabled on the low-balance profile, enabled on the funded one, with every
    other menu item in the same state on both -> the item's state tracks the account.
C2  A tooltip / aria text on hover that names credits -> the page states the cause.
    No such text -> the cause is inferred from the balance read, not stated by the page.
C3  ``button.prompt-warning-button`` visible in the editor -> the submit-path credits
    check already sees the shortfall here; absent -> it cannot be reused as-is.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
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

from gflow_cli.api.transports._mc_common import CREDITS_WARNING  # noqa: E402
from gflow_cli.api.transports.migrated_extend import (  # noqa: E402
    ADD_CLIP_BUTTON,
    EXTEND_MENU_ITEM,
)
from gflow_cli.api.transports.migrated_recover import MIGRATED_CLIP_URL  # noqa: E402

ALWAYS_ABORT = ("fZytfe", "YhhmEf", "eb1hJf", "MZZa6b", "nprQif", "rqZuUc")

_MENU_JS = r"""() => [...document.querySelectorAll('.cdk-overlay-pane [role="menuitem"]')]
 .map(e => ({
  lig: (e.querySelector('mat-icon')||{}).textContent?.trim() || '',
  disabled_attr: e.getAttribute('disabled'),
  aria_disabled: e.getAttribute('aria-disabled'),
  prop_disabled: !!e.disabled,
  cls: (e.className||'').toString(),
  text_recorded_not_anchor: (e.innerText||'').replace(/\s+/g,' ').trim().slice(0,120),
  aria_label_recorded: e.getAttribute('aria-label'),
  title: e.getAttribute('title'),
  describedby: e.getAttribute('aria-describedby'),
  tooltip_attr: e.getAttribute('mattooltip') || e.getAttribute('ng-reflect-message'),
  // C4: controls rendered inside or beside the item (the "upgrade" call to action)
  inner_controls: [...e.querySelectorAll('a, button, [role="button"]')].map(c => ({
    tag: c.tagName.toLowerCase(), href: c.getAttribute('href'), cls: (c.className||'').toString(),
    lig: (c.querySelector('mat-icon, .google-symbols')||{}).textContent?.trim() || '',
    text_recorded: (c.innerText||'').trim().slice(0,60)})),
  sibling_controls: [...(e.parentElement||e).children]
    .filter(s => s !== e && !s.matches('[role="menuitem"]'))
    .map(s => ({tag: s.tagName.toLowerCase(), cls: (s.className||'').toString(),
      href: s.getAttribute('href'), html: s.outerHTML.slice(0, 2000),
      links: [...s.querySelectorAll('a')].map(x => x.href)})),
  outer_html: e.outerHTML.slice(0, 1500),
}))"""

_TOOLTIPS_JS = r"""() => [...document.querySelectorAll(
  '[role="tooltip"], .mat-mdc-tooltip, .mdc-tooltip, .cdk-describedby-message-container *')]
  .map(e => (e.innerText||e.textContent||'').replace(/\s+/g,' ').trim()).filter(Boolean)"""

_WARN_JS = r"""(sel) => [...document.querySelectorAll(sel)].map(e => ({
  visible: !!(e.offsetWidth || e.offsetHeight),
  text_recorded: (e.innerText||'').replace(/\s+/g,' ').trim().slice(0,160)}))"""


async def _main(a: argparse.Namespace) -> int:
    report: dict[str, Any] = {"profile": a.profile, "aborted": []}

    async def on_batch(route: Any) -> None:
        seen = route.request.url + (route.request.post_data or "")
        hit = [r for r in ALWAYS_ABORT if r in seen]
        if hit:
            report["aborted"].append(hit)
            await route.abort()
            return
        await route.continue_()

    shot = default_out_path(f"extend_disabled_{a.profile}", ".png")
    async with build_client(resolve_profile_dir(a.profile)) as client:
        ctx, page = client._context, client._page  # noqa: SLF001
        assert ctx is not None and page is not None
        await ctx.route(re.compile(r"batchexecute"), on_batch)
        await page.goto(
            MIGRATED_CLIP_URL.format(project_id=a.project, media_id=a.media),
            wait_until="domcontentloaded",
        )
        add = page.locator(ADD_CLIP_BUTTON).first
        await add.wait_for(state="visible", timeout=30_000)
        await page.wait_for_timeout(3000)
        report["credits_warning_before_menu"] = await page.evaluate(_WARN_JS, CREDITS_WARNING)
        await add.click()
        await page.locator(".cdk-overlay-pane [role='menuitem']").first.wait_for(
            state="visible", timeout=15_000
        )
        report["menu_items"] = await page.evaluate(_MENU_JS)
        ext = page.locator(EXTEND_MENU_ITEM).first
        report["extend_is_disabled"] = await ext.is_disabled()
        report["extend_is_enabled"] = await ext.is_enabled()
        await ext.hover(force=True)
        await page.wait_for_timeout(1500)
        report["tooltips_after_hover"] = await page.evaluate(_TOOLTIPS_JS)
        report["credits_warning_with_menu"] = await page.evaluate(_WARN_JS, CREDITS_WARNING)
        await page.screenshot(path=str(shot))
        report["screenshot"] = str(shot)
        await page.keyboard.press("Escape")
    out = default_out_path(f"extend_disabled_{a.profile}")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    step("done", f"{out}")
    print(json.dumps(report, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--media", required=True)
    return asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
