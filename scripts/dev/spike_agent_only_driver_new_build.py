"""Spike: can the agent-only driver reach the agent chat on Flow's 2026-11-20 build? (#1369)

#1369 reports that the agent-only composer's chat answers the previous request. That can
only happen after the driver (`agent_only_composer.py`) has sent a request. The driver's
first step is `_open_pane`, which clicks `SETTINGS_BUTTON` (= `AGENT_ONLY_ANCHOR`, the
`tune` button inside `flow-creative-agent-prompt-box`) to set the run's defaults in Agent
settings. The 2026-11-20 spike recorded no `tune` ligature in any agent state of the new
build (`docs/superpowers/spikes/2026-11-20-agent-mode-switch-and-panel-rendering.md`).

This probe puts a classic account into agent mode, then calls the driver's own `_open_pane`
twice: first with the agent panel closed, then with it open. Then it restores the classic
composer. Nothing is typed or submitted.

Cost: $0. It makes layout clicks only; the driver's pane step never submits. The account's
remembered agent mode is restored in `finally`.

Pre-registered readings (written before the first run):

| Observation | Reading |
|---|---|
| `_open_pane` raises "Agent settings button (...) missing" in both states | the driver cannot run on the new build; #1369 cannot occur there until #1387 ports the settings anchor |
| `_open_pane` opens the pane in either state | the driver can run; #1369 needs its own back-to-back reproduction |
| agent mode cannot be turned on | settles nothing; the probe could not reach the state |

    uv run python scripts/dev/spike_agent_only_driver_new_build.py --profile NAME --project UUID
"""

# ruff: noqa: E501 - the pre-registered reading table keeps its shape

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from gflow_cli.api.transports._mc_common import MIGRATED_PROJECT_URL
from gflow_cli.api.transports.agent_only_composer import SETTINGS_BUTTON, AgentOnlyComposer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import get_settings

OUT_ROOT = Path(__file__).parent / "_spike_out"
TOGGLE = "button.agent-mode-chip, flow-agent-mode-switch button.switch"
PRESSED = (
    "button.agent-mode-chip[aria-pressed='true'], "
    "flow-agent-mode-switch button.switch[aria-pressed='true']"
)
TRIGGER = ".settings-trigger-button"
LIGATURES_JS = """() => [...new Set([...document.querySelectorAll('.google-symbols')]
    .map(e => (e.textContent || '').trim()).filter(Boolean))].sort()"""


async def _open_pane_verdict(page: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "settings_button_count": await page.locator(SETTINGS_BUTTON).count(),
        "ligatures": await page.evaluate(LIGATURES_JS),
    }
    try:
        await AgentOnlyComposer()._open_pane(page)  # noqa: SLF001 - the driver's own first step
        out["open_pane"] = "opened"
        await page.keyboard.press("Escape")
    except Exception as exc:  # noqa: BLE001 - the class and detail are the observation
        out["open_pane"] = f"raised {type(exc).__name__}: {str(exc)[:160]}"
    return out


async def _restore(page: Any) -> bool:
    for ligature in ("right_panel_close", "close"):
        close = page.locator(f"flow-agent-panel button:has(mat-icon:text-is('{ligature}'))").first
        if await close.count() and await close.is_visible():
            await close.click(timeout=10_000)
            await page.wait_for_timeout(2500)
            break
    pressed = page.locator(PRESSED).first
    if await pressed.count():
        await pressed.click(timeout=10_000)
        await page.wait_for_timeout(2500)
    return bool(await page.locator(TRIGGER).first.is_visible())


async def main(profile: str, project: str) -> None:
    out = OUT_ROOT / f"agent_only_driver_{time.strftime('%Y%m%dT%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    snaps: dict[str, Any] = {}
    transport = UiAutomationTransport()
    await transport.setup(get_settings().profile_subdir(profile))
    page = transport._page  # noqa: SLF001 - a spike reads the live page
    assert page is not None
    try:
        await page.goto(
            MIGRATED_PROJECT_URL.format(project_id=project), wait_until="domcontentloaded"
        )
        await page.locator(f"{TOGGLE}, {TRIGGER}").first.wait_for(state="attached", timeout=45_000)
        await page.wait_for_timeout(3000)
        snaps["start_classic"] = await _restore(page)
        toggle = page.locator(TOGGLE).first
        await toggle.click(timeout=10_000)
        await page.locator(TRIGGER).first.wait_for(state="hidden", timeout=15_000)
        await page.wait_for_timeout(2500)
        snaps["agent_mode_panel_closed"] = await _open_pane_verdict(page)
        expand = page.locator(
            "flow-creative-agent-prompt-box button:has(mat-icon:text-is('right_panel_open')), "
            "flow-creative-agent-prompt-box button:has(mat-icon:text-is('expand_content'))"
        ).first
        if await expand.count():
            await expand.click(timeout=10_000)
            await page.locator("flow-agent-panel").first.wait_for(state="visible", timeout=15_000)
            await page.wait_for_timeout(2500)
            snaps["agent_mode_panel_open"] = await _open_pane_verdict(page)
    finally:
        try:
            snaps["restored_classic"] = await _restore(page)
        finally:
            (out / "snapshots.json").write_text(json.dumps(snaps, indent=1), encoding="utf-8")
            await transport.teardown()
    for key, value in snaps.items():
        if isinstance(value, dict):
            print(
                f"{key}: settings_button_count={value['settings_button_count']} open_pane={value['open_pane']}"
            )
            print(f"    ligatures={value['ligatures']}")
        else:
            print(f"{key}: {value}")
    print("evidence:", out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="#1369: agent-only driver on the new build")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.profile, args.project))
