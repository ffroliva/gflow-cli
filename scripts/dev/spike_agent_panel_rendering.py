"""Spike: which agent-panel rendering does this account get, and how is its close button built?

#1374 measured the expanded agent panel on 2026-11-19 (one profile, en-GB). It carried the
ligatures `menu`, `edit_square`, `close`, `add`, `article_spark`, `tune` and `arrow_forward`,
and its `close` button brought the Agent chip back. #1384 (2026-11-20, macOS, an Ultra
account) reports a page with none of `close`, `tune`, `edit_square`, `article_spark` or `menu`.
It has `right_panel_close`, `forum` and `settings_2` instead, and the agent settings gear sits
in the panel header. gflow pins its panel close to `close` and its agent-only check to `tune`
inside `flow-creative-agent-prompt-box`, so on that page the readiness gate ends as drift
(exit 23).

This probe answers three questions for the fix:

1. **Rendering.** Which panel does this account get today? The 2026-11-19 one ("C"), or
   #1384's ("D")?
2. **Structure.** For every button in `flow-agent-panel`: its ligature, the custom elements
   between it and the panel, its vertical position in the panel (header or footer), and
   whether it has an `aria-label`. Also whether the agent prompt box exists and which
   ligatures its buttons carry. No text is recorded; anchors must stay locale-invariant
   (AGENTS.md).
3. **Recovery.** Does closing the panel (with whichever of `close` or `right_panel_close` is
   present) bring a pressed Agent chip back, and does turning the chip off restore the classic
   composer across a reload?

Cost: $0. It navigates, clicks layout controls (the Agent chip, the expand control, the panel
close) and reads the DOM. Nothing is submitted. The `--cycle` mode changes the account's
remembered composer state and its `finally` block restores it. If the restore step finds no
close button, the probe says so and the account must be restored by hand.

Pre-registered readings (written before the first run):

| Observation | Reading |
|---|---|
| C2/C3 panel ligatures include `close` and `tune` | rendering C on this account; #1384's rendering does not reproduce here, so a fix for it can only be verified by the reporter (`Refs #1384`) |
| C2/C3 panel ligatures include `right_panel_close`, no `tune` | rendering D reproduces here; the recorded header structure is the anchor for the fix, and an e2e test can drive it |
| C3 verdict `agent_only` | the gate misreads the panel as #799's cohort (#1374) |
| C3 verdict raises `UiSelectorDriftError` | the gate reports drift on this rendering (#1384) |
| after the close: a pressed chip appears | the panel state is recoverable on this rendering |
| after the close: no chip, trigger still hidden | possible no-classic cohort on this rendering; do not claim it from one run |
| no close button matched in the restore step | STOP: restore the account by hand, and record the panel structure as the finding |
| a single run | one observation per profile; it settles the question for that profile on that day only |

Added 2026-11-20 after the first observe run, before any `--cycle` run: the classic page on
ffroliva had no `button.agent-mode-chip`. Its Agent toggle is now
`flow-agent-mode-switch > button.switch[aria-pressed]` carrying `spark_off`. The cycle drives
whichever toggle exists (`AGENT_TOGGLE`).

| Observation | Reading |
|---|---|
| switch pressed after C1, its ligature changes (e.g. `spark`) | the switch is the agent-mode toggle; gflow's `AGENT_MODE_CHIP` no longer matches it, so #749's recovery is dead on this build |
| C1 opens the panel directly (no expand control needed) | the new build has no compact agent composer; agent mode on = panel |
| after the C4 close, the switch is present and pressed | closing the panel is recoverable, and the switch is the second click |
| C6 classic after reload | the two-step recovery persists |

    uv run python scripts/dev/spike_agent_panel_rendering.py --profile NAME --project UUID [--cycle]

Without `--cycle` it only loads the project and records the state it finds: no clicks.

Evidence goes to `scripts/dev/_spike_out/agent_panel_<ts>/` (gitignored). The script prints a
summary of structure and booleans only.
"""

# ruff: noqa: E501 - pre-registered reading table and inline JS keep their shape

from __future__ import annotations

import argparse
import asyncio
import json
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
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import get_settings

OUT_ROOT = Path(__file__).parent / "_spike_out"
#: Both renderings' close ligatures, in the order the restore step tries them.
PANEL_CLOSE_LIGATURES = ("close", "right_panel_close")
#: Ligatures in the agent prompt box that are not the expand control.
NOT_EXPAND = ("tune", "add", "arrow_forward", "settings_2")
#: The Agent toggle in either build: the 2026-11-19 chip, or the 2026-11-20 switch.
AGENT_TOGGLE = f"{AGENT_MODE_CHIP_ANY}, flow-agent-mode-switch button"
AGENT_TOGGLE_PRESSED = f"{AGENT_MODE_CHIP}, flow-agent-mode-switch button[aria-pressed='true']"
SETTLE = f"{AGENT_TOGGLE}, {READY_ANCHOR}, {AGENT_ONLY_ANCHOR}, flow-agent-panel"

