#!/usr/bin/env python3
r"""Mode-switch round-trip validation (#299) — drive classic -> agentic -> back,
asserting the real selectors at each step (0 credits, navigation only).

Confirmed from live DOM (2026-07-17, PT locale):
- The "Agente" toggle is `button[aria-pressed]` containing `span.content`
  (aria-pressed=false => classic, true => agent on). Locale-invariant.
- Turning it on reveals an `expand_content` button; clicking that opens the
  right chat sidebar (classic composer gone), closed via the `close` (X) button.
- `apps_spark_2` is the "Tools" nav item, NOT an agentic signal.

This maps the state machine empirically and validates the selectors the robust
ModeController will use.

Usage:
    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\spike_mode_roundtrip.py \
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

# Candidate locale-invariant selectors (to validate).
AGENT_TOGGLE = "button[aria-pressed]:has(span.content)"
EXPAND_BTN = "button:has(i.google-symbols:text-is('expand_content'))"
SIDEBAR_CLOSE = (
    "div:has(button:has(i.google-symbols:text-is('edit_square'))) "
    "button:has(i.google-symbols:text-is('close'))"
)
CROP = (
    "button[aria-haspopup='menu']:has(i.google-symbols:text-is('crop_16_9')), "
    "button[aria-haspopup='menu']:has(i.google-symbols:text-is('crop_9_16'))"
)


async def _state(page: Any) -> dict[str, Any]:
    toggle = page.locator(AGENT_TOGGLE)
    n = await toggle.count()
    pressed = None
    text = None
    if n:
        pressed = await toggle.first.get_attribute("aria-pressed")
        text = (await toggle.first.inner_text() or "").strip()[:20]
    return {
        "agent_toggle_count": n,
        "agent_pressed": pressed,
        "agent_text": text,
        "crop_present": await page.locator(CROP).count() > 0,
        "expand_present": await page.locator(EXPAND_BTN).count() > 0,
        "sidebar_close_present": await page.locator(SIDEBAR_CLOSE).count() > 0,
        "slate_present": await page.locator(
            'div[role="textbox"][data-slate-editor="true"]'
        ).count()
        > 0,
    }


async def _wait_editor(page: Any) -> bool:
    for _ in range(30):
        if (
            await page.locator('div[role="textbox"][data-slate-editor="true"]').count() > 0
            or await page.locator("button").count() > 8
        ):
            return True
        await page.wait_for_timeout(1000)
    return False


async def _run(profile: str, project: str) -> dict[str, Any]:
    out_dir = default_out_path("mode_roundtrip", ".json").parent
    result: dict[str, Any] = {"profile": profile, "project": project, "trace": []}

    async def snap(page: Any, label: str) -> dict[str, Any]:
        s = await _state(page)
        s["label"] = label
        result["trace"].append(s)
        await page.screenshot(path=str(out_dir / f"rt_{label}.png"))
        return s

    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        step("A", f"goto project {project}", prefix="rt")
        await page.goto(routes.project_editor_url("en", project), wait_until="domcontentloaded", timeout=60_000)
        result["editor_ready"] = await _wait_editor(page)
        await page.wait_for_timeout(1500)
        await snap(page, "00_initial")

        # 1. Turn Agente ON (if a toggle exists and reads not-pressed).
        toggle = page.locator(AGENT_TOGGLE).first
        if await toggle.count() > 0:
            step("B", "click Agente toggle -> agent ON", prefix="rt")
            await toggle.click(force=True, timeout=4000)
            await page.wait_for_timeout(1500)
            await snap(page, "01_agent_on")

            # 2. Expand to the sidebar, if the expand button appeared.
            exp = page.locator(EXPAND_BTN).first
            if await exp.count() > 0:
                step("C", "click expand_content -> sidebar", prefix="rt")
                await exp.click(force=True, timeout=4000)
                await page.wait_for_timeout(1500)
                await snap(page, "02_sidebar")

                # 3. Close the sidebar via X.
                x = page.locator(SIDEBAR_CLOSE).first
                if await x.count() > 0:
                    step("D", "click sidebar X (close) -> back to composer", prefix="rt")
                    await x.click(force=True, timeout=4000)
                    await page.wait_for_timeout(1500)
                    await snap(page, "03_after_close")

            # 4. Ensure classic: toggle Agente off if still pressed.
            t2 = page.locator(AGENT_TOGGLE).first
            if await t2.count() > 0 and (await t2.get_attribute("aria-pressed")) == "true":
                step("E", "toggle Agente OFF -> classic", prefix="rt")
                await t2.click(force=True, timeout=4000)
                await page.wait_for_timeout(1500)
            await snap(page, "04_final_classic")

        out_file = out_dir / "mode_roundtrip.json"
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
