#!/usr/bin/env python3
r"""#493 hypothesis 3 — the EXPANDED CHAT SIDEBAR state.

`mode_control`'s own module docstring: with ``aria-pressed="true"`` an
``expand_content`` button appears, and expanding it opens a right-side chat
sidebar **and the classic composer disappears**.

That state matches #493's fingerprint exactly and is the one state not yet
tested:

  * no ``crop_*`` settings trigger  -> ``mode_switch_trigger`` misses -> exit 23
  * no in-composer Agent pill       -> "the Agente pill matches NEITHER selector"

Recovery hinges on ``SIDEBAR_CLOSE_SELECTOR``:

    div:has(button:has(i.google-symbols:text-is('edit_square')))
    button:has(i.google-symbols:text-is('close'))

which is scoped to a div carrying an ``edit_square`` button. If a cohort's
sidebar lacks that ligature, the close button is never found, the sidebar never
closes, the composer never returns, and the run dies with exit 23 — with no
agentic indicator visible either, so it cannot even be classified as agentic
(exit 25).

This drives the composer INTO that state and reports whether production
recovers. Credit-free: toggles and DOM reads only, no prompt, no Generate.

Usage:
    PYTHONUTF8=1 .venv\Scripts\python.exe scripts\dev\capture_sidebar_state_dom.py \
        --profile denon82 --project <id>
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
from gflow_cli.api.transports import mode_control  # noqa: E402
from gflow_cli.api.transports.mode_control import (  # noqa: E402
    AGENT_TOGGLE_SELECTOR,
    CROP_SELECTORS,
)

_EXPAND_SELECTOR = "button:has(i.google-symbols:text-is('expand_content'))"


async def _wait_editor(page: Any) -> bool:
    for _ in range(30):
        if (
            await page.locator('div[role="textbox"], textarea').count() > 0
            or await page.locator("button").count() > 8
        ):
            return True
        await page.wait_for_timeout(1000)
    return False


async def _probe(page: Any) -> dict[str, Any]:
    crop = sum([await page.locator(s).count() for s in CROP_SELECTORS])
    return {
        "crop_triggers": crop,
        "agent_toggle": await page.locator(AGENT_TOGGLE_SELECTOR).count(),
        "sidebar_close": await page.locator(mode_control.SIDEBAR_CLOSE_SELECTOR).count(),
        "expand_button": await page.locator(_EXPAND_SELECTOR).count(),
        "edit_square": await page.locator("i.google-symbols:text-is('edit_square')").count(),
        "any_close": await page.locator("i.google-symbols:text-is('close')").count(),
        "mode": await mode_control.read_mode(page),
    }


async def _run(profile: str, project: str) -> int:
    out: dict[str, Any] = {"profile": profile, "project": project, "phases": {}}
    async with build_client(resolve_profile_dir(profile)) as client:
        ctx = client._context  # noqa: SLF001
        assert ctx is not None
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        await page.goto(
            routes.project_editor_url("en", project),
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        await _wait_editor(page)
        await page.wait_for_timeout(2500)

        out["phases"]["start"] = await _probe(page)
        step("start", str(out["phases"]["start"]))

        # 1. Turn Agent mode ON (only if it is currently off).
        toggle = page.locator(AGENT_TOGGLE_SELECTOR).first
        if await toggle.count() > 0 and await toggle.get_attribute("aria-pressed") == "false":
            await toggle.click(timeout=4000)
            await page.wait_for_timeout(1500)
        out["phases"]["agent_on"] = await _probe(page)
        step("agent_on", str(out["phases"]["agent_on"]))

        # 2. Expand the chat sidebar — this is the state under test.
        exp = page.locator(_EXPAND_SELECTOR).first
        expanded = False
        if await exp.count() > 0:
            try:
                await exp.click(timeout=4000)
                await page.wait_for_timeout(2000)
                expanded = True
            except Exception as e:  # noqa: BLE001
                step("expand", f"click failed: {type(e).__name__}")
        out["expanded"] = expanded
        out["phases"]["sidebar_open"] = await _probe(page)
        step("sidebar_open", str(out["phases"]["sidebar_open"]))

        # 3. Can production recover from here?
        acted = await mode_control.ensure_media_mode(page, allow_reload=True)
        await page.wait_for_timeout(1500)
        out["ensure_media_mode_acted"] = acted
        out["phases"]["after_recovery"] = await _probe(page)
        step("after_recovery", f"acted={acted} {out['phases']['after_recovery']}")

    p = default_out_path("sidebar_state_dom")
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    step("done", f"wrote {p}")

    sb = out["phases"]["sidebar_open"]
    rec = out["phases"]["after_recovery"]
    print("\n=== VERDICT ===")
    print(f"  sidebar actually opened            : {out['expanded']}")
    print(
        f"  in sidebar state: crop={sb['crop_triggers']} pill={sb['agent_toggle']}"
        f" (both 0 = the #493 fingerprint)"
    )
    print(
        f"  SIDEBAR_CLOSE_SELECTOR matched     : {sb['sidebar_close']}"
        f"   (edit_square present: {sb['edit_square']}, any close: {sb['any_close']})"
    )
    print(f"  production recovered to classic    : {rec['crop_triggers'] > 0}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument(
        "--break-scoped-selector",
        action="store_true",
        help="A/B control: simulate a cohort whose sidebar lacks edit_square by "
        "neutering the scoped SIDEBAR_CLOSE_SELECTOR, leaving only the #493 fallback.",
    )
    ap.add_argument(
        "--break-fallback-too",
        action="store_true",
        help="Negative control: also neuter the #493 fallback, which must then FAIL "
        "to recover — proving the fallback is what does the rescuing.",
    )
    args = ap.parse_args()
    if args.break_scoped_selector:
        # Reproduce the reporter's cohort: the scoped selector never matches.
        mode_control.SIDEBAR_CLOSE_SELECTOR = "button#__gflow_never_matches__"
        print("[spike] A/B: scoped SIDEBAR_CLOSE_SELECTOR neutered")
    if args.break_fallback_too:
        mode_control.SIDEBAR_CLOSE_FALLBACK_SELECTOR = "button#__gflow_never_matches_either__"
        print("[spike] A/B: #493 fallback ALSO neutered (expect NO recovery)")
    return asyncio.run(_run(args.profile, args.project))


if __name__ == "__main__":
    raise SystemExit(main())