#: Structure only: tags, ligatures, attributes' presence, geometry. No text, no labels.
SNAPSHOT_JS = r"""
() => {
  const vis = (e) => !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const q = (s) => document.querySelector(s);
  const lig = (e) => [...e.querySelectorAll('mat-icon, .google-symbols')]
      .map(i => (i.textContent || '').trim()).filter(Boolean);
  const chip = q('button.agent-mode-chip') || q('flow-agent-mode-switch button');
  const trigger = q('.settings-trigger-button');
  const panel = q('flow-agent-panel');
  const agentBox = q('flow-creative-agent-prompt-box');
  const chain = (el, stop) => {
    const out = [];
    for (let p = el.parentElement; p && p !== stop; p = p.parentElement) {
      const t = p.tagName.toLowerCase();
      if (t.includes('-')) out.push(t);
    }
    return out.reverse();
  };
  let panelInfo = null;
  if (panel) {
    const pr = panel.getBoundingClientRect();
    panelInfo = {
      visible: vis(panel),
      rect: {x: Math.round(pr.x), y: Math.round(pr.y), w: Math.round(pr.width), h: Math.round(pr.height)},
      custom_tags: [...new Set([...panel.querySelectorAll('*')].map(e => e.tagName.toLowerCase())
          .filter(t => t.includes('-')))].slice(0, 60),
      buttons: [...panel.querySelectorAll('button')].map(b => {
        const r = b.getBoundingClientRect();
        return {
          ligatures: lig(b),
          chain: chain(b, panel),
          visible: vis(b),
          rel_y: pr.height ? Math.round(((r.y - pr.y) / pr.height) * 100) : null,
          has_aria_label: b.hasAttribute('aria-label'),
          has_aria_pressed: b.hasAttribute('aria-pressed'),
          has_aria_expanded: b.hasAttribute('aria-expanded'),
          attrs: [...b.attributes].map(a => a.name)
              .filter(n => !n.startsWith('_ng') && n !== 'class' && n !== 'style').slice(0, 20),
        };
      }),
    };
  }
  const syms = [...document.querySelectorAll('.google-symbols')]
      .map(e => (e.textContent || '').trim()).filter(Boolean);
  // Every control carrying a ligature that could be the agent toggle, the panel close or
  // the agent settings gear, wherever it sits on the page.
  const WATCH = ['spark', 'spark_off', 'expand_content', 'right_panel_close', 'close',
                 'settings_2', 'tune', 'forum'];
  const watched = [...document.querySelectorAll('mat-icon, .google-symbols')]
      .filter(i => WATCH.includes((i.textContent || '').trim()))
      .map(i => {
        const host = i.closest('button, [role="button"], [role="switch"], [role="tab"], a') || i;
        return {
          ligature: (i.textContent || '').trim(),
          host_tag: host.tagName.toLowerCase(),
          chain: chain(host, document.body).slice(-6),
          classes: String(host.className || '').split(/\s+/).filter(c => c && !c.startsWith('mat-mdc') && !c.startsWith('mdc-')).slice(0, 12),
          attrs: [...host.attributes].map(a => a.name)
              .filter(n => !n.startsWith('_ng') && n !== 'class' && n !== 'style').slice(0, 20),
          aria_pressed: host.getAttribute('aria-pressed'),
          aria_checked: host.getAttribute('aria-checked'),
          visible: vis(host),
          host_ligatures: lig(host),
        };
      });
  return {
    watched,
    chip: chip ? {visible: vis(chip), pressed: chip.getAttribute('aria-pressed')} : null,
    trigger: trigger ? {visible: vis(trigger), hidden_attr: trigger.hasAttribute('hidden')} : null,
    agent_panel: panelInfo,
    agent_prompt_box: agentBox ? {
      visible: vis(agentBox),
      inside_panel: !!(panel && panel.contains(agentBox)),
      button_ligatures: [...agentBox.querySelectorAll('button')].map(lig),
    } : null,
    classic_prompt_box: !!q('flow-prompt-box'),
    page_ligatures: [...new Set(syms)].sort(),
  };
}
"""


async def _settle(page: Any) -> None:
    await page.locator(SETTLE).first.wait_for(state="attached", timeout=45_000)
    await page.wait_for_timeout(4000)


async def _verdict(page: Any, project: str) -> str:
    """gflow's own reading of the page, or the exception class it raises."""
    try:
        return str(await MigratedComposer().ensure_editor(page, project, timeout_s=45.0))
    except Exception as exc:  # noqa: BLE001 - the class IS the observation
        return f"raised {type(exc).__name__}"


async def _close_panel(page: Any, snaps: dict[str, Any], tag: str) -> None:
    """Close the agent panel with whichever close ligature it carries; record which."""
    snaps[f"{tag}_close_matched"] = None
    for name in PANEL_CLOSE_LIGATURES:
        btn = page.locator(f"flow-agent-panel button:has(mat-icon:text-is('{name}'))").first
        if await btn.count() and await btn.is_visible():
            await btn.click(timeout=5000)
            await page.wait_for_timeout(2500)
            snaps[f"{tag}_close_matched"] = name
            break
    snaps[f"{tag}_after_close"] = await page.evaluate(SNAPSHOT_JS)


