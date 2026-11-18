#!/usr/bin/env python3
r"""#493 decisive probe — what ligature does the settings TRIGGER carry in each
composer sub-mode?

HYPOTHESIS (2026-08-14): `CROP_SELECTORS` (mode_control.py:49) — the trigger
cascade used by BOTH the `mode_switch_trigger` probe and `detect_ui_mode` —
matches six aspect ligatures (crop_16_9 / 9_16 / square / portrait / landscape /
original) but NOT `crop_free`, which `ui_automation_video.py:634` documents as
the **Frames** (start/end frame) tab icon.

If the trigger button renders the ligature of the ACTIVE sub-mode, then a
composer sitting in Frames mode shows a `crop_free` trigger that no selector
matches → `mode_switch_trigger` misses → `UiSelectorDriftError` exit 23, and
`detect_ui_mode` finds no classic signal and falls through after its 8s poll.

That is exactly issue #493's fingerprint: a reporter on pt-BR seeing frame slots
"Inicial"/"Final" (= Frames mode) and "no crop_* settings button".

This probe reads the trigger's ligature in each sub-mode and reports whether any
production selector matches. Credit-free: navigation + tab clicks + DOM reads.
Never types a prompt, never clicks Generate.

Usage:
    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\capture_frames_mode_trigger_ligature.py \
        --profile denon82 --project <project-id>
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

# Sub-mode tabs inside the open video popover, keyed by their (locale-invariant)
# Material Symbols ligature.
_SUBMODE_TABS = {
    "frames": "crop_free",
    "ingredients": "chrome_extension",
}


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
    """True only for the SETTINGS popover — a menu that actually carries tabs.

    `[role='menu']` alone is too loose: the editor has other menus (project /
    account) with no tabs, and accepting one of those made every sub-mode tab
    look 'absent'.
    """
    return await page.locator("[role='menu'] [role='tab']").count() > 0


async def _open_settings(page: Any) -> bool:
    """Open the COMPOSER settings popover via the production CROP_SELECTORS.

    An earlier "try every aria-haspopup button" version kept opening the wrong
    menu (the account menu, then Flow's app-settings panel with its
    dashboard/batch/size tabs), which made every sub-mode tab look absent.
    Opening via the production cascade is also the honest experiment: we already
    know it works while an ASPECT sub-mode is active — the question under test is
    only whether it still matches AFTER switching to Frames.
    """
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


async def _click_tab(page: Any, ligature: str) -> bool:
    tab = page.locator(f"[role='tab']:has(i.google-symbols:text-is('{ligature}'))").first
    if await tab.count() == 0:
        return False
    try:
        await tab.click(timeout=4000)
        await page.wait_for_timeout(800)
    except Exception:  # noqa: BLE001
        return False
    return True


async def _trigger_report(page: Any) -> dict[str, Any]:
    """Every aria-haspopup trigger with the ligatures it renders."""
    triggers = await page.evaluate(
        """() => [...document.querySelectorAll("button[aria-haspopup='menu']")].map(b => ({
             ligatures: [...b.querySelectorAll('i.google-symbols, i.material-symbols-outlined')]
                          .map(i => (i.textContent || '').trim()),
             text: (b.textContent || '').replace(/\\s+/g,' ').trim().slice(0, 60),
             aria: b.getAttribute('aria-label'),
           }))"""
    )
    matched = {}
    for sel in CROP_SELECTORS:
        # "…text('crop_16_9'))" -> "crop_16_9" (rstrip would eat chars, not a suffix)
        name = sel.split("text('")[1].split("'")[0]
        matched[name] = await page.locator(sel).count()
    return {"triggers": triggers, "production_selector_hits": matched}


async def _run(profile: str, project: str) -> int:
    findings: dict[str, Any] = {"profile": profile, "project": project, "modes": {}}
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
        if not await _wait_editor(page):
            step("nav", "ERROR editor not ready")
            return 1

        if not await _open_settings(page):
            # The account's isAgentModeToggled persists server-side, so the
            # editor can load agentic (no classic composer at all).
            step("mode", "no popover — running ensure_media_mode()")
            from gflow_cli.api.transports import mode_control  # noqa: PLC0415

            acted = await mode_control.ensure_media_mode(page, allow_reload=True)
            step("mode", f"  ensure_media_mode acted={acted}")
            await _wait_editor(page)

        if not await _open_settings(page):
            step("menu", "ERROR could not open the settings popover at all")
            return 1
        async def _tabs() -> list[str]:
            return await page.evaluate(
                """() => [...document.querySelectorAll("[role='tab']")]
                       .map(t => (t.textContent||'').replace(/\\s+/g,' ').trim())"""
            )

        step("tabs", f"before video-tab click: {await _tabs()}")
        vid = await _click_tab(page, "videocam")  # ensure VIDEO mode
        step("tabs", f"videocam clicked={vid} -> {await _tabs()}")

        for name, ligature in _SUBMODE_TABS.items():
            await _open_settings(page)
            if not await _click_tab(page, ligature):
                step(name, f"  tab '{ligature}' not present — skipping")
                findings["modes"][name] = {"tab_present": False}
                continue
            # Close the popover so we read the trigger in its resting state.
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
            report = await _trigger_report(page)
            hits = {k: v for k, v in report["production_selector_hits"].items() if v}
            all_lig = sorted({lig for t in report["triggers"] for lig in t["ligatures"]})
            findings["modes"][name] = {
                "tab_present": True,
                "trigger_ligatures": all_lig,
                "production_selector_hits": hits,
                "production_cascade_matches": bool(hits),
                "triggers": report["triggers"],
            }
            step(
                name,
                f"  trigger ligatures={all_lig} | CROP_SELECTORS match={bool(hits)} {hits}",
            )

    out = default_out_path("frames_mode_trigger_ligature")
    out.write_text(json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {out}")

    print("\n=== VERDICT ===")
    for name, m in findings["modes"].items():
        if not m.get("tab_present"):
            print(f"  {name:<12} tab absent")
            continue
        ok = m["production_cascade_matches"]
        print(
            f"  {name:<12} CROP_SELECTORS match={ok}  ligatures={m['trigger_ligatures']}"
            + ("" if ok else "   <-- production would MISS here (#493 shape)")
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
