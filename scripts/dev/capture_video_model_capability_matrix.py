#!/usr/bin/env python3
r"""Live $0 recon — per-model capability matrix for the classic video composer.

WHY (2026-08-14 owner recon): the settings popover is **model-conditional**.
Screenshots showed `Omni Flash` rendering a duration row (4s/6s/8s/10s) while
`Veo 3.1 - Quality/Lite/Fast` render **no duration control at all**, and
`Veo 3.1 - Quality` rejecting image ingredients ("You cannot use image
ingredients with this model.") that `Veo 3.1 - Fast` accepts.

`api/video.py:44` currently claims "the four VEO_3_1_* models cap at 8s", which
assumes they expose a duration control. If they expose none, that is the
unexplained root cause of #451 / #288 (`--duration` "broken", A/B-identical on
playwright 1.59 and 1.61 — never a Playwright regression, never locale).

This spike reads the popover for EVERY model and dumps a structured matrix:
duration tabs, count tabs, the live credit cost, the composer tag, and any
ingredient-rejection notice.

**Credit-free by construction:** navigation + popover reads only. It never types
a prompt and never clicks Generate. Selecting a model in the picker does not
bill; only submission does.

Usage:
    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\capture_video_model_capability_matrix.py \
        --profile ffroliva --project 5ee3e625-ff3f-44a1-9f17-1434f432f30e
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
    MODEL_PICKER_TRIGGER,
    VIDEO_MODEL_OPTION_SELECTORS,
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


async def _open_settings(page: Any) -> bool:
    """Ensure the settings popover is OPEN, idempotently.

    The crop_* trigger is a TOGGLE: clicking it while the popover is already
    open closes it. Clicking unconditionally produced empty reads alternating
    with model-picker misses (the picker lives *inside* the popover), so probe
    for the menu first and only click when it is absent.
    """
    if await page.locator("[role='menu']").count() > 0:
        return True
    for sel in CROP_SELECTORS:
        loc = page.locator(sel).first
        if await loc.count() > 0:
            try:
                await loc.click(timeout=4000)
                await page.wait_for_timeout(600)
            except Exception:  # noqa: BLE001 - benign: a racing render may have opened it
                pass
            return await page.locator("[role='menu']").count() > 0
    return False


async def _ensure_video_mode(page: Any) -> bool:
    """Select the popover's Video tab (the model picker only exists there).

    Keyed on the Material Symbols ligature ``videocam``, NOT the tab text: on a
    pt-BR account the label reads ``videocamVídeo``, and text matching would
    miss (memory: flow-locale-leak-icon-ligatures). Without this the popover
    stays in Image mode — five aspect tabs, no video model picker — and every
    model lookup misses, which is exactly what denon82 showed.
    """
    tab = page.locator("[role='tab']:has(i.google-symbols:text-is('videocam'))").first
    if await tab.count() == 0:
        return False
    if await tab.get_attribute("aria-selected") == "true":
        return True
    try:
        await tab.click(timeout=4000)
        await page.wait_for_timeout(700)
    except Exception:  # noqa: BLE001 - verified by the caller's re-read
        return False
    return True


async def _menu_state(page: Any) -> dict[str, Any]:
    """Structured read of the OPEN settings popover — pure DOM, no clicks."""
    return await page.evaluate(
        """() => {
          const menu = document.querySelector("[role='menu']");
          const scope = menu || document.body;
          const tabs = [...scope.querySelectorAll("[role='tab']")].map(t => ({
            label: (t.getAttribute('aria-label') || t.textContent || '').trim(),
            id: t.id || null,
            selected: t.getAttribute('aria-selected') === 'true',
          })).filter(t => t.label);
          const text = (scope.textContent || '').replace(/\\s+/g, ' ').trim();
          // Locale-tolerant: match a number next to a credit-word stem, which
          // covers en "120 credits", pt "120 creditos/creditos", es "creditos".
          // An English-only /Generating will use N credits/ silently returns
          // null on a pt-BR UI and reads as "no cost shown" (memory:
          // flow-locale-leak-icon-ligatures).
          const creditRe = /([\\d.,]+)\\s*(?:cr[e\\u00e9]dito?s?|credits?)/i;
          const credits = (text.match(creditRe) || [])[1] || null;
          // The composer's dynamic summary chip, e.g. "Video · 4s x1".
          // "Video" is a product-ish word that also reads in pt ("Video"), but
          // keep the raw menu text so a locale miss is visible, not silent.
          const chip = [...document.querySelectorAll('button, div')]
            .map(e => (e.textContent || '').replace(/\\s+/g, ' ').trim())
            .filter(t => /^V[i\\u00ed]deo\\b/i.test(t) && t.length <= 40)
            .sort((a, b) => a.length - b.length)[0] || null;
          const body = document.body.innerText.toLowerCase();
          return {
            menu_present: !!menu,
            tabs,
            credits_text: credits,
            composer_chip: chip,
            menu_text: text.slice(0, 400),
            page_lang: document.documentElement.lang || null,
            ingredient_reject: body.includes('cannot use image ingredients')
              || body.includes('ingredientes de imagem'),
          };
        }"""
    )


def _classify(tabs: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Split the popover's tab labels into duration vs count vs aspect."""
    duration, count, aspect, other = [], [], [], []
    for t in tabs:
        label = t["label"]
        if label.rstrip().endswith("s") and label[:-1].strip().isdigit():
            duration.append(label)
        elif label.lower().startswith("x") and label[1:].strip().isdigit():
            count.append(label)
        elif ":" in label:
            aspect.append(label)
        else:
            other.append(label)
    return {"duration": duration, "count": count, "aspect": aspect, "other": other}


