"""Spike: is "agent-only" just the agent sidebar, and where does "Leaving 5 Dec" come from?

Two questions, one $0 browser session (navigation, clicks on layout controls, DOM and
network reads; nothing is submitted, no credits, no quota).

**Q1 — the agent sidebar.** gflow calls a page "agent-only" (#799) when the settings trigger
is hidden and no `button.agent-mode-chip` exists. A user report (2026-11-19, screenshots)
shows the agent chat docked as a right-hand sidebar with a Close (X) button; closing it
reveals the bottom composer with the Agent chip and an expand-arrows control, and turning
the chip off shows the classic composer. The probe snapshots the composer before and after
each step: S0 as loaded, S1 after clicking the sidebar's close button, S2 after clicking a
pressed Agent chip, S3 after a reload, then asks gflow's own `ensure_editor` for a verdict.

Pre-registered readings:

| Outcome | Reading |
|---|---|
| S0 no chip + sidebar; S1 chip appears; S2 trigger visible; S3 still classic | "agent-only" is the sidebar state, recoverable in two clicks, and the recovery persists |
| S1 chip appears but S3 reloads back to no chip | recoverable per load only; gflow must recover on every run |
| S1 no chip, no composer | a real agent-only cohort on this account; the sidebar is not the explanation |
| S0 already classic | does not reproduce on this account now; settles nothing |

**Q2 — model retirement.** The video model menu labels Omni 1.1 Flash, Veo 3.1 Fast and Veo
3.1 Quality "Leaving 5 Dec". The probe records every `batchexecute` reply during the load
and while the model menu is open, searches each for the model names and for date-like
values, and dumps the menu items' DOM structure.

| Outcome | Reading |
|---|---|
| a reply carries a date/timestamp/status next to the leaving models only | the retirement is data; map it into the model catalog |
| only the menu DOM carries it (a separate element/class per item) | rendered text only; map it from the DOM or document it |
| neither | unmeasured; capture a HAR by hand (scripts/dev/har-spike) |

    uv run python scripts/dev/spike_agent_sidebar_and_model_retirement.py \
        --profile NAME --project UUID [--cycle]

**`--cycle`** reproduces Q1 from a classic page and restores it: C1 turn the Agent chip on,
C2 click the composer's expand control (the ligature that appears only in agent mode), C3
reload and ask `ensure_editor` for a verdict, then recover — C4 close the sidebar, C5 turn
the chip off, C6 reload and ask again. Pre-registered: C3 verdict `agent_only` with C4
revealing a pressed chip means gflow misclassifies the sidebar; C6 `classic` means the
two-click recovery restores the account. The `finally` block always attempts the recovery,
so the account is not left in agent mode by this script.

Evidence goes to `scripts/dev/_spike_out/agent_sidebar_<ts>/` (gitignored: replies carry
account data). The script prints a redacted summary only.
"""

# ruff: noqa: E501 - pre-registered reading tables and inline JS keep their shape

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from gflow_cli.api.transports._mc_common import (
    AGENT_MODE_CHIP,
    AGENT_MODE_CHIP_ANY,
    AGENT_ONLY_ANCHOR,
    MIGRATED_PROJECT_URL,
    READY_ANCHOR,
)
from gflow_cli.api.transports.batchexecute import parse_frames
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import get_settings

OUT_ROOT = Path(__file__).parent / "_spike_out"
SIDEBAR_CLOSE = "flow-agent-panel button:has(mat-icon:text-is('close'))"
MODEL_NAMES = ("Omni 1.2", "Omni 1.1", "Veo 3.1", "veo_3_1", "omni")
DATE_RE = re.compile(r"(?:\b5 Dec\b|\bDec(?:ember)? 5\b|2026-12-0[45]|179[67]\d{6})")