async def _chip_off(page: Any, snaps: dict[str, Any], tag: str) -> None:
    chip = page.locator(AGENT_TOGGLE_PRESSED).first
    snaps[f"{tag}_chip_was_pressed"] = bool(await chip.count())
    if snaps[f"{tag}_chip_was_pressed"]:
        await chip.click(timeout=5000)
        await page.wait_for_timeout(2500)
    snaps[f"{tag}_after_chip_off"] = await page.evaluate(SNAPSHOT_JS)


async def observe(page: Any, project: str, snaps: dict[str, Any]) -> None:
    await page.goto(MIGRATED_PROJECT_URL.format(project_id=project), wait_until="domcontentloaded")
    await _settle(page)
    snaps["O0_loaded"] = await page.evaluate(SNAPSHOT_JS)


async def cycle(page: Any, project: str, snaps: dict[str, Any]) -> None:
    try:
        await observe(page, project, snaps)
        chip = page.locator(AGENT_TOGGLE).first
        if await chip.count() and await chip.get_attribute("aria-pressed") != "true":
            await chip.click(timeout=5000)
            await page.wait_for_timeout(2500)
        snaps["C1_agent_on"] = await page.evaluate(SNAPSHOT_JS)
        box = snaps["C1_agent_on"].get("agent_prompt_box") or {}
        before = set(snaps["O0_loaded"]["page_ligatures"])
        tried = [
            lig
            for ligs in box.get("button_ligatures", [])
            for lig in ligs
            if lig not in NOT_EXPAND and lig not in before
        ]
        snaps["C2_expand_ligatures_tried"] = tried
        for lig in tried:
            btn = page.locator(
                f"flow-creative-agent-prompt-box button:has(mat-icon:text-is('{lig}'))"
            ).first
            if await btn.count() and await btn.is_visible():
                await btn.click(timeout=5000)
                await page.wait_for_timeout(2500)
                snaps["C2_clicked"] = lig
                break
        snaps["C2_after_expand"] = await page.evaluate(SNAPSHOT_JS)
        await page.reload(wait_until="domcontentloaded")
        await _settle(page)
        snaps["C3_after_reload"] = await page.evaluate(SNAPSHOT_JS)
        # The verdict gflow reaches today, before any recovery of ours.
        snaps["C3_gflow_verdict"] = await _verdict(page, project)
    finally:
        await _close_panel(page, snaps, "C4")
        await _chip_off(page, snaps, "C5")
        await page.reload(wait_until="domcontentloaded")
        await _settle(page)
        snaps["C6_after_reload"] = await page.evaluate(SNAPSHOT_JS)
        snaps["C6_gflow_verdict"] = await _verdict(page, project)


def _summary(snaps: dict[str, Any]) -> None:
    for key, s in snaps.items():
        if not isinstance(s, dict):
            print(f"{key}: {s}")
            continue
        panel = s.get("agent_panel") or {}
        print(
            f"{key}: chip={s.get('chip')} trigger={s.get('trigger')} "
            f"panel_visible={panel.get('visible')} agent_box={s.get('agent_prompt_box')} "
            f"classic_box={s.get('classic_prompt_box')}"
        )
        for w in s.get("watched", []):
            print(
                f"    watched {w['ligature']}: {w['host_tag']} chain={w['chain']} "
                f"classes={w['classes']} attrs={w['attrs']} pressed={w['aria_pressed']} "
                f"checked={w['aria_checked']} visible={w['visible']} ligs={w['host_ligatures']}"
            )
        for b in panel.get("buttons", []):
            print(
                f"    panel button y={b['rel_y']}% ligs={b['ligatures']} chain={b['chain']} "
                f"visible={b['visible']} attrs={b['attrs']}"
            )


async def main(profile: str, project: str, do_cycle: bool) -> None:
    out = OUT_ROOT / f"agent_panel_{time.strftime('%Y%m%dT%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    transport = UiAutomationTransport()
    await transport.setup(get_settings().profile_subdir(profile))
    snaps: dict[str, Any] = {"mode": "cycle" if do_cycle else "observe"}
    page = transport._page  # noqa: SLF001 - a spike reads the live page
    assert page is not None
    try:
        await (cycle if do_cycle else observe)(page, project, snaps)
    finally:
        try:
            (out / "snapshots.json").write_text(
                json.dumps(snaps, indent=1, default=str), encoding="utf-8"
            )
        finally:
            await transport.teardown()
    _summary(snaps)
    print("evidence:", out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="#1374/#1384: agent panel rendering")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--cycle", action="store_true", help="open the panel, then restore")
    args = parser.parse_args()
    asyncio.run(main(args.profile, args.project, args.cycle))