async def _select_model(page: Any, model: VideoModel) -> bool:
    """Open the model picker and click *model*. No submit; nothing is billed."""
    trigger = page.locator(MODEL_PICKER_TRIGGER).first
    if await trigger.count() == 0:
        return False
    try:
        await trigger.click(timeout=4000)
        await page.wait_for_timeout(500)
    except Exception:  # noqa: BLE001
        return False
    option = page.locator(VIDEO_MODEL_OPTION_SELECTORS[model]).first
    if await option.count() == 0:
        await page.keyboard.press("Escape")
        return False
    try:
        await option.click(timeout=4000)
        await page.wait_for_timeout(800)
    except Exception:  # noqa: BLE001
        return False
    return True


async def _run(profile: str, project: str) -> int:
    rows: list[dict[str, Any]] = []
    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        step("nav", f"goto editor for project {project}")
        await page.goto(
            routes.project_editor_url("en", project),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        if not await _wait_editor(page):
            step("nav", "ERROR: editor never became ready")
            return 1

        # The account's `isAgentModeToggled` persists server-side, so the editor
        # can load agentic (no crop_* trigger, no classic settings popover).
        # Restore classic with the production primitive — this is the sanctioned
        # pre-bind reload site, and this spike is pre-bind recon.
        if not await _open_settings(page):
            step("mode", "no classic composer — running ensure_media_mode()")
            from gflow_cli.api.transports import mode_control  # noqa: PLC0415

            acted = await mode_control.ensure_media_mode(page, allow_reload=True)
            step("mode", f"  ensure_media_mode acted={acted}")
            await _wait_editor(page)

        if not await _open_settings(page):
            step("menu", "ERROR: still no crop_* settings trigger after mode recovery")
            return 1

        video_ok = await _ensure_video_mode(page)
        step("mode", f"video tab selected={video_ok}")
        baseline = await _menu_state(page)
        step("menu", f"baseline tabs={len(baseline['tabs'])} credits={baseline['credits_text']}")

        for model in VideoModel:
            step(model.value, "selecting...")
            await _open_settings(page)
            await _ensure_video_mode(page)
            ok = await _select_model(page, model)
            if not ok:
                # A miss is a RESULT, not a dead end — capture what the popover
                # actually rendered so a cohort/locale difference is diagnosable
                # instead of an unexplained "PICKER MISS".
                miss_state = await _menu_state(page)
                trigger_count = await page.locator(MODEL_PICKER_TRIGGER).count()
                menuitems = await page.evaluate(
                    """() => [...document.querySelectorAll("[role='menuitem']")]
                           .map(e => (e.textContent || '').replace(/\\s+/g,' ').trim())
                           .slice(0, 20)"""
                )
                rows.append(
                    {
                        "model": model.value,
                        "selected": False,
                        "menu_text": miss_state["menu_text"],
                        "page_lang": miss_state["page_lang"],
                        "tabs": [t["label"] for t in miss_state["tabs"]],
                        "model_trigger_count": trigger_count,
                        "menuitems_seen": menuitems,
                    }
                )
                step(
                    model.value, f"  picker miss — tabs={[t['label'] for t in miss_state['tabs']]}"
                )
                continue
            await _open_settings(page)
            state = await _menu_state(page)
            groups = _classify(state["tabs"])
            row = {
                "model": model.value,
                "selected": True,
                "duration_tabs": groups["duration"],
                "has_duration_control": bool(groups["duration"]),
                "count_tabs": groups["count"],
                "aspect_tabs": groups["aspect"],
                "credits_text": state["credits_text"],
                "composer_chip": state["composer_chip"],
                "ingredient_rejected": state["ingredient_reject"],
                "page_lang": state["page_lang"],
                "menu_text": state["menu_text"],
            }
            rows.append(row)
            step(
                model.value,
                f"  duration={groups['duration'] or 'NONE'} "
                f"count={groups['count']} credits={state['credits_text']} "
                f"chip={state['composer_chip']!r} ingr_reject={state['ingredient_reject']}",
            )

    out = default_out_path("video_model_capability_matrix")
    payload = {
        "project": project,
        "profile": profile,
        "note": "credit-free: navigation + popover reads only, never submitted",
        "baseline": baseline,
        "model_picker_trigger": MODEL_PICKER_TRIGGER,
        "models": rows,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {out}")

    print("\n=== MATRIX ===")
    for r in rows:
        if not r.get("selected"):
            print(f"  {r['model']:<16} PICKER MISS")
            continue
        # Format the list to a string FIRST — `[...] or 'NONE'` yields a list
        # when non-empty, and a list has no __format__ for a width spec.
        duration = "/".join(r["duration_tabs"]) or "NONE"
        print(
            f"  {r['model']:<28} duration={duration:<20} "
            f"count={len(r['count_tabs'])} credits={r['credits_text']} "
            f"ingr_reject={r['ingredient_rejected']}"
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