#: Structure of the composer region: tags, ligatures, chip state, visibility. No text.
SNAPSHOT_JS = r"""
() => {
  const vis = (e) => !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const q = (s) => document.querySelector(s);
  const chip = q('button.agent-mode-chip');
  const trigger = q('.settings-trigger-button');
  const panel = q('flow-agent-panel');
  const ligs = (root) => root ? [...root.querySelectorAll('mat-icon')]
      .map(i => (i.textContent || '').trim()).filter(Boolean) : [];
  const box = q('flow-creative-agent-prompt-box') || q('flow-prompt-box');
  return {
    chip: chip ? {visible: vis(chip), pressed: chip.getAttribute('aria-pressed')} : null,
    trigger: trigger ? {visible: vis(trigger), hidden_attr: trigger.hasAttribute('hidden')} : null,
    agent_panel: panel ? {visible: vis(panel), ligatures: ligs(panel).slice(0, 20)} : null,
    agent_prompt_box: !!q('flow-creative-agent-prompt-box'),
    classic_prompt_box: !!q('flow-prompt-box'),
    composer_ligatures: ligs(box),
    composer_tags: box ? [...new Set([...box.querySelectorAll('*')].map(e => e.tagName.toLowerCase())
      .filter(t => t.includes('-')))].slice(0, 30) : [],
  };
}
"""

MENU_JS = r"""
() => [...document.querySelectorAll("[role='menuitem']")].map(it => ({
  classes: it.className,
  children: [...it.querySelectorAll('*')].map(e => ({tag: e.tagName.toLowerCase(),
     cls: (e.className && e.className.baseVal === undefined) ? String(e.className) : '',
     text: (e.children.length ? '' : (e.textContent || '').trim()).slice(0, 40)})),
}))
"""


