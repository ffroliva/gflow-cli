#!/usr/bin/env python3
r"""Live $0 spike — the R2V **Ingredients** component end to end.

WHAT THIS TESTS (owner recon, 2026-08-14):
1. Attaching an ingredient produces a **named handle chip in the prompt** (e.g.
   "flowers blooming in a field") — ingredients and `@`-mentions are the SAME
   mechanism in Flow's UI. `docs/REFERENCE_STRATEGIES.md:56` currently calls
   `@media` on `r2v` "Phase 3" (unimplemented), which looks wrong.
2. **Switching the model AFTER attaching flags the ingredient as not accepted.**
   `Veo 3.1 - Quality` refuses image ingredients ("You cannot use image
   ingredients with this model."); Fast / Omni Flash accept them.

gflow already orders this correctly (model is chosen inside
`configure_video_settings`, ingredients attach after), but it has **no
ingredient x model capability check**, and for R2V the model select is
``required=False`` — so with ``--model`` omitted Flow's *sticky default* applies
and may be a model that refuses ingredients.

The spike drives the real production helpers (`_switch_video_sub_mode`,
`_attach_remote_references`) so it exercises the same code an `r2v` run does.

**Credit-free:** never types a prompt for submission, never clicks Generate.

Usage:
    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\capture_r2v_ingredient_lifecycle.py \
        --profile denon82 --project <id> --ref-name "a brass key on dark velvet"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts" / "dev"))

from _spike_common import build_client, default_out_path, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports.mode_control import CROP_SELECTORS  # noqa: E402
from gflow_cli.api.transports.ui_automation_video import (  # noqa: E402
    VIDEO_MODEL_OPTION_SELECTORS,
    VideoGenerationMixin,
)
from gflow_cli.api.video import VideoModel  # noqa: E402


async def _wait_editor(page: Any) -> bool:
    for _ in range(30):
        if (
            await page.locator('div[role="textbox"], textarea').count() > 0
            or await page.locator("button").count() > 8
        ):
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


async def _composer_state(page: Any) -> dict[str, Any]:
    """Read the composer: attached ingredient tiles, their flagged state, and
    any handle chips rendered into the prompt."""
    return await page.evaluate(
        """() => {
          const body = document.body.innerText;
          const rejectRe = /cannot use image ingredients|ingredientes de imagem/i;
          // Ingredient tiles carry a small img thumbnail; a rejected one gets an
          // overlay/badge, which shows up as an extra symbol inside the tile.
          const tiles = [...document.querySelectorAll('button, div')]
            .filter(e => e.querySelector('img') && e.clientHeight < 140 && e.clientWidth < 140)
            .slice(0, 12)
            .map(e => ({
              html: e.outerHTML.slice(0, 260),
              ligatures: [...e.querySelectorAll('i')].map(i => (i.textContent||'').trim()),
              img_alt: (e.querySelector('img')||{}).alt || null,
            }));
          // Handle chips: short pill-like nodes inside the composer area.
          const chips = [...document.querySelectorAll('span, div, button')]
            .map(e => ({t: (e.textContent||'').replace(/\\s+/g,' ').trim(), c: e.className}))
            .filter(o => o.t && o.t.length > 3 && o.t.length < 60)
            .filter(o => /chip|tag|token|mention|pill/i.test(String(o.c)))
            .slice(0, 12);
          return {
            reject_notice: rejectRe.test(body),
            tiles,
            chips,
          };
        }"""
    )


async def _select_model(page: Any, model: VideoModel) -> bool:
    from gflow_cli.api.transports.ui_automation_video import MODEL_PICKER_TRIGGER

    trig = page.locator(MODEL_PICKER_TRIGGER).first
    if await trig.count() == 0:
        return False
    try:
        await trig.click(timeout=4000)
        await page.wait_for_timeout(500)
        opt = page.locator(VIDEO_MODEL_OPTION_SELECTORS[model]).first
        if await opt.count() == 0:
            await page.keyboard.press("Escape")
            return False
        await opt.click(timeout=4000)
        await page.wait_for_timeout(900)
    except Exception:  # noqa: BLE001
        return False
    return True


async def _run(profile: str, project: str, ref_name: str) -> int:
    findings: dict[str, Any] = {"profile": profile, "project": project, "ref_name": ref_name}
    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        step("nav", f"goto editor {project}")
        await page.goto(
            routes.project_editor_url("en", project),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        await _wait_editor(page)

        if not await _open_settings(page):
            from gflow_cli.api.transports import mode_control  # noqa: PLC0415

            await mode_control.ensure_media_mode(page, allow_reload=True)
            await _wait_editor(page)
            if not await _open_settings(page):
                step("menu", "ERROR: no composer settings popover")
                return 1

        await _click_lig(page, "videocam")

        # 1. Choose a model that ACCEPTS ingredients, before attaching.
        ok_fast = await _select_model(page, VideoModel.VEO_3_1_FAST)
        step("model", f"selected Veo 3.1 - Fast = {ok_fast}")

        # 2. Switch to the Ingredients (references) sub-mode via PRODUCTION code.
        await _open_settings(page)
        try:
            await VideoGenerationMixin._switch_video_sub_mode(  # noqa: SLF001
                page, "references", out_dir=None
            )
            submode_ok = True
        except Exception as exc:  # noqa: BLE001
            submode_ok = False
            step("submode", f"  _switch_video_sub_mode FAILED: {type(exc).__name__}: {exc}")
        step("submode", f"references sub-mode selected = {submode_ok}")

        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)

        # 3. Attach an ingredient using the production remote-reference helper.
        try:
            await VideoGenerationMixin._attach_remote_references(  # noqa: SLF001
                page, [ref_name], out_dir=None
            )
            attach_ok = True
        except Exception as exc:  # noqa: BLE001
            attach_ok = False
            step("attach", f"  _attach_remote_references FAILED: {type(exc).__name__}")
            # The picker is still open after the name timeout — dump what it
            # ACTUALLY offers. gflow searches by exact display_name, but the
            # catalog stores display_name=None for these assets and Flow labels
            # options with a short auto-caption, so the search can never hit.
            opts = await page.evaluate(
                """() => [...document.querySelectorAll("[role='option']")].map(o => ({
                     name: (o.getAttribute('aria-label') || o.textContent || '')
                             .replace(/\\s+/g,' ').trim().slice(0, 80),
                     tile: o.getAttribute('data-tile-id'),
                   })).slice(0, 25)"""
            )
            findings["picker_options_offered"] = opts
            step("picker", f"  picker offers {len(opts)} options: {[o['name'] for o in opts][:6]}")
        step("attach", f"ingredient attached = {attach_ok}")

        after_attach = await _composer_state(page)
        findings["after_attach_on_fast"] = after_attach
        chip_texts = [c["t"] for c in after_attach["chips"]][:4]
        step(
            "state",
            f"  reject_notice={after_attach['reject_notice']} "
            f"tiles={len(after_attach['tiles'])} chips={chip_texts}",
        )

        # 4. Now switch to a model that REFUSES ingredients and re-read.
        await _open_settings(page)
        ok_q = await _select_model(page, VideoModel.VEO_3_1_QUALITY)
        step("model", f"switched to Veo 3.1 - Quality = {ok_q}")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(900)

        after_switch = await _composer_state(page)
        findings["after_switch_to_quality"] = after_switch
        step(
            "state",
            f"  reject_notice={after_switch['reject_notice']} tiles={len(after_switch['tiles'])}",
        )

    out = default_out_path("r2v_ingredient_lifecycle")
    out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {out}")

    print("\n=== VERDICT ===")
    a = findings.get("after_attach_on_fast", {})
    b = findings.get("after_switch_to_quality", {})
    print(f"  ingredient-reject notice on Veo 3.1 Fast    : {a.get('reject_notice')}")
    print(f"  ingredient-reject notice on Veo 3.1 Quality : {b.get('reject_notice')}")
    print(f"  handle chips seen after attach              : {[c['t'] for c in a.get('chips', [])]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--ref-name", required=True)
    args = ap.parse_args()
    return asyncio.run(_run(args.profile, args.project, args.ref_name))


if __name__ == "__main__":
    raise SystemExit(main())
