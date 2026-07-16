#!/usr/bin/env python3
r"""Issue #313 recon — Agent mode's "tune" Settings panel and its sticky
count defaults (0 credits).

A 33-day-old memory (PR #124/#138) documented Agent mode removing the whole
media-generation settings panel from the DOM. That was true when written —
but Flow's UI has since added a NEW settings surface: a `tune` icon on the
Agent composer that opens a full "Agent settings" panel (not a small popover
like classic mode's `crop_*` menu) with STICKY count/aspect/model defaults
for image and video generation separately. This spike proves that mechanism
live and validates the selectors + click mechanics needed to drive it —
see memory `flow-agent-settings-panel-sticky-defaults` for the write-up.

Navigation only — no prompt is submitted, no generate route is hit, 0 credits.

Usage (headed, supervised):

    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_issue313_agent_mode_controls.py \
        --profile denon82 --project 580a6bbf-d433-4153-80b9-1842b5a560ea
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
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    COMPOSER_AGENT_TOGGLE_SELECTOR,
    VideoGenerationMixin,
)

_TUNE_BUTTON_SELECTOR = "button:has(i.google-symbols:text-is('tune'))"
_COMPOSER_SELECTOR = "div[role='textbox'][data-slate-editor='true']"
# The image count control is the FIRST [role='tablist'] in DOM order that
# contains both '1x' and 'x2' buttons — the image section always renders
# before the video section (verified live, both en and pt-BR locales).
_IMAGE_COUNT_TABLIST_SELECTOR = (
    "[role='tablist']:has(button:text-is('1x')):has(button:text-is('x2'))"
)

# Scoped, locale-invariant "Save" lookup: the button has no icon ligature, no
# data-* attribute, no type=submit, and its text ("Salvar"/"Save") is
# locale-dependent. Walk up from the arrow_back header icon to the nearest
# ancestor that also contains the count tablist (the panel root), then take
# the last visible button in that scope.
_FIND_SAVE_BUTTON_JS = """
() => {
  const backBtn = [...document.querySelectorAll('button')].find((b) => {
    const i = b.querySelector('i.google-symbols');
    return i && (i.textContent || '').trim() === 'arrow_back';
  });
  if (!backBtn) return null;
  let node = backBtn.parentElement;
  for (let i = 0; i < 8 && node; i++) {
    const hasCountTablist = [...node.querySelectorAll("[role='tablist']")].some((t) => {
      const texts = [...t.querySelectorAll('button')].map((b) => (b.textContent || '').trim());
      return texts.includes('1x') && texts.includes('x2');
    });
    if (hasCountTablist) {
      const visible = [...node.querySelectorAll('button')].filter((b) => {
        const r = b.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      });
      const save = visible[visible.length - 1];
      save?.setAttribute('data-spike-save-target', '1');
      return true;
    }
    node = node.parentElement;
  }
  return false;
}
"""


async def _run(*, profile_dir: Path, project_id: str, locale: str, out_path: Path) -> int:
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "spike": "issue313-agent-mode-controls",
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": project_id,
        "locale": locale,
    }

    async with build_client(profile_dir, headless=False) as client:
        page = await client._checkout_page()  # noqa: SLF001
        try:
            editor_url = routes.project_editor_url(locale, project_id)
            step("1", f"goto {editor_url}", prefix="313")
            await page.goto(editor_url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(4_000)
            await page.keyboard.press("Escape")

            media_present = await VideoGenerationMixin._media_panel_present(page)  # noqa: SLF001
            result["mediaPanelPresent_onLoad"] = media_present
            if media_present:
                # Toggle into Agent mode (binary pill) if we loaded into classic.
                pill = page.locator(COMPOSER_AGENT_TOGGLE_SELECTOR).first
                if await pill.count() > 0:
                    await pill.click(force=True, timeout=5_000)
                    await page.wait_for_timeout(1_000)
            step("1", f"in Agent mode = {not media_present}", prefix="313")
            await page.screenshot(path=str(out_dir / "1_agent_mode.png"))

            tune_btn = page.locator(_TUNE_BUTTON_SELECTOR).first
            if await tune_btn.count() == 0:
                result["error"] = "no tune button found"
                return 1

            step("2", "opening Agent settings panel (tune icon)", prefix="313")
            await tune_btn.click(timeout=5_000)
            await page.wait_for_timeout(1_000)
            await page.screenshot(path=str(out_dir / "2_settings_panel.png"))

            image_count_tablist = page.locator(_IMAGE_COUNT_TABLIST_SELECTOR).first
            before = await image_count_tablist.locator(
                "button[aria-selected='true']"
            ).text_content()
            result["countBeforeChange"] = (before or "").strip()

            step("3", "setting image count default to 1x", prefix="313")
            one_x_btn = image_count_tablist.locator("button:text-is('1x')").first
            await one_x_btn.click(timeout=5_000)
            await page.wait_for_timeout(300)
            result["ariaSelectedAfterClick"] = await one_x_btn.get_attribute("aria-selected")

            step("4", "saving via scoped, locale-invariant Save selector", prefix="313")
            found_save = await page.evaluate(_FIND_SAVE_BUTTON_JS)
            result["saveButtonFound"] = bool(found_save)
            if found_save:
                save_btn = page.locator("[data-spike-save-target='1']").first
                await save_btn.click(timeout=5_000)
                await page.wait_for_timeout(500)

            composer = page.locator(_COMPOSER_SELECTOR).first
            result["composerVisibleAfterSave"] = (
                await composer.count() > 0 and await composer.is_visible()
            )
            await page.screenshot(path=str(out_dir / "3_after_save.png"))
            step(
                "5",
                f"composer visible after Save = {result['composerVisibleAfterSave']}",
                prefix="313",
            )

            # Reopen to confirm the change persisted (not just an optimistic UI flip).
            await tune_btn.click(timeout=5_000)
            await page.wait_for_timeout(800)
            reselected = await image_count_tablist.locator(
                "button[aria-selected='true']"
            ).text_content()
            result["countAfterReopen"] = (reselected or "").strip()
            await page.screenshot(path=str(out_dir / "4_reopened.png"))
        finally:
            client._checkin_page(page)  # noqa: SLF001
            out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n[313] json -> {out_path}", flush=True)
    print(f"[313] countBeforeChange = {result.get('countBeforeChange')}", flush=True)
    print(f"[313] countAfterReopen  = {result.get('countAfterReopen')}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Issue #313: drive Agent mode's settings-panel count control (0 credits)"
    )
    p.add_argument("--profile", default=os.environ.get("GFLOW_CLI_PROFILE", "denon82"))
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
        Path(args.out) if args.out else default_out_path("spike_issue313_agent_mode", ".json")
    )
    step("--", f"profile={args.profile} project={args.project}", prefix="313")
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
        print("[313] aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