async def main(profile: str, project: str) -> None:
    out = OUT_ROOT / f"agent_sidebar_{time.strftime('%Y%m%dT%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    phase = {"name": "load"}

    async def on_response(resp: Any) -> None:
        url = str(getattr(resp, "url", ""))
        if "batchexecute" not in url:
            return
        try:
            text = await resp.text()
        except Exception:  # noqa: BLE001 - a lost body is recorded as missing
            frames.append({"phase": phase["name"], "url_rpcids": url, "body": None})
            return
        for rid, payload in parse_frames(text):
            frames.append({"phase": phase["name"], "rpcid": rid, "payload": payload})

    transport = UiAutomationTransport()
    await transport.setup(get_settings().profile_subdir(profile))
    snaps: dict[str, Any] = {}
    try:
        page = transport._page  # noqa: SLF001 - a spike reads the live page
        assert page is not None
        page.on("response", on_response)
        await page.goto(
            MIGRATED_PROJECT_URL.format(project_id=project), wait_until="domcontentloaded"
        )
        await page.locator(
            f"{AGENT_MODE_CHIP_ANY}, {READY_ANCHOR}, {AGENT_ONLY_ANCHOR}"
        ).first.wait_for(state="attached", timeout=45_000)
        await page.wait_for_timeout(4000)
        snaps["S0_loaded"] = await page.evaluate(SNAPSHOT_JS)

        close = page.locator(SIDEBAR_CLOSE).first
        snaps["S1_close_present"] = bool(await close.count())
        if snaps["S1_close_present"]:
            await close.click(timeout=5000)
            await page.wait_for_timeout(2500)
        snaps["S1_after_close"] = await page.evaluate(SNAPSHOT_JS)

        chip = page.locator(AGENT_MODE_CHIP).first
        snaps["S2_chip_was_pressed"] = bool(await chip.count())
        if snaps["S2_chip_was_pressed"]:
            await chip.click(timeout=5000)
            await page.wait_for_timeout(2500)
        snaps["S2_after_chip"] = await page.evaluate(SNAPSHOT_JS)

        phase["name"] = "reload"
        await page.reload(wait_until="domcontentloaded")
        await page.locator(
            f"{AGENT_MODE_CHIP_ANY}, {READY_ANCHOR}, {AGENT_ONLY_ANCHOR}"
        ).first.wait_for(state="attached", timeout=45_000)
        await page.wait_for_timeout(4000)
        snaps["S3_after_reload"] = await page.evaluate(SNAPSHOT_JS)
        snaps["S4_gflow_verdict"] = await MigratedComposer().ensure_editor(
            page, project, timeout_s=45.0
        )

        # Q2: open the settings pane and the video model menu on the classic composer.
        phase["name"] = "model_menu"
        snaps["Q2_menu"] = None
        trigger = page.locator(READY_ANCHOR).first
        if await trigger.count() and await trigger.is_visible():
            await trigger.click(timeout=5000)
            await page.wait_for_timeout(1500)
            picker = page.locator(
                ".cdk-overlay-pane button:has(mat-icon:text-is('arrow_drop_down'))"
            )
            snaps["Q2_pickers"] = await picker.count()
            for i in range(await picker.count()):
                await picker.nth(i).click(timeout=5000)
                await page.wait_for_timeout(1500)
                items = await page.evaluate(MENU_JS)
                if items:
                    snaps.setdefault("Q2_menus", []).append(items)
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(500)
            await page.keyboard.press("Escape")
    finally:
        await transport.teardown()

    (out / "snapshots.json").write_text(json.dumps(snaps, indent=1, default=str), encoding="utf-8")
    (out / "frames.json").write_text(json.dumps(frames, default=str), encoding="utf-8")

    # Redacted summary: structure and booleans only.
    for key in ("S0_loaded", "S1_after_close", "S2_after_chip", "S3_after_reload"):
        s = snaps.get(key) or {}
        print(
            f"{key}: chip={s.get('chip')} trigger={s.get('trigger')} "
            f"panel_visible={(s.get('agent_panel') or {}).get('visible')} "
            f"agent_box={s.get('agent_prompt_box')} classic_box={s.get('classic_prompt_box')} "
            f"composer_ligatures={s.get('composer_ligatures')}"
        )
    print(
        "close_present:",
        snaps.get("S1_close_present"),
        "chip_was_pressed:",
        snaps.get("S2_chip_was_pressed"),
    )
    print("gflow verdict after reload:", snaps.get("S4_gflow_verdict"))
    print(
        "pickers in pane:",
        snaps.get("Q2_pickers"),
        "menus captured:",
        len(snaps.get("Q2_menus") or []),
    )
    hits = []
    for f in frames:
        blob = json.dumps(f.get("payload"), default=str)
        if any(m in blob for m in MODEL_NAMES):
            dates = sorted(set(DATE_RE.findall(blob)))
            hits.append((f["phase"], f.get("rpcid"), len(blob), dates))
    print(
        "batchexecute frames:",
        len(frames),
        "naming a model:",
        [(p, r, n, d) for p, r, n, d in hits],
    )
    print("evidence:", out)


async def _settle(page: Any) -> None:
    await page.locator(
        f"{AGENT_MODE_CHIP_ANY}, {READY_ANCHOR}, {AGENT_ONLY_ANCHOR}"
    ).first.wait_for(state="attached", timeout=45_000)
    await page.wait_for_timeout(4000)


async def _recover(page: Any, snaps: dict[str, Any], tag: str) -> None:
    """Close the agent sidebar, then turn a pressed Agent chip off."""
    close = page.locator(SIDEBAR_CLOSE).first
    snaps[f"{tag}_close_present"] = bool(await close.count())
    if snaps[f"{tag}_close_present"]:
        await close.click(timeout=5000)
        await page.wait_for_timeout(2500)
    snaps[f"{tag}_after_close"] = await page.evaluate(SNAPSHOT_JS)
    chip = page.locator(AGENT_MODE_CHIP).first
    snaps[f"{tag}_chip_was_pressed"] = bool(await chip.count())
    if snaps[f"{tag}_chip_was_pressed"]:
        await chip.click(timeout=5000)
        await page.wait_for_timeout(2500)
    snaps[f"{tag}_after_chip_off"] = await page.evaluate(SNAPSHOT_JS)


