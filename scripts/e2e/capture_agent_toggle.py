"""Capture the Agent-toggle + expand-button selectors that force the Agentic UI.

The Agentic UI is normally a server-assigned A/B cohort that can't be forced
(docs/AGENT_UI_RECON.md). But the *classic* composer also exposes an in-input
"Agent" toggle and an expand button that, when clicked, switch the composer into
the same agentic chat layout (Slate box + ``tune`` settings + sidebar) — a
deterministic way in.

This script enters a scratch project on a logged-in profile, dumps the composer
buttons so the toggle/expand selectors can be identified, clicks the known agent
toggle, then re-dumps and reports whether the agentic indicators (``tune`` /
Slate / sidebar) appeared and the classic ``crop_*`` trigger disappeared.

Run:
    $env:PYTHONUTF8=1
    uv run python scripts/e2e/capture_agent_toggle.py --profile denon82
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import COMPOSER_AGENT_TOGGLE_SELECTOR

_DUMP_BUTTONS_JS = """() => Array.from(document.querySelectorAll('button')).map(b => ({
  text: (b.innerText || '').trim().slice(0, 40),
  aria: b.getAttribute('aria-label') || '',
  icons: Array.from(b.querySelectorAll('i')).map(i => (i.textContent || '').trim()),
  state: b.getAttribute('data-state') || '',
  cls: (b.className || '').toString().slice(0, 90),
})).filter(b => b.text || b.aria || b.icons.length)"""

_SIGNALS_JS = """() => {
  const lig = (t) => Array.from(document.querySelectorAll('i.google-symbols'))
      .some(e => (e.textContent || '').trim() === t);
  const cropPresent = Array.from(document.querySelectorAll('button i.google-symbols'))
      .some(e => (e.textContent || '').trim().startsWith('crop_'));
  return {
    cropPresent,
    tune: lig('tune'),
    slate: !!document.querySelector('div[role="textbox"][data-slate-editor="true"]'),
    placeholder: (document.body.innerText || '').includes('What do you want to create'),
  };
}"""


def _resolve_profile_dir(profile: str) -> Path:
    base = Path.home() / "AppData" / "Local" / "ffroliva" / "gflow-cli" / f"profile_{profile}"
    if not base.exists():
        msg = f"profile dir not found: {base}"
        raise SystemExit(msg)
    return base


async def _dump(page: Any, label: str) -> None:
    signals = await page.evaluate(_SIGNALS_JS)
    buttons = await page.evaluate(_DUMP_BUTTONS_JS)
    print(f"\n===== {label} =====")
    print("signals:", json.dumps(signals))
    print("buttons:")
    for b in buttons:
        print("  ", json.dumps(b, ensure_ascii=False))


async def main(profile: str) -> None:
    profile_dir = _resolve_profile_dir(profile)
    transport = UiAutomationTransport()
    await transport.setup(profile_dir)
    page = transport._page  # noqa: SLF001 — investigation script
    if page is None:
        raise SystemExit("no page after setup")

    await transport._enter_editor(page)  # noqa: SLF001 — creates a scratch project
    await page.wait_for_timeout(2500)
    await _dump(page, "BEFORE (classic composer)")

    # Click the known agent toggle, then re-dump to locate the expand button.
    toggle = page.locator(COMPOSER_AGENT_TOGGLE_SELECTOR).first
    if await toggle.count() > 0:
        await toggle.click(force=True)
        await page.wait_for_timeout(1500)
        await _dump(page, "AFTER agent-toggle click")
    else:
        print("\n[!] agent toggle selector matched 0 elements — inspect BEFORE dump.")

    print("\nNote which button carries the expand/sidebar action in the AFTER dump,")
    print("then click it and confirm signals.tune / signals.slate flip to true.")
    await page.wait_for_timeout(2000)
    await transport.teardown()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="denon82")
    args = ap.parse_args()
    asyncio.run(main(args.profile))
