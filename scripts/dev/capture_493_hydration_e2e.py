#!/usr/bin/env python3
r"""#493 live e2e — drive the PRODUCTION mode-switch immediately after
navigation, with no pre-wait, on a real editor.

This is the scenario the reporter hits: `gflow video r2v` navigates and the
`mode_switch_trigger` probe runs against a composer that may not have rendered
yet. Measured live (capture_agent_pill_dom.py): a freshly navigated editor had
**0** ``[aria-pressed]`` nodes and **0** ``crop_*`` triggers on load.

RESULT (2026-08-14): the hypothesis was REFUTED by this harness. A hydration
wait was added, then NEUTERED as an A/B control, and cold loads passed 3/3 BOTH
ways -- ``_probe_selector_cascade`` already waits up to 4s per selector for
``visible``, so it absorbs the race on its own. The wait was reverted rather
than shipped; no ``await_composer_hydrated`` exists in the codebase.

PASS = the production switch completes without raising, starting from a cold
navigation. Keep this harness to A/B any future mode-switch change.

**Credit-free:** no prompt, no Generate.

Usage:
    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\capture_493_hydration_e2e.py \
        --profile denon82 --project <id> [--loads 3]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts" / "dev"))

from _spike_common import build_client, resolve_profile_dir, step  # noqa: E402

from gflow_cli.api import routes  # noqa: E402
from gflow_cli.api.transports.mode_control import CROP_SELECTORS  # noqa: E402
from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin  # noqa: E402
from gflow_cli.errors import UiSelectorDriftError  # noqa: E402


async def _run(profile: str, project: str, loads: int) -> int:
    results: list[dict[str, Any]] = []
    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        for i in range(1, loads + 1):
            await page.goto(
                routes.project_editor_url("en", project),
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            # Deliberately NO editor-ready wait: reproduce the cold-probe race.
            crop_on_load = sum([await page.locator(s).count() for s in CROP_SELECTORS])
            pressed_on_load = await page.locator("[aria-pressed]").count()

            t0 = time.monotonic()
            try:
                await VideoGenerationMixin._switch_to_video_mode(  # noqa: SLF001
                    page, out_dir=None
                )
                outcome = "ok"
                err = ""
            except UiSelectorDriftError as exc:
                outcome = "EXIT-23 UiSelectorDriftError"
                err = str(exc)[:90]
            except Exception as exc:  # noqa: BLE001
                outcome = type(exc).__name__
                err = str(exc)[:90]
            dt = round(time.monotonic() - t0, 1)

            results.append(
                {
                    "load": i,
                    "crop_on_load": crop_on_load,
                    "aria_pressed_on_load": pressed_on_load,
                    "outcome": outcome,
                    "seconds": dt,
                }
            )
            step(
                f"load{i}",
                f"cold DOM: crop={crop_on_load} aria_pressed={pressed_on_load} "
                f"-> {outcome} ({dt}s) {err}",
            )
            # Close the popover the switch leaves open before the next load.
            await page.keyboard.press("Escape")

    ok = sum(1 for r in results if r["outcome"] == "ok")
    cold = sum(1 for r in results if r["crop_on_load"] == 0)
    print("\n=== VERDICT ===")
    print(f"  loads that started from a COLD (unhydrated) DOM : {cold}/{len(results)}")
    print(f"  production mode-switch succeeded                : {ok}/{len(results)}")
    print("  PASS" if ok == len(results) else "  FAIL — a cold probe still raises")
    return 0 if ok == len(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--loads", type=int, default=3)
    args = ap.parse_args()
    return asyncio.run(_run(args.profile, args.project, args.loads))


if __name__ == "__main__":
    raise SystemExit(main())