async def cycle(profile: str, project: str) -> None:
    out = OUT_ROOT / f"agent_sidebar_cycle_{time.strftime('%Y%m%dT%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    transport = UiAutomationTransport()
    await transport.setup(get_settings().profile_subdir(profile))
    snaps: dict[str, Any] = {}
    page = transport._page  # noqa: SLF001 - a spike reads the live page
    assert page is not None
    try:
        await page.goto(
            MIGRATED_PROJECT_URL.format(project_id=project), wait_until="domcontentloaded"
        )
        await _settle(page)
        snaps["C0_loaded"] = await page.evaluate(SNAPSHOT_JS)
        chip = page.locator(AGENT_MODE_CHIP_ANY).first
        if await chip.count() and await chip.get_attribute("aria-pressed") != "true":
            await chip.click(timeout=5000)
            await page.wait_for_timeout(2500)
        snaps["C1_agent_on"] = await page.evaluate(SNAPSHOT_JS)
        before = set(snaps["C0_loaded"]["composer_ligatures"])
        new_ligs = [lig for lig in snaps["C1_agent_on"]["composer_ligatures"] if lig not in before]
        snaps["C2_expand_ligatures_tried"] = new_ligs
        for lig in new_ligs:
            btn = page.locator(
                f"flow-creative-agent-prompt-box button:has(mat-icon:text-is('{lig}'))"
            ).first
            if (
                await btn.count()
                and await btn.is_visible()
                and lig not in ("tune", "add", "arrow_forward")
            ):
                await btn.click(timeout=5000)
                await page.wait_for_timeout(2500)
                snaps["C2_clicked"] = lig
                break
        snaps["C2_after_expand"] = await page.evaluate(SNAPSHOT_JS)
        await page.reload(wait_until="domcontentloaded")
        await _settle(page)
        snaps["C3_after_reload"] = await page.evaluate(SNAPSHOT_JS)
        # The verdict gflow reaches today, BEFORE any recovery of ours.
        snaps["C3_gflow_verdict"] = await MigratedComposer().ensure_editor(
            page, project, timeout_s=45.0
        )
    finally:
        try:
            await _recover(page, snaps, "C4C5")
            await page.reload(wait_until="domcontentloaded")
            await _settle(page)
            snaps["C6_after_reload"] = await page.evaluate(SNAPSHOT_JS)
            snaps["C6_gflow_verdict"] = await MigratedComposer().ensure_editor(
                page, project, timeout_s=45.0
            )
        finally:
            await transport.teardown()
    (out / "snapshots.json").write_text(json.dumps(snaps, indent=1, default=str), encoding="utf-8")
    for key in (
        "C0_loaded",
        "C1_agent_on",
        "C2_after_expand",
        "C3_after_reload",
        "C4C5_after_close",
        "C4C5_after_chip_off",
        "C6_after_reload",
    ):
        s = snaps.get(key) or {}
        print(
            f"{key}: chip={s.get('chip')} trigger={s.get('trigger')} "
            f"panel_visible={(s.get('agent_panel') or {}).get('visible')} "
            f"agent_box={s.get('agent_prompt_box')} classic_box={s.get('classic_prompt_box')} "
            f"ligs={s.get('composer_ligatures')}"
        )
    print(
        "expand ligatures tried:",
        snaps.get("C2_expand_ligatures_tried"),
        "clicked:",
        snaps.get("C2_clicked"),
    )
    print("C3 gflow verdict (no recovery):", snaps.get("C3_gflow_verdict"))
    print(
        "recovery: close_present",
        snaps.get("C4C5_close_present"),
        "chip_was_pressed",
        snaps.get("C4C5_chip_was_pressed"),
    )
    print("C6 gflow verdict after recovery + reload:", snaps.get("C6_gflow_verdict"))
    print("evidence:", out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="#799/#1354: agent sidebar and model retirement")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--cycle", action="store_true", help="reproduce the sidebar state, then restore"
    )
    args = parser.parse_args()
    asyncio.run(
        cycle(args.profile, args.project) if args.cycle else main(args.profile, args.project)
    )
