#!/usr/bin/env python3
r"""Issue #404 recon — classic composer count tabs: why does clicking
``nth(0) of _count_tabs_locator`` report success but never change the
displayed count? (0 credits)

Replicates ``_set_count``'s exact steps using the transport's own static
helpers (``_open_gen_settings_panel`` / ``_read_displayed_count`` /
``_count_tabs_locator`` / ``_dump_count_panel_dom``), then probes the
candidate fix: clicking the count tab whose TEXT contains the desired digit,
scoped to the tablist that owns the currently aria-selected count tab.

Navigation only — no prompt is submitted, no generate route is hit, 0 credits.

Usage (headed, supervised):

    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_issue404_count_tabs_recon.py \
        --profile ffroliva --project 9d7b750f-b4a8-4c2f-b5b0-a059cbfbae73
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports.ui_automation import (  # noqa: E402
    UiAutomationTransport,
    _count_tabs_locator,
)
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    COMPOSER_AGENT_TOGGLE_SELECTOR,
    VideoGenerationMixin,
)

# Per count tab: text, aria-selected, rect, and a fingerprint of the owning
# tablist (sibling tab texts in DOM order) so duplicate tablists are visible.
_TABS_TOPOLOGY_JS = """
() => {
  const isCount = (t) => /^(1x|x[2-4])$/.test((t || '').trim());
  const tablists = [...document.querySelectorAll("[role='tablist']")];
  return {
    tablists: tablists.map((tl, i) => {
      const r = tl.getBoundingClientRect();
      return {
        index: i,
        tabTexts: [...tl.querySelectorAll("[role='tab']")].map(
          (t) => (t.innerText || '').trim().slice(0, 40)),
        rect: { x: r.x, y: r.y, w: r.width, h: r.height },
        visible: r.width > 0 && r.height > 0,
      };
    }),
    countTabs: [...document.querySelectorAll("[role='tab']")]
      .filter((t) => isCount(t.innerText))
      .map((t) => {
        const tl = t.closest("[role='tablist']");
        const r = t.getBoundingClientRect();
        return {
          text: (t.innerText || '').trim(),
          ariaSelected: t.getAttribute('aria-selected'),
          tablistIndex: tl ? tablists.indexOf(tl) : null,
          rect: { x: r.x, y: r.y, w: r.width, h: r.height },
          visible: r.width > 0 && r.height > 0,
        };
      }),
  };
}
"""


async def _ensure_classic(page: Any, result: dict[str, Any]) -> bool:
    media_present = await VideoGenerationMixin._media_panel_present(page)  # noqa: SLF001
    result["mediaPanelPresent_onLoad"] = media_present
    if not media_present:
        pill = page.locator(COMPOSER_AGENT_TOGGLE_SELECTOR).first
        if await pill.count() > 0:
            await pill.click(force=True, timeout=5_000)
            await page.wait_for_timeout(1_500)
            media_present = await VideoGenerationMixin._media_panel_present(page)  # noqa: SLF001
    result["classicMode"] = media_present
    return media_present


async def _run(*, profile_dir: Path, project_id: str, locale: str, out_path: Path) -> int:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "spike": "issue404-count-tabs-recon",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": project_id,
    }
    T = UiAutomationTransport

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001
        try:
            editor_url = routes.project_editor_url(locale, project_id)
            step("1", f"goto {editor_url}", prefix="404")
            await page.goto(editor_url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)
            await page.keyboard.press("Escape")

            if not await _ensure_classic(page, result):
                result["error"] = "could not reach classic mode"
                return 1

            step("2", "opening generation settings panel (transport helper)", prefix="404")
            panel_open = await T._is_settings_panel_open(page)  # noqa: SLF001
            if not panel_open:
                panel_open = await T._open_gen_settings_panel(page)  # noqa: SLF001
            result["panelOpened"] = panel_open
            if not panel_open:
                result["error"] = "settings panel did not open"
                return 1

            # The real t2i path switches to IMAGE mode before configuring
            # settings (#40) — replicate via the ligature-keyed mode tab
            # (locale-invariant, memory: flow-locale-leak-icon-ligatures).
            step("2b", "selecting Image mode tab (ligature-keyed)", prefix="404")
            image_tab = page.locator(
                "[role='tab']:has(i.google-symbols:text-is('image'))"
            ).first
            await image_tab.wait_for(state="visible", timeout=5_000)
            await image_tab.click()
            await page.wait_for_timeout(800)
            result["imageModeSelected"] = await image_tab.get_attribute("aria-selected")
            await page.screenshot(path=str(out_dir / "404_1_panel_open.png"))

            # Full role dump via the transport's own diagnostic (issue #24 shape).
            await T._dump_count_panel_dom(page, out_dir, 404)  # noqa: SLF001
            result["topology_before"] = await page.evaluate(_TABS_TOPOLOGY_JS)
            result["displayed_before"] = await T._read_displayed_count(page)  # noqa: SLF001

            step("3", "replicating failing click: nth(0) of _count_tabs_locator", prefix="404")
            tabs = _count_tabs_locator(page)
            result["filtered_set_size"] = await tabs.count()
            target = tabs.nth(0)
            result["nth0_before_click"] = await target.evaluate(
                """(t) => {
                  const tablists = [...document.querySelectorAll("[role='tablist']")];
                  return {
                    text: (t.innerText || '').trim(),
                    ariaSelected: t.getAttribute('aria-selected'),
                    tablistIndex: tablists.indexOf(t.closest("[role='tablist']")),
                    outerHTML: t.outerHTML.slice(0, 400),
                  };
                }"""
            )
            try:
                await target.wait_for(state="visible", timeout=3_000)
                await target.click()
                await page.wait_for_timeout(300)
                result["nth0_click"] = "landed"
            except Exception as e:  # noqa: BLE001
                result["nth0_click"] = f"error: {e}"
            result["nth0_after_click_ariaSelected"] = await target.get_attribute("aria-selected")
            result["displayed_after_nth0"] = await T._read_displayed_count(page)  # noqa: SLF001
            result["topology_after_nth0"] = await page.evaluate(_TABS_TOPOLOGY_JS)
            await page.screenshot(path=str(out_dir / "404_2_after_nth0.png"))

            step("4", "candidate fix: text-keyed click scoped to selected tablist", prefix="404")
            fix = await page.evaluate(
                """() => {
                  // Union of old ("1x", "x2"..) and new ("x1", "x2"..) label cohorts.
                  const isCount = (t) => /^(1x|x[1-4])$/.test((t || '').trim());
                  const selected = [...document.querySelectorAll(
                    "[role='tab'][aria-selected='true']")].filter((t) => isCount(t.innerText));
                  if (!selected.length) return { found: false, reason: 'no selected count tab' };
                  const tl = selected[0].closest("[role='tablist']");
                  if (!tl) return { found: false, reason: 'selected tab has no tablist' };
                  const target = [...tl.querySelectorAll("[role='tab']")].find(
                    (t) => /^(1x|x1)$/.test((t.innerText || '').trim()));
                  if (!target) return { found: false, reason: 'no count-1 tab in selected tablist' };
                  target.setAttribute('data-spike-404-target', '1');
                  return { found: true };
                }"""
            )
            result["fix_target"] = fix
            if fix.get("found"):
                fix_btn = page.locator("[data-spike-404-target='1']").first
                await fix_btn.click(timeout=5_000)
                await page.wait_for_timeout(300)
                result["fix_after_click_ariaSelected"] = await fix_btn.get_attribute(
                    "aria-selected"
                )
                result["displayed_after_fix"] = await T._read_displayed_count(page)  # noqa: SLF001
                await page.screenshot(path=str(out_dir / "404_3_after_fix.png"))

                step("5", "persistence: Escape-close, reopen, re-read", prefix="404")
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(400)
                reopened = await T._open_gen_settings_panel(page)  # noqa: SLF001
                result["panelReopened"] = reopened
                result["displayed_after_reopen"] = await T._read_displayed_count(page)  # noqa: SLF001
                result["topology_after_reopen"] = await page.evaluate(_TABS_TOPOLOGY_JS)
                await page.screenshot(path=str(out_dir / "404_4_reopened.png"))

            # Cleanup: earlier probe runs flipped the VIDEO count row to x1 on
            # this project — restore Flow's x2 default so the recon leaves no
            # sticky state behind.
            step("6", "cleanup: restore video count row to x2", prefix="404")
            video_tab = page.locator(
                "[role='tab']:has(i.google-symbols:text-is('videocam'))"
            ).first
            if await video_tab.count() > 0:
                await video_tab.click()
                await page.wait_for_timeout(800)
                x2_tab = page.locator("[role='tab']:text-is('x2')").first
                if await x2_tab.count() > 0:
                    await x2_tab.click()
                    await page.wait_for_timeout(300)
                    result["videoCountRestored"] = await x2_tab.get_attribute(
                        "aria-selected"
                    )
        finally:
            client._checkin_page(page)  # noqa: SLF001
            out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n[404] json -> {out_path}", flush=True)
    for key in (
        "displayed_before",
        "filtered_set_size",
        "nth0_before_click",
        "nth0_click",
        "nth0_after_click_ariaSelected",
        "displayed_after_nth0",
        "fix_target",
        "fix_after_click_ariaSelected",
        "displayed_after_fix",
        "displayed_after_reopen",
    ):
        print(f"[404] {key} = {json.dumps(result.get(key), ensure_ascii=False)}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Issue #404: classic composer count-tab recon (0 credits)"
    )
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "ffroliva"))
    p.add_argument(
        "--project",
        default=os.environ.get("GFLOW_CLI_PROJECT"),
        required="GFLOW_CLI_PROJECT" not in os.environ,
    )
    p.add_argument("--locale", default=os.environ.get("GFLOW_CLI_LOCALE", "en"))
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    profile_dir = resolve_profile_dir(args.profile)
    out_path = (
        Path(args.out) if args.out else default_out_path("spike_issue404_count_tabs", ".json")
    )
    step("--", f"profile={args.profile} project={args.project}", prefix="404")
    try:
        return asyncio.run(
            _run(
                profile_dir=profile_dir,
                project_id=args.project,
                locale=args.locale,
                out_path=out_path,
            )
        )
    except KeyboardInterrupt:
        print("[404] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
