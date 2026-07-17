#!/usr/bin/env python3
r"""Mode-switch recon (#299 core) — can we click the Agent toggle to get back
to CLASSIC media mode on a forced-agentic account? (0 credits, navigation only.)

The current `_exit_agent_mode` clicks the in-composer pill AT MOST ONCE and, if
the classic `crop_*` media panel doesn't return while a forced-agentic indicator
is present, gives up ("not recoverable"). This spike is exploratory: it dumps
EVERY agent/mode affordance in the DOM, then systematically clicks candidate
toggles (dismiss chat panel, click the Agent pill, and a broad text/ligature
scan for any 'Agent'-like button), re-checking for the classic `crop_*` trigger
after each action. Goal: find the reliable click sequence that restores classic,
so it can be built into a clean, robust ModeController component.

Usage (headed, supervised):
    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_mode_switch_agentic_to_classic.py \
        --profile ffroliva --project <project-id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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
    AGENT_CHAT_PANEL_CLOSE_SELECTOR,
    COMPOSER_AGENT_TOGGLE_SELECTOR,
    MODE_SWITCH_TRIGGER_SELECTORS,
    VideoGenerationMixin,
)

# Forced-agentic indicator ligatures (factory.py doc).
_AGENTIC_INDICATORS = {
    "apps_spark_2": "i.google-symbols:text-is('apps_spark_2')",
    "article_spark": "i.google-symbols:text-is('article_spark')",
    "edit_square": "i.google-symbols:text-is('edit_square')",
    "tune": "i.google-symbols:text-is('tune')",
}
_CLASSIC = MODE_SWITCH_TRIGGER_SELECTORS  # crop_* trigger present == classic panel back

# Broad scan: any button whose text/ligature hints at an Agent/mode toggle.
_BUTTON_SCAN_JS = r"""
() => {
  const btns = Array.from(document.querySelectorAll('button'));
  return btns.map((b, i) => {
    const lig = b.querySelector('i.google-symbols');
    const span = b.querySelector('span');
    const t = (b.innerText || '').trim().slice(0, 40);
    return {
      i, text: t,
      ligature: lig ? lig.textContent.trim() : null,
      spanText: span ? (span.textContent || '').trim().slice(0, 40) : null,
      ariaLabel: b.getAttribute('aria-label'),
    };
  }).filter(x => {
    const hay = `${x.text} ${x.ligature} ${x.spanText} ${x.ariaLabel}`.toLowerCase();
    return /agent|spark|classic|mode|image|video|crop/.test(hay);
  }).slice(0, 25);
}
"""


async def _classic_present(page: Any) -> bool:
    for sel in _CLASSIC:
        if await page.locator(sel).count() > 0:
            return True
    return False


async def _snapshot(page: Any) -> dict[str, Any]:
    snap: dict[str, Any] = {"classic_crop_present": await _classic_present(page)}
    snap["media_panel_present"] = await VideoGenerationMixin._media_panel_present(page)  # noqa: SLF001
    snap["agent_pill_present"] = await page.locator(COMPOSER_AGENT_TOGGLE_SELECTOR).first.count() > 0
    snap["chat_close_present"] = await page.locator(AGENT_CHAT_PANEL_CLOSE_SELECTOR).first.count() > 0
    ind: dict[str, int] = {}
    for name, sel in _AGENTIC_INDICATORS.items():
        ind[name] = await page.locator(sel).count()
    snap["agentic_indicators"] = ind
    return snap


async def _run(profile: str, project: str) -> dict[str, Any]:
    out_dir = default_out_path("mode_switch", ".json").parent
    result: dict[str, Any] = {"profile": profile, "project": project, "steps": []}
    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        url = routes.project_editor_url("en", project)
        step("A", f"goto {url}", prefix="mode")
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        # Flow is a heavy SPA — poll until the editor actually renders (the Slate
        # prompt box is present in BOTH cohorts) before snapshotting, else the DOM
        # is empty and every selector reads 0.
        step("A2", "wait for editor to render", prefix="mode")
        editor_ready = False
        for _ in range(30):  # ~30 s max
            box = await page.locator('div[role="textbox"][data-slate-editor="true"]').count()
            btns = await page.locator("button").count()
            if box > 0 or btns > 8:
                editor_ready = True
                break
            await page.wait_for_timeout(1000)
        result["editor_ready"] = editor_ready
        result["url_after_load"] = page.url
        await page.wait_for_timeout(1500)

        step("B", "initial snapshot + candidate-button scan", prefix="mode")
        result["initial"] = await _snapshot(page)
        result["candidate_buttons"] = await page.evaluate(_BUTTON_SCAN_JS)
        await page.screenshot(path=str(out_dir / "mode_00_initial.png"))

        # Systematic attempts: dismiss chat panel, then click the Agent pill,
        # re-checking classic after each — up to 4 actions (not once).
        actions = [
            ("dismiss_chat_panel", AGENT_CHAT_PANEL_CLOSE_SELECTOR),
            ("click_agent_pill", COMPOSER_AGENT_TOGGLE_SELECTOR),
            ("click_agent_pill_2", COMPOSER_AGENT_TOGGLE_SELECTOR),
            ("dismiss_chat_panel_2", AGENT_CHAT_PANEL_CLOSE_SELECTOR),
        ]
        for n, (label, sel) in enumerate(actions, 1):
            loc = page.locator(sel).first
            present = await loc.count() > 0
            entry: dict[str, Any] = {"action": label, "present": present}
            if present:
                try:
                    await loc.click(force=True, timeout=4000)
                    await page.wait_for_timeout(1500)
                    entry["clicked"] = True
                except Exception as e:  # noqa: BLE001
                    entry["clicked"] = False
                    entry["error"] = f"{type(e).__name__}: {e}"
            entry["classic_after"] = await _classic_present(page)
            result["steps"].append(entry)
            await page.screenshot(path=str(out_dir / f"mode_{n:02d}_{label}.png"))
            if entry["classic_after"]:
                result["classic_restored_by"] = label
                break

        result["final"] = await _snapshot(page)
        out_file = out_dir / "mode_switch_recon.json"
        out_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
        result["_out_file"] = str(out_file)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    args = ap.parse_args(argv)
    res = asyncio.run(_run(args.profile, args.project))
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
